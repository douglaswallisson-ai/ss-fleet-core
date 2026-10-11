"""
Comparativo com iguais: o cliente contra os outros clientes do MESMO segmento
(urbano, fretamento ou carga), nos últimos 30 dias. Pedido do PM em 10/10/2026.

Regras:
- Segmento: a mesma regra de `cliente.py` (linha urbana rodando = urbano;
  5 ou mais ônibus sem linha = fretamento; o resto = carga), com o ajuste
  manual da SS quando existir.
- Entra como igual quem rodou pelo menos `KM_MIN` km com `VEICULOS_MIN` veículos.
- Cada indicador só compara quem MEDE aquele dado:
  - km/l: o combustível válido cobre pelo menos metade dos km, e o resultado fica
    entre 0,8 e 8 km/l;
  - eventos (aceleração + freada) e excesso de velocidade NÃO são comparados entre
    clientes: cada cliente tem o equipamento configurado com limites próprios. Em
    10/10/2026, com o mesmo rastreador (VIRLOC 8), a CECOTI contou 9,2 acelerações e
    freadas a cada 100 km, a RCA 0,06 e a Raja e a Figueiredo 0. A tela mostra só o
    valor do próprio cliente (zero ou mais de 0,5 por km = o equipamento não mede).
- Máquinas: cliente com 30% ou mais de máquinas (categorias 5, 6, 8, 23) só se compara
  com outros assim (a Unibase, com 26 máquinas em 57, entrava como transportadora).
- Os outros clientes aparecem sem nome ("Cliente A", "Cliente B"…). Só a SS vê
  os nomes.

Só leitura (con_driver_h_km, con_telemetry_day, con_telemetry, tracked_unit).
"""

import statistics
import string
import time
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.api.v1.endpoints import cliente
from app.core import combustivel
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

KM_MIN = 20_000
VEICULOS_MIN = 5
IGUAIS_MIN = 3
KML_FAIXA = (0.8, 8.0)
COBERTURA_COMBUSTIVEL_MIN = 0.5
EVENTOS_POR_KM_MAX = 0.5
CACHE_S = 6 * 3600
_CACHE: dict[tuple, tuple[float, dict]] = {}

NOME_SEGMENTO = {"urbano": "Transporte urbano", "fretamento": "Fretamento", "carga": "Carga", "maquinas": "Máquinas e equipamentos"}
CATEGORIAS_MAQUINA = (5, 6, 8, 23)
MAQUINAS_FRACAO = 0.3
#: Dependem da configuração do equipamento de cada cliente (ver docstring).
NAO_COMPARAVEIS = {"eventos100", "velocidade100"}

INDICADORES = [
    # chave, nome, unidade, menor é melhor, explicação
    ("kml", "Consumo", "km/l", False, "Km rodados com combustível válido ÷ litros."),
    ("eventos100", "Acelerações e freadas bruscas", "a cada 100 km", True,
     "Acelerações + freadas bruscas a cada 100 km rodados."),
    ("velocidade100", "Excesso de velocidade", "a cada 100 km", True,
     "Excessos de velocidade a cada 100 km rodados."),
    ("ocioso_pct", "Parado com motor ligado", "% do tempo", True,
     "Tempo parado com motor ligado ÷ tempo total com motor ligado."),
    ("sem_motorista_pct", "Rodando sem motorista identificado", "% dos km", True,
     "Km rodados sem motorista identificado ÷ km rodados."),
    ("km_veiculo_dia", "Uso da frota", "km por veículo por dia", False,
     "Km rodados ÷ veículos ÷ dias do período."),
]

SQL_KM = f"""
SELECT h.group_id, count(DISTINCT h.unit_id) AS veiculos,
       sum(h.distance_traveled_hist) / 1000.0 AS km,
       sum({combustivel.km_com_combustivel_m()}) / 1000.0 AS km_comb,
       sum({combustivel.litros_ml()}) / 1000.0 AS litros,
       sum(coalesce(h.count_acel_excess, 0) + coalesce(h.count_break_excess, 0)) AS eventos,
       sum(coalesce(h.count_speed_excess, 0) + coalesce(h.count_speed_excess_dry_l1, 0) + coalesce(h.count_speed_excess_dry_l2, 0)
           + coalesce(h.count_speed_excess_dry_l3, 0) + coalesce(h.count_speed_excess_wet_l1, 0)
           + coalesce(h.count_speed_excess_wet_l2, 0) + coalesce(h.count_speed_excess_wet_l3, 0)) AS velocidade,
       sum(CASE WHEN coalesce(h.driver_id, 0) = 0 THEN h.distance_traveled_hist ELSE 0 END) / 1000.0 AS km_sem
FROM mova.con_driver_h_km h
WHERE h.dt >= :ini AND h.dt <= :fim AND h.group_id IS NOT NULL
GROUP BY h.group_id
"""

SQL_OCIOSO = """
SELECT td.group_id,
       sum(coalesce(td.time_stop_engine_on, 0) + coalesce(td.time_stop_engine_on_productive, 0)) AS parado,
       sum(coalesce(td.time_green, 0) + coalesce(td.time_extra_eco, 0) + coalesce(td.time_inercia, 0) + coalesce(td.time_eco_roll, 0)
           + coalesce(td.time_low_speed, 0) + coalesce(td.time_yellow, 0) + coalesce(td.time_red, 0) + coalesce(td.time_blue, 0)
           + coalesce(td.time_banguela, 0) + coalesce(td.time_stop_accel, 0) + coalesce(td.time_stop_engine_on, 0)
           + coalesce(td.time_stop_engine_on_productive, 0) + coalesce(td.time_tolerancia, 0)) AS total
FROM mova.con_telemetry_day td
WHERE td.day >= :ini AND td.day <= :fim AND td.group_id IS NOT NULL
GROUP BY td.group_id
"""

SQL_SEGMENTO = """
SELECT g.id AS group_id, g.name,
       (SELECT count(*) FROM mova.tracked_unit t WHERE t.group_id = g.id AND t.status = 1 AND t.unit_category_id = ANY(:cats)) AS onibus,
       (SELECT count(*) FROM mova.tracked_unit t WHERE t.group_id = g.id AND t.status = 1 AND t.unit_category_id = ANY(:maq)) AS maquinas,
       (SELECT count(*) FROM mova.tracked_unit t WHERE t.group_id = g.id AND t.status = 1) AS ativos,
       g.id IN (SELECT DISTINCT tu.group_id FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
                WHERE ct.start_time > :desde_linha AND ct.line_number > 0) AS urbano
FROM mova."group" g WHERE g.id = ANY(:grupos)
"""


def segmento(urbano: bool, onibus: int, ajuste: Optional[dict] = None, maquinas: int = 0, ativos: int = 0) -> str:
    """Segmento principal do cliente (o ajuste manual da SS vale mais que a regra)."""
    if not (ajuste and (ajuste.get("urbano") or ajuste.get("fretamento"))) and ativos and maquinas / ativos >= MAQUINAS_FRACAO:
        return "maquinas"
    if ajuste and ajuste.get("urbano") is not None:
        urbano = bool(ajuste["urbano"])
    fretamento = (onibus or 0) >= 5 and not urbano
    if ajuste and ajuste.get("fretamento") is not None:
        fretamento = bool(ajuste["fretamento"])
    return "urbano" if urbano else "fretamento" if fretamento else "carga"


def indicadores_do_cliente(k: dict, ocioso: Optional[dict], dias: int) -> dict:
    """Valores de um cliente; None quando ele não mede aquele dado."""
    km = float(k["km"] or 0)
    out: dict[str, Optional[float]] = {c[0]: None for c in INDICADORES}
    if km <= 0:
        return out
    litros, km_comb = float(k["litros"] or 0), float(k["km_comb"] or 0)
    if litros > 0 and km_comb / km >= COBERTURA_COMBUSTIVEL_MIN:
        kml = km_comb / litros
        if KML_FAIXA[0] <= kml <= KML_FAIXA[1]:
            out["kml"] = round(kml, 2)
    for chave, campo in (("eventos100", "eventos"), ("velocidade100", "velocidade")):
        n = float(k[campo] or 0)
        if 0 < n / km <= EVENTOS_POR_KM_MAX:
            out[chave] = round(n / km * 100, 2)
    if ocioso and float(ocioso["total"] or 0) > 0:
        out["ocioso_pct"] = round(float(ocioso["parado"] or 0) / float(ocioso["total"]) * 100, 1)
    out["sem_motorista_pct"] = round(float(k["km_sem"] or 0) / km * 100, 1)
    if k["veiculos"]:
        out["km_veiculo_dia"] = round(km / int(k["veiculos"]) / dias, 1)
    return out


def _quartil(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    p = (len(xs) - 1) * q
    i = int(p)
    return xs[i] + (xs[min(i + 1, len(xs) - 1)] - xs[i]) * (p - i)


def comparar(meu: Optional[float], iguais: list[tuple[str, float]], menor_melhor: bool) -> dict:
    """Posição do cliente entre os iguais (os iguais NÃO incluem o próprio cliente)."""
    vals = [v for _, v in iguais]
    if meu is None:
        return {"situacao": "sem_dado", "motivo": "O equipamento deste cliente não mede este dado."}
    if len(vals) < IGUAIS_MIN:
        return {"situacao": "poucos_iguais", "motivo": f"Só {len(vals)} {'cliente igual mede' if len(vals) == 1 else 'clientes iguais medem'} este dado; a comparação precisa de {IGUAIS_MIN}."}
    todos = sorted(vals + [meu], reverse=not menor_melhor)
    posicao = todos.index(meu) + 1
    piores = sum(1 for v in vals if (v > meu if menor_melhor else v < meu))
    p25, p75 = _quartil(vals, 0.25), _quartil(vals, 0.75)
    bom, ruim = (p25, p75) if menor_melhor else (p75, p25)
    melhor = min(vals) if menor_melhor else max(vals)
    if (meu <= bom) if menor_melhor else (meu >= bom):
        situacao = "melhor"
    elif (meu >= ruim) if menor_melhor else (meu <= ruim):
        situacao = "pior"
    else:
        situacao = "media"
    return {
        "situacao": situacao, "posicao": posicao, "de": len(todos),
        "melhor_que_pct": round(piores / len(vals) * 100),
        "mediana": round(statistics.median(vals), 2), "p25": round(p25, 2), "p75": round(p75, 2),
        "melhor": round(melhor, 2),
    }


def _rotulos(n: int) -> list[str]:
    letras = string.ascii_uppercase
    return [f"Cliente {letras[i % 26]}{'' if i < 26 else i // 26}" for i in range(n)]


async def _base(ini: date, fim: date) -> dict:
    chave = (ini, fim)
    hit = _CACHE.get(chave)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    p = {"ini": ini, "fim": fim}
    async with AsyncSessionLocalReplica() as db:
        kms = {r["group_id"]: dict(r) for r in (await db.execute(text(SQL_KM), p)).mappings()}
        ocs = {r["group_id"]: dict(r) for r in (await db.execute(text(SQL_OCIOSO), p)).mappings()}
        elegiveis = [g for g, k in kms.items() if float(k["km"] or 0) >= KM_MIN and int(k["veiculos"] or 0) >= VEICULOS_MIN]
        segs = {r["group_id"]: dict(r) for r in (await db.execute(text(SQL_SEGMENTO), {
            "grupos": elegiveis, "cats": list(cliente.CATEGORIAS_ONIBUS), "maq": list(CATEGORIAS_MAQUINA),
            # Linhas no mesmo período do comparativo (a JTP rodou 34 linhas em 30 dias e nenhuma nos últimos 15).
            "desde_linha": ini})).mappings()}
    d = {"kms": kms, "ocs": ocs, "elegiveis": elegiveis, "segs": segs}
    _CACHE[chave] = (time.time(), d)
    return d


def _ajustes() -> dict[int, dict]:
    import json
    with cliente._trava, cliente._con() as c:
        return {r["group_id"]: json.loads(r["dados"]) for r in c.execute("SELECT group_id, dados FROM modulos")}


@router.get("")
async def comparativo(group_id: int = Query(...), dias: int = Query(30, ge=7, le=90),
                      user=Depends(require_permission("reports", "read"))):
    cliente._grupo_ok(user, group_id)
    fim = date.today() - timedelta(days=1)
    ini = fim - timedelta(days=dias - 1)
    b = await _base(ini, fim)
    ajustes = _ajustes()
    if group_id not in b["kms"] or group_id not in b["elegiveis"]:
        k = b["kms"].get(group_id)
        raise HTTPException(404, f"Este cliente rodou pouco no período ({round(float(k['km'] or 0)) if k else 0} km): "
                                 f"o comparativo precisa de {KM_MIN:,} km com {VEICULOS_MIN} veículos.".replace(",", "."))

    def seg_de(g: int) -> str:
        s = b["segs"].get(g) or {}
        return segmento(bool(s.get("urbano")), int(s.get("onibus") or 0), ajustes.get(g), int(s.get("maquinas") or 0), int(s.get("ativos") or 0))

    meu_seg = seg_de(group_id)
    iguais_ids = sorted((g for g in b["elegiveis"] if g != group_id and seg_de(g) == meu_seg),
                        key=lambda g: -float(b["kms"][g]["km"] or 0))
    ss = bool(getattr(user, "is_super_admin", False))
    rotulo = dict(zip(iguais_ids, _rotulos(len(iguais_ids))))
    nome = {g: ((b["segs"].get(g) or {}).get("name") if ss else rotulo[g]) for g in iguais_ids}
    vals = {g: indicadores_do_cliente(b["kms"][g], b["ocs"].get(g), dias) for g in iguais_ids}
    meus = indicadores_do_cliente(b["kms"][group_id], b["ocs"].get(group_id), dias)

    saida = []
    for chave, nome_ind, unidade, menor, explica in INDICADORES:
        iguais = [(nome[g], vals[g][chave]) for g in iguais_ids if vals[g][chave] is not None]
        if chave in NAO_COMPARAVEIS:
            r = {"situacao": "nao_comparavel", "motivo": "Cada cliente tem o equipamento configurado com limites próprios: "
                 "este número serve para acompanhar a própria frota, não para comparar com outros."}
            iguais = []
        else:
            r = comparar(meus[chave], iguais, menor)
        saida.append({"chave": chave, "nome": nome_ind, "unidade": unidade, "menor_melhor": menor, "explicacao": explica,
                      "valor": meus[chave], "comparavel": chave not in NAO_COMPARAVEIS, **r,
                      "iguais": sorted(({"nome": n, "valor": v} for n, v in iguais), key=lambda x: x["valor"])})
    k = b["kms"][group_id]
    return {
        "group_id": group_id, "segmento": meu_seg, "segmento_nome": NOME_SEGMENTO[meu_seg],
        "inicio": ini.isoformat(), "fim": fim.isoformat(), "dias": dias,
        "iguais": len(iguais_ids), "nomes_visiveis": ss,
        "base": {"veiculos": int(k["veiculos"] or 0), "km": round(float(k["km"] or 0))},
        "indicadores": saida,
        "regras": {"km_min": KM_MIN, "veiculos_min": VEICULOS_MIN, "iguais_min": IGUAIS_MIN},
    }
