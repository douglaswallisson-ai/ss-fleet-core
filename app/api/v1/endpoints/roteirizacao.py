"""
Roteirização: a partir dos pontos de uma viagem (da Escala de Viagem ou
digitados), estima distância, tempo com as pausas da Lei do Motorista,
combustível, custo e pedágio. A Escala de Viagem chama esta conta; as duas
ferramentas continuam separadas (decisão do PM, 04/10/2026).

Fontes:
- km/L: média real do veículo nos últimos 90 dias (`con_driver_h_km`, com a
  mesma regra de plausibilidade do resto da plataforma). Sem histórico, usa a
  média do tipo de veículo do cliente;
- preço do diesel/gasolina: média dos estados no levantamento ANP mais recente
  (`mova.precos_combustivel`);
- pausas: CTB art. 67-C (30 min a cada 5h30 na carga e a cada 4 h com
  passageiros) e CLT 235-C (11 h de descanso quando a direção passa de 10 h).

Rota e pedágio:
- com um motor de rotas configurado (app/core/roteador.py: Amazon Location,
  HERE, Valhalla próprio ou OSRM), a distância e o tempo vêm da malha viária
  real e o caminho desvia das áreas de risco do cliente (app/core/areas_risco.py;
  o OSRM não desvia). Em qualquer caso a resposta diz quais áreas de risco o
  caminho atravessa;
- sem ele, a distância é a linha reta × FATOR_ESTRADA e o tempo usa a
  velocidade média do tipo de operação. SUPOSIÇÃO: fator 1,3 e 60 km/h
  (carga) / 65 km/h (passageiros); comparar com viagens reais;
- pedágio: praças da ANTT (rodovias federais concedidas) perto do trajeto, se
  o arquivo `data/pracas_pedagio.csv` (dados abertos da ANTT) estiver na pasta.
  A tarifa por eixo não é dado aberto: fica `tarifa_eixo` informada pelo
  usuário. Recomendação para valor exato: Amazon Location Service (rotas de
  caminhão com pedágio por eixo) ou QualP.
"""

import csv
import re
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import areas_risco, combustivel, geo, roteador
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

FATOR_ESTRADA = 1.3
VELOCIDADE = {"carga": 60.0, "passageiros": 65.0}
PAUSA_A_CADA_MIN = {"carga": 330, "passageiros": 240}
PAUSA_MIN = 30
DIRECAO_MAX_DIA_MIN = 600  # 8 h + 2 h extras
DESCANSO_MIN = 11 * 60
KML_PADRAO = {"carga": 2.5, "passageiros": 3.0}  # SUPOSIÇÃO, só sem histórico
PRACAS = Path(__file__).resolve().parents[4] / "data" / "pracas_pedagio.csv"
RAIO_PRACA_M = 1500
# Sem a malha viária, o trajeto é a linha reta entre os pontos e a estrada real
# se afasta dela: 15 km pegou as 8 praças da Fernão Dias de Betim a São Paulo.
RAIO_PRACA_RETA_M = 15_000
# Veículo comercial paga a tarifa básica da praça × número de eixos (regra ANTT;
# ex.: básica R$ 4,00 → 6 eixos R$ 24,00). A tarifa de cada praça não é dado
# aberto. SUPOSIÇÃO: R$ 8,00 por eixo, valor do meio da faixa das concessões
# federais; o usuário troca na tela. Aprovado como estimativa pelo PM (04/10/2026).
TARIFA_EIXO_PADRAO = 8.0
_PRACAS_CACHE: Optional[list[dict]] = None


class PontoRota(BaseModel):
    nome: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    parada_min: int = Field(0, ge=0, le=24 * 60)


class Pedido(BaseModel):
    group_id: int
    unit_id: Optional[int] = None
    operacao: Optional[str] = Field(None, pattern="^(carga|passageiros)$")
    pontos: list[PontoRota] = Field(min_length=2, max_length=50)
    saida: Optional[datetime] = None
    eixos: int = Field(3, ge=2, le=9)
    tarifa_eixo: Optional[float] = Field(None, ge=0)
    kml: Optional[float] = Field(None, gt=0, le=30)
    preco_litro: Optional[float] = Field(None, gt=0, le=20)
    # Rota cadastrada (fretamento): traçado real [[lat, lng], ...] e o id em
    # mova.route, para o tempo vir das viagens que já rodaram nela.
    trajeto: Optional[list[list[float]]] = Field(None, max_length=20_000)
    rota_id: Optional[int] = None


# Ponto a mais de 1,5 km do traçado não está nele: aquele trecho volta à estimativa.
DIST_TRAJETO_M = 1500


def _km_trajeto(tr: list[list[float]], i: int, j: int) -> float:
    p = math.pi / 180
    s = 0.0
    for (a1, o1), (a2, o2) in zip(tr[i:j], tr[i + 1:j + 1]):
        h = math.sin((a2 - a1) * p / 2) ** 2 + math.cos(a1 * p) * math.cos(a2 * p) * math.sin((o2 - o1) * p / 2) ** 2
        s += 12_742_000 * math.asin(math.sqrt(h))
    return s / 1000


def _indice_no_trajeto(tr: list[list[float]], lat: float, lon: float, desde: int) -> Optional[int]:
    """Vértice do traçado mais perto do ponto, sem voltar atrás no sentido da rota."""
    melhor, dmin = None, float("inf")
    kx = 111_320.0 * math.cos(math.radians(lat))
    for k in range(desde, len(tr)):
        d = math.hypot((tr[k][0] - lat) * 111_320.0, (tr[k][1] - lon) * kx)
        if d < dmin:
            melhor, dmin = k, d
    return melhor if dmin <= DIST_TRAJETO_M else None


async def _tempo_real_rota(rota_id: int) -> Optional[tuple[float, int]]:
    """Mediana da duração (min) das viagens que rodaram nesta rota nos últimos 90 dias."""
    r = await _ler("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM local_time_end - local_time_ini) / 60) AS med,
                             count(*) AS n
                      FROM mova.con_status_buss_line
                      WHERE buss_line_shift_route_id = :r AND local_time_ini >= now() - interval '90 days'
                        AND local_time_end > local_time_ini AND local_time_end - local_time_ini BETWEEN interval '5 minutes' AND interval '6 hours'""",
                   {"r": rota_id})
    if r and r[0]["n"] and r[0]["n"] >= 3 and r[0]["med"]:
        return float(r[0]["med"]), int(r[0]["n"])
    return None


async def _vel_cliente(group_id: int) -> Optional[float]:
    """Velocidade mediana (km/h, com as paradas de embarque) das rotas do cliente
    que têm viagens: serve para a rota que ainda não rodou. VTR em 04/10/2026:
    15 km/h em 209 rotas."""
    r = await _ler("""WITH m AS (
            SELECT c.buss_line_shift_route_id AS rid,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM c.local_time_end - c.local_time_ini) / 60) AS med, count(*) AS n
            FROM mova.con_status_buss_line c
            WHERE c.group_id = :g AND c.local_time_ini >= now() - interval '90 days'
              AND c.local_time_end - c.local_time_ini BETWEEN interval '5 minutes' AND interval '6 hours'
            GROUP BY 1)
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (ST_Length(r.route::geography) / 1000) / (m.med / 60)) AS vel, count(*) AS n
        FROM m JOIN mova.route r ON r.id = m.rid WHERE m.n >= 3 AND m.med > 0""", {"g": group_id})
    if r and r[0]["n"] and r[0]["n"] >= 5 and r[0]["vel"] and 3 < float(r[0]["vel"]) < 120:
        return float(r[0]["vel"])
    return None


def _dist_m(a: PontoRota, b: PontoRota) -> float:
    p = math.pi / 180
    h = (math.sin((b.latitude - a.latitude) * p / 2) ** 2
         + math.cos(a.latitude * p) * math.cos(b.latitude * p) * math.sin((b.longitude - a.longitude) * p / 2) ** 2)
    return 12_742_000 * math.asin(math.sqrt(h))


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


def _pracas() -> list[dict]:
    global _PRACAS_CACHE
    if _PRACAS_CACHE is not None:
        return _PRACAS_CACHE
    out = []
    if PRACAS.exists():
        # O arquivo da ANTT vem em Latin-1.
        with PRACAS.open(encoding="latin-1") as f:
            for r in csv.DictReader(f, delimiter=";"):
                try:
                    if (r.get("situacao") or "").lower().startswith("inativ"):
                        continue
                    # Free Flow cobra por pórtico de entrada/saída, não como praça: fica de fora da estimativa.
                    if (r.get("praca_de_pedagio") or "").lower().startswith("free flow"):
                        continue
                    out.append({"nome": r.get("praca_de_pedagio") or r.get("praca"), "rodovia": r.get("rodovia"),
                                "uf": r.get("uf"), "lat": float(str(r["latitude"]).replace(",", ".")),
                                "lon": float(str(r["longitude"]).replace(",", "."))})
                except (KeyError, ValueError):
                    continue
    # Uma praça aparece em mais de uma linha (pista principal e marginal, sentidos).
    vistos, unicas = set(), []
    for x in out:
        base = re.sub(r"\b(norte|sul|leste|oeste|defasada|crescente|decrescente)\b|[-–]", "", (x["nome"] or "").lower())
        k = (re.sub(r"\s+", " ", base).strip(), x["rodovia"])
        if k not in vistos:
            vistos.add(k)
            unicas.append(x)
    _PRACAS_CACHE = unicas
    return unicas


def _dist_ponto_segmento_m(lat, lon, a, b) -> float:
    """Distância aproximada (projeção plana local) de um ponto a um segmento."""
    k = 111_320.0
    ax, ay = a[1] * k * math.cos(math.radians(lat)), a[0] * k
    bx, by = b[1] * k * math.cos(math.radians(lat)), b[0] * k
    px, py = lon * k * math.cos(math.radians(lat)), lat * k
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


async def _consumo(g: int, unit_id: Optional[int], operacao: str) -> tuple[float, str]:
    if unit_id:
        r = await _ler(f"""SELECT SUM({combustivel.km_com_combustivel_m()}) / NULLIF(SUM({combustivel.litros_ml()}), 0) AS kml
                           FROM mova.con_driver_h_km h WHERE h.unit_id = :u AND h.dt >= now() - interval '90 days'""", {"u": unit_id})
        if r and r[0]["kml"] and 0.5 < float(r[0]["kml"]) < 30:
            return round(float(r[0]["kml"]), 2), "média real do veículo nos últimos 90 dias"
    r = await _ler(f"""SELECT SUM({combustivel.km_com_combustivel_m()}) / NULLIF(SUM({combustivel.litros_ml()}), 0) AS kml
                       FROM mova.con_driver_h_km h JOIN mova.tracked_unit tu ON tu.id = h.unit_id
                       WHERE tu.group_id = :g AND h.dt >= now() - interval '90 days'""", {"g": g})
    if r and r[0]["kml"] and 0.5 < float(r[0]["kml"]) < 30:
        return round(float(r[0]["kml"]), 2), "média da frota do cliente nos últimos 90 dias"
    return KML_PADRAO[operacao], "SUPOSIÇÃO: média típica do tipo de operação (sem histórico)"


async def _preco(diesel: bool) -> tuple[Optional[float], Optional[str]]:
    r = await _ler("""SELECT data_referencia, round(avg(preco), 3) AS preco FROM mova.precos_combustivel
                      WHERE combustivel_id = :c AND data_referencia = (SELECT max(data_referencia) FROM mova.precos_combustivel WHERE combustivel_id = :c)
                      GROUP BY data_referencia""", {"c": 2 if diesel else 1})
    if not r:
        return None, None
    return float(r[0]["preco"]), f"ANP, média dos estados em {r[0]['data_referencia'].strftime('%d/%m/%Y')}"


async def _operacao(unit_id: Optional[int]) -> str:
    if unit_id:
        r = await _ler("SELECT unit_category_id FROM mova.tracked_unit WHERE id = :u", {"u": unit_id})
        if r and r[0]["unit_category_id"] in (12, 22):
            return "passageiros"
    return "carga"


@router.post("/calcular")
async def calcular(p: Pedido, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    op = p.operacao or await _operacao(p.unit_id)
    traj = [x for x in (p.trajeto or []) if len(x) == 2] or None
    areas = await areas_risco.areas([p.group_id])
    real = None
    if not traj:
        real = await roteador.calcular([[x.latitude, x.longitude] for x in p.pontos], areas,
                                       "onibus" if op == "passageiros" else "caminhao")
    erro_motor = real.get("erro") if real and real.get("erro") else None
    if erro_motor:
        real = None
    # Traçado da rota cadastrada: cada ponto é localizado nele e o trecho mede
    # o caminho real entre os dois. O tempo vem da mediana das viagens da rota.
    idx: list[Optional[int]] = []
    vel_trajeto, fonte_tempo = None, None
    if traj:
        ultimo = 0
        for x in p.pontos:
            k = _indice_no_trajeto(traj, x.latitude, x.longitude, ultimo)
            idx.append(k)
            if k is not None:
                ultimo = k
        km_tr = _km_trajeto(traj, 0, len(traj) - 1)
        med = await _tempo_real_rota(p.rota_id) if p.rota_id else None
        if med and km_tr > 0:
            vel_trajeto = km_tr / (med[0] / 60)
            fonte_tempo = f"mediana de {med[1]} viagens reais nesta rota ({round(med[0])} min)"
        else:
            vc = await _vel_cliente(p.group_id)
            if vc:
                vel_trajeto = vc
                fonte_tempo = f"velocidade mediana das rotas do cliente ({round(vc)} km/h, com paradas), pois esta rota ainda não tem viagens"
    trechos = []
    usou_trajeto = False
    for i in range(len(p.pontos) - 1):
        a, b = p.pontos[i], p.pontos[i + 1]
        if real:
            km, mn = real["trechos"][i]["km"], real["trechos"][i]["min"]
        elif traj and idx[i] is not None and idx[i + 1] is not None and idx[i + 1] > idx[i]:
            km = _km_trajeto(traj, idx[i], idx[i + 1])
            mn = km / (vel_trajeto or VELOCIDADE[op]) * 60
            usou_trajeto = True
        else:
            km = _dist_m(a, b) / 1000 * FATOR_ESTRADA
            mn = km / VELOCIDADE[op] * 60
        trechos.append({"de": a.nome, "para": b.nome, "km": round(km, 1), "conducao_min": round(mn)})

    # Linha do tempo com paradas planejadas e as pausas obrigatórias.
    saida = p.saida or datetime.now().replace(second=0, microsecond=0)
    t = saida
    dirigindo_desde_pausa = dirigindo_no_dia = dirigido_total = 0.0
    pausas = []
    agenda = [{"ponto": p.pontos[0].nome, "chegada": None, "saida": saida.isoformat(timespec="minutes")}]
    for i, tr in enumerate(trechos):
        resto = float(tr["conducao_min"])
        while resto > 0:
            ate_pausa = PAUSA_A_CADA_MIN[op] - dirigindo_desde_pausa
            ate_descanso = DIRECAO_MAX_DIA_MIN - dirigindo_no_dia
            passo = min(resto, ate_pausa, ate_descanso)
            t += timedelta(minutes=passo)
            resto -= passo
            dirigindo_desde_pausa += passo
            dirigindo_no_dia += passo
            dirigido_total += passo
            if resto > 0 and dirigindo_no_dia >= DIRECAO_MAX_DIA_MIN:
                pausas.append({"tipo": "descanso", "min": DESCANSO_MIN, "quando": t.isoformat(timespec="minutes"), "dirigido_min": round(dirigido_total),
                               "trecho": f"{tr['de']} → {tr['para']}", "regra": "CLT 235-C: 11 h de descanso"})
                t += timedelta(minutes=DESCANSO_MIN)
                dirigindo_desde_pausa = dirigindo_no_dia = 0
            elif resto > 0 and dirigindo_desde_pausa >= PAUSA_A_CADA_MIN[op]:
                pausas.append({"tipo": "pausa", "min": PAUSA_MIN, "quando": t.isoformat(timespec="minutes"), "dirigido_min": round(dirigido_total),
                               "trecho": f"{tr['de']} → {tr['para']}", "regra": f"CTB 67-C: 30 min a cada {PAUSA_A_CADA_MIN[op] // 60}h{PAUSA_A_CADA_MIN[op] % 60:02d} de direção".replace("h00", " h")})
                t += timedelta(minutes=PAUSA_MIN)
                dirigindo_desde_pausa = 0
        destino = p.pontos[i + 1]
        chegada = t
        t += timedelta(minutes=destino.parada_min)
        if destino.parada_min >= PAUSA_MIN:
            dirigindo_desde_pausa = 0
        if destino.parada_min >= DESCANSO_MIN:
            dirigindo_no_dia = 0
        agenda.append({"ponto": destino.nome, "chegada": chegada.isoformat(timespec="minutes"),
                       "saida": t.isoformat(timespec="minutes") if i < len(trechos) - 1 else None})

    km_total = sum(x["km"] for x in trechos)
    conducao = sum(x["conducao_min"] for x in trechos)
    kml, fonte_kml = (p.kml, "informado") if p.kml else await _consumo(p.group_id, p.unit_id, op)
    preco, fonte_preco = (p.preco_litro, "informado") if p.preco_litro else await _preco(diesel=True)
    litros = km_total / kml if kml else None

    # Pedágio: praças da ANTT a até 1,5 km de algum trecho.
    linhas = real["geometria"] if real else traj if usou_trajeto else [[x.latitude, x.longitude] for x in p.pontos]
    caminho_real = bool(real or usou_trajeto)
    achadas = []
    for pr in _pracas():
        for a, b in zip(linhas, linhas[1:]):
            if _dist_ponto_segmento_m(pr["lat"], pr["lon"], a, b) <= (RAIO_PRACA_M if caminho_real else RAIO_PRACA_RETA_M):
                achadas.append(pr)
                break
    tarifa = p.tarifa_eixo if p.tarifa_eixo is not None else TARIFA_EIXO_PADRAO

    # Áreas de risco perto do caminho e as que ele atravessa.
    risco = []
    for a in areas:
        d = min(geo.dist_area_linha_m(anel, geo.simplificar(linhas, 800)) for anel in a["aneis"])
        if d <= 5000:
            risco.append({"nome": a["nome"], "nivel": a["nivel"], "origem": a["origem"], "motivo": a.get("motivo"),
                          "dist_m": round(d), "na_rota": d == 0,
                          "poligonos": [[[q[1], q[0]] for q in anel] for anel in a["aneis"]]})
    risco.sort(key=lambda x: x["dist_m"])
    na_rota = [x for x in risco if x["na_rota"] and x["nivel"] == "evitar"]
    motor = roteador.NOME_MOTOR.get(real["motor"]) if real else None
    pedagio_valor = round(len(achadas) * p.eixos * tarifa, 2) if achadas else (0.0 if _pracas() else None)

    return {
        "operacao": op,
        "fonte_rota": (f"malha viária ({motor})" + (f", desviando de {real['areas_enviadas']} área(s) de risco" if real.get("areas_enviadas") else "")) if real else ("traçado gravado da rota" + (f"; tempo pela {fonte_tempo}" if fonte_tempo else "; tempo pela velocidade média"))
                      if usou_trajeto else "estimativa: linha reta × 1,3 e velocidade média",
        "trechos": trechos, "km_total": round(km_total, 1), "conducao_min": conducao,
        "pausas": pausas, "pausas_min": sum(x["min"] for x in pausas),
        "paradas_min": sum(x.parada_min for x in p.pontos[1:-1]),
        "saida": saida.isoformat(timespec="minutes"), "chegada": agenda[-1]["chegada"], "agenda": agenda,
        "duracao_total_min": round((datetime.fromisoformat(agenda[-1]["chegada"]) - saida).total_seconds() / 60),
        "combustivel": {"kml": kml, "fonte_kml": fonte_kml, "litros": round(litros, 1) if litros else None,
                        "preco_litro": preco, "fonte_preco": fonte_preco,
                        "custo": round(litros * preco, 2) if litros and preco else None},
        "pedagio": {"pracas": [{"nome": x["nome"], "rodovia": x["rodovia"], "uf": x["uf"]} for x in achadas],
                    "base_disponivel": bool(_pracas()), "eixos": p.eixos, "tarifa_eixo": tarifa,
                    "tarifa_estimada": p.tarifa_eixo is None, "valor": pedagio_valor,
                    "observacao": None if _pracas() else "Base de praças da ANTT ainda não carregada; pedágio não estimado."},
        "geometria": real["geometria"] if real else traj if usou_trajeto else None,
        "motor": {"nome": motor, "configurado": roteador.NOME_MOTOR.get(roteador.motor_configurado()),
                  "desvia_areas": bool(real and real.get("desvia_areas")), "erro": erro_motor},
        "areas_risco": risco[:100],
        "areas_na_rota": [{"nome": x["nome"], "motivo": x["motivo"]} for x in na_rota],
        "aviso_risco": (None if not na_rota else
                        f"O caminho passa por {len(na_rota)} área(s) de risco" + (
                            ": o motor de rotas não encontrou caminho que desvie." if real and real.get("desvia_areas") else
                            ". Sem motor de rotas ligado, a plataforma não consegue desviar; ajuste os pontos ou peça ao TI para ligar o motor.")),
    }


# ------------------------------------------------------------------ rotograma
# O rotograma (pedido do CEO, 06/10/2026) é o documento da rota que o motorista
# leva: o que ele vai encontrar trecho a trecho. Sai na tela e em PDF, para um
# cliente ou para todos; sem assinatura (PM, 06/10/2026).

#: Eventos de condução que viram "ponto crítico" quando a frota repete no mesmo lugar.
EVENTOS_CRITICOS = {7: "Excesso de velocidade", 37: "Excesso de velocidade na chuva", 9: "Freada brusca",
                    153: "Aceleração brusca", 359: "Curva brusca"}
CRITICO_RAIO_M = 150         # evento a até 150 m do caminho
CRITICO_GRADE = 0.003        # agrupa em quadrados de ~300 m
CRITICO_MIN_EVENTOS = 3      # um evento solto não é ponto crítico
CRITICO_DIAS = 30
APOIO_RAIO_M = 2000
AREA_RAIO_M = 2000
CATEGORIAS_APOIO = {5: "Empresa", 6: "Estacionamento", 7: "Posto de serviço"}
EVENTOS_POR_KM_SUSPEITO = 0.5  # o mesmo de manutencao_risco.py


def _wkt(linha: list) -> str:
    return "LINESTRING(" + ",".join(f"{q[1]:.6f} {q[0]:.6f}" for q in linha) + ")"


@router.post("/rotograma")
async def rotograma(p: Pedido, user=Depends(require_permission("reports", "read"))):
    r = await calcular(p, user)
    linha = r["geometria"] or [[x.latitude, x.longitude] for x in p.pontos]
    curta = geo.simplificar(linha, 500)
    acum = geo.km_ate_vertice(curta)
    escala = (r["km_total"] / acum[-1]) if acum[-1] else 1.0  # km da rota (estimada ou real) por km da linha
    km = lambda la, ln: round(geo.km_na_linha(la, ln, curta, acum) * escala, 1)  # noqa: E731
    estimada = not r["geometria"]
    itens: list[dict] = []

    for a in r["areas_risco"]:
        if a["dist_m"] > AREA_RAIO_M:
            continue
        pts = [q for pol in a["poligonos"] for q in pol]
        c = min(pts, key=lambda q: geo.dist_ponto_linha_m(q[0], q[1], curta))
        itens.append({"tipo": "area_risco" if a["nivel"] == "evitar" else "atencao", "km": km(c[0], c[1]), "lat": c[0], "lng": c[1],
                      "titulo": ("Atravessa área de risco: " if a["na_rota"] else "Área de risco a %s: " % (f"{a['dist_m']} m" if a["dist_m"] < 1000 else f"{a['dist_m'] / 1000:.1f} km".replace(".", ","))) + a["nome"]
                      if a["nivel"] == "evitar" else "Atenção: " + a["nome"],
                      "detalhe": a.get("motivo") or ""})

    rota_sql = {"wkt": _wkt(curta), "g": p.group_id}
    vel = await _ler(f"""SELECT c.id, c.name, c.speed, ST_Y(ST_Centroid(geo)) AS lat, ST_X(ST_Centroid(geo)) AS lng
        FROM mova.cerca c CROSS JOIN LATERAL (SELECT ({areas_risco.GEO_CERCA}) AS geo) x
        WHERE c.status = 1 AND c.group_id = :g AND coalesce(c.speed, 0) > 0 AND x.geo IS NOT NULL
          AND ST_DWithin(x.geo::geography, ST_GeomFromText(:wkt, 4326)::geography, 300)
        LIMIT 200""", rota_sql)
    for v in vel:
        itens.append({"tipo": "velocidade", "km": km(v["lat"], v["lng"]), "lat": v["lat"], "lng": v["lng"],
                      "titulo": f"Velocidade máxima {v['speed']} km/h", "detalhe": (v["name"] or "").strip()})

    apoio = await _ler(f"""SELECT c.name, cca.category_id, ST_Y(ST_Centroid(geo)) AS lat, ST_X(ST_Centroid(geo)) AS lng,
               ST_Distance(x.geo::geography, ST_GeomFromText(:wkt, 4326)::geography) AS dist
        FROM mova.cerca c JOIN mova.cerca_category_account cca ON cca.id = c.cerca_category_account_id
        CROSS JOIN LATERAL (SELECT ({areas_risco.GEO_CERCA}) AS geo) x
        WHERE c.status = 1 AND c.group_id = :g AND cca.category_id IN ({",".join(map(str, CATEGORIAS_APOIO))}) AND x.geo IS NOT NULL
          AND ST_DWithin(x.geo::geography, ST_GeomFromText(:wkt, 4326)::geography, {APOIO_RAIO_M})
        LIMIT 200""", rota_sql)
    for a in apoio:
        itens.append({"tipo": "apoio", "km": km(a["lat"], a["lng"]), "lat": a["lat"], "lng": a["lng"],
                      "titulo": f"{CATEGORIAS_APOIO[a['category_id']]}: {(a['name'] or '').strip()}",
                      "detalhe": f"a {round(a['dist'])} m da rota" if a["dist"] >= 50 else "na rota"})

    for pr in _pracas():
        if any(geo.dist_ponto_segmento_m(pr["lat"], pr["lon"], a, b) <= (RAIO_PRACA_RETA_M if estimada else RAIO_PRACA_M) for a, b in zip(curta, curta[1:])):
            itens.append({"tipo": "pedagio", "km": km(pr["lat"], pr["lon"]), "lat": pr["lat"], "lng": pr["lon"],
                          "titulo": f"Pedágio {pr['nome']}", "detalhe": f"{pr['rodovia']}/{pr['uf']}"})

    for pa in r["pausas"]:
        k = round(r["km_total"] * pa.get("dirigido_min", 0) / r["conducao_min"], 1) if r["conducao_min"] else 0
        itens.append({"tipo": "pausa", "km": k, "lat": None, "lng": None,
                      "titulo": "Descanso obrigatório de 11 h" if pa["tipo"] == "descanso" else "Pausa obrigatória de 30 min",
                      "detalhe": f"por volta de {pa['quando'][11:16]} · {pa['regra']}"})

    # Pontos críticos: onde a própria frota do cliente mais freia, acelera ou passa do limite.
    b0, o0, b1, o1 = geo.caixa(curta)
    crit = await _ler("""
        WITH suspeitos AS (
            -- Mesma regra da manutenção (EVENTOS_POR_KM_SUSPEITO): acima de 0,5 freada/aceleração por km é
            -- o equipamento contando demais, não o lugar (RNY-4F94: 102 "acelerações" no mesmo ponto).
            SELECT unit_id FROM mova.con_driver_h_km
            WHERE group_id = :g AND dt >= :desde
            GROUP BY unit_id
            HAVING sum(coalesce(count_break_excess, 0) + coalesce(count_acel_excess, 0))
                   > :susp * greatest(sum(distance_traveled_hist) / 1000.0, 1)),
        ev AS (
            SELECT d.tracker_event_id AS cod, d.latitude::float AS lat, d.longitude::float AS lng
            FROM mova.dev_status_30 d
            WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
              AND d.unit_id NOT IN (SELECT unit_id FROM suspeitos)
              AND d.local_time >= :desde AND d.tracker_event_id = ANY(CAST(:cods AS int[]))
              AND d.latitude BETWEEN :b0 AND :b1 AND d.longitude BETWEEN :o0 AND :o1
              AND ST_DWithin(ST_SetSRID(ST_MakePoint(d.longitude, d.latitude), 4326)::geography,
                             ST_GeomFromText(:wkt, 4326)::geography, :raio))
        SELECT round(lat / :grade) AS gy, round(lng / :grade) AS gx, avg(lat) AS lat, avg(lng) AS lng, count(*) AS n,
               jsonb_object_agg(cod, k) AS por_tipo
        FROM (SELECT cod, lat, lng, count(*) OVER (PARTITION BY round(lat / :grade), round(lng / :grade), cod) AS k FROM ev) z
        GROUP BY 1, 2 HAVING count(*) >= :minimo ORDER BY n DESC LIMIT 15""",
        {**rota_sql, "desde": datetime.now() - timedelta(days=CRITICO_DIAS), "cods": list(EVENTOS_CRITICOS),
         "b0": b0 - 0.01, "b1": b1 + 0.01, "o0": o0 - 0.01, "o1": o1 + 0.01, "raio": CRITICO_RAIO_M,
         "grade": CRITICO_GRADE, "minimo": CRITICO_MIN_EVENTOS, "susp": EVENTOS_POR_KM_SUSPEITO})
    for c in crit:
        tipos = sorted(((EVENTOS_CRITICOS.get(int(k), k), int(v)) for k, v in (c["por_tipo"] or {}).items()), key=lambda x: -x[1])
        itens.append({"tipo": "critico", "km": km(c["lat"], c["lng"]), "lat": c["lat"], "lng": c["lng"],
                      "titulo": f"Ponto crítico da frota: {tipos[0][0].lower()}" if tipos else "Ponto crítico da frota",
                      "detalhe": f"{c['n']} ocorrências nos últimos {CRITICO_DIAS} dias: " + ", ".join(f"{t} {n}" for t, n in tipos)})

    itens.sort(key=lambda x: (x["km"], x["tipo"]))
    veic = None
    if p.unit_id:
        v = await _ler("SELECT label AS placa, label2 AS prefixo FROM mova.tracked_unit WHERE id = :u", {"u": p.unit_id})
        if v:  # prefixo longo (descrição do veículo) não cabe no cabeçalho: vale a placa, como no CCO
            pref = v[0]["prefixo"] if v[0]["prefixo"] and len(v[0]["prefixo"]) <= 10 else None
            veic = " · ".join(x for x in (pref, v[0]["placa"]) if x)
    return {"rota": r, "itens": itens, "linha": curta, "veiculo": veic, "gerado_em": datetime.now().isoformat(timespec="minutes"),
            "estimada": estimada,
            "observacoes": [x for x in (
                "Pontos críticos não contam veículos com contagem suspeita de freada/aceleração (mais de 0,5 por km: defeito de equipamento).",
                "Caminho estimado em linha reta (motor de rotas ainda não ligado): as distâncias ao caminho são aproximadas." if estimada else None,
                f"Pontos críticos: eventos da própria frota a até {CRITICO_RAIO_M} m do caminho nos últimos {CRITICO_DIAS} dias, a partir de {CRITICO_MIN_EVENTOS} no mesmo lugar.",
                "Velocidades máximas: cercas do cliente com limite cadastrado que cruzam o caminho.",
            ) if x]}
