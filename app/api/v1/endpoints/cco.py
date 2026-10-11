"""
Painel CCO — a operação em tempo real numa tela só (fase 2: dado real).

Especificação combinada com o PM em 06/10/2026 (memória "painel-cco"):
cor do carro (vermelho crítico, amarelo moderado, verde andando, cinza parado),
wifi vermelho só sem comunicação > 2 h, avisos sem som que saem só com Visto
ou Tratado, SS vê todos os clientes e o cliente só a operação dele.

Fontes (só leitura):
- veículos: `mova.dev_status` (estado atual), `tracked_unit` (placa, prefixo,
  categoria → ícone caminhão/ônibus/van), `vcms_unit_device` (tem câmera);
- segurança (equipamento): `dev_status_30.tracker_event_id`, gravidade da
  `timeline.py` (CRITICOS) + pânico (11) e furto de combustível (440);
- câmera (ADAS/DMS): `vcms.vcms_history` + `vcms_alarm_type` pela chave
  (type, modelo, source) — ver app/core/camera.py; a gravidade sai do NOME
  (tabela aprovada: fadiga, celular, olhos fechados, colisão = crítico);
- alarmes do Monitor: `mova.alarm_violation` + `alarm.level` (3 = alto = crítico);
- manutenção: as mesmas regras validadas de manutencao.py (só com uma empresa
  escolhida — o cálculo é por grupo).
Achado (06/10/2026): `mova.fleet_events`, que Eventos e Videotelemetria usam,
está VAZIA; as ocorrências reais de câmera estão em `vcms.vcms_history`.

Janela: avisos das últimas `horas` (padrão 2; o painel deixa escolher 1, 2, 6 ou 12).
Agrupamento: o equipamento gera muito evento (10 mil acelerações bruscas em 12
h). Um aviso = veículo + tipo, com a contagem e o horário do último. Visto ou
Tratado grava a hora; só ocorrências depois dela reabrem o aviso. As marcações
ficam no armazenamento provisório `data/cco.sqlite`.
"""

import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.v1.endpoints import manutencao as man
from app.core.camera import JOIN_TIPO, NOME_SQL, gravidade_camera
from app.core import areas_risco, geo
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "cco.sqlite"
_trava = threading.Lock()
_CACHE: dict[tuple, tuple[float, object]] = {}
#: Janela padrão dos avisos. Com 12 h, 84 dos 132 carros da CECOTI ficavam vermelhos (06/10/2026).
JANELA_PADRAO_H = 2
SEM_COMUNICACAO_H = 2
CACHE_S = 25
CACHE_MANUT_S = 300

#: Eventos do equipamento no painel (nome, gravidade). Críticos = timeline.py + pânico e furto (PM, 06/10/2026).
EVENTOS = {
    7: ("Excesso de velocidade", "moderado"), 9: ("Freada brusca", "moderado"), 153: ("Aceleração brusca", "moderado"),
    163: ("Faixa vermelha", "moderado"), 13: ("Movimento sem tração", "moderado"), 27: ("Alimentação desconectada", "critico"),
    37: ("Excesso de velocidade na chuva", "critico"), 48: ("Motorista não autorizado", "critico"), 288: ("Parado acelerando", "moderado"),
    11: ("Pânico ativado", "critico"),
    # 440 "Furto de combustível" fora do painel: muito sensível, alarme falso (PM, 06/10/2026).
    161: ("Faixa amarela", "moderado"), 359: ("Curva brusca", "moderado"), 148: ("Excesso de embreagem", "moderado"),
}
#: PM, 06/10/2026: excesso de velocidade e aceleração brusca são moderados (acontecem o tempo
#: todo) e só viram crítico a partir de 20 ocorrências na janela — "deveria ocorrer 1 ou 2
#: vezes por dia no máximo". Com eles críticos, 49 de 132 carros da CECOTI ficavam vermelhos.
# PM, 06/10/2026 (2ª decisão): os eventos frequentes de condução e o furto de combustível
# (20 veículos da CECOTI em 2 h — detecção do equipamento) são moderados e viram crítico a
# partir de 50 ocorrências na janela.
ESCALAM_PARA_CRITICO = {7: 50, 153: 50, 9: 50, 163: 50, 13: 50, 288: 50}


def gravidade_evento(cod: int, n: int) -> str:
    base = EVENTOS[cod][1]
    lim = ESCALAM_PARA_CRITICO.get(cod)
    return "critico" if base == "critico" or (lim is not None and n >= lim) else base


def _so_digitos_nome(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def tipo_icone(categoria_id: Optional[int]) -> str:
    """Categoria do cadastro → ícone (mesma regra de iconesVeiculo.ts, com van separada)."""
    if categoria_id in (12, 22):
        return "onibus"
    if categoria_id == 15:
        return "van"
    if categoria_id in (1, 2, 10, 14):
        return "carro"
    if categoria_id == 9:
        return "moto"
    if categoria_id in (5, 6, 8, 23):
        return "maquina"
    return "caminhao"


def local_do_endereco(endereco: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(cidade, estado, bairro) do endereço que o rastreador manda.

    Formato em 10/10/2026: "Rua, Bairro - Cidade - Estado - Brasil" (2.735 de 2.754 veículos
    ativos); os outros 19 vêm como "CIDADE - UF". Transporte urbano é por cidade (PM, 10/10/2026:
    a Fênix opera em Florianópolis e São José)."""
    partes = [p.strip() for p in (endereco or "").split(" - ") if p.strip()]
    if len(partes) >= 3 and partes[-1].lower() == "brasil":
        bairro = partes[-4].split(",")[-1].strip() if len(partes) >= 4 else None
        return partes[-3], partes[-2], bairro or None
    if len(partes) == 2 and len(partes[1]) == 2 and partes[1].isalpha():
        return partes[0].title(), partes[1].upper(), None
    return None, None, None


def cor_do_carro(avisos_abertos: list[dict], ignicao: bool, velocidade: float) -> str:
    if any(a["gravidade"] == "critico" for a in avisos_abertos):
        return "vermelho"
    if avisos_abertos:
        return "amarelo"
    return "verde" if ignicao and (velocidade or 0) > 3 else "cinza"


# ------------------------------------------------------------- armazenamento

@contextmanager
def _con():
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS marcacao (chave TEXT PRIMARY KEY, unit_id INTEGER, situacao TEXT NOT NULL,
            ate TEXT NOT NULL, por INTEGER, por_nome TEXT, nota TEXT, em TEXT);
        CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT, unit_id INTEGER,
            situacao TEXT, ate TEXT, nota TEXT, por INTEGER, por_nome TEXT, em TEXT);
        """
    )
    try:
        with c:
            yield c
    finally:
        c.close()


# ------------------------------------------------------------------ escopo

def _grupos_do_usuario(user, group_id: Optional[int]) -> Optional[list[int]]:
    """None = todos (só a SS). Cliente: só os grupos dele."""
    ss = getattr(user, "master", 0) or getattr(user, "is_super_admin", False)
    if ss:
        return [group_id] if group_id else None
    meus = sorted({g for g, _ in (getattr(user, "group_access", None) or []) if g})
    if group_id:
        if group_id not in meus:
            raise HTTPException(403, "Sem acesso a esta empresa.")
        return [group_id]
    if not meus:
        raise HTTPException(403, "Usuário sem empresa.")
    return meus


SQL_VEICULOS = """
SELECT tu.id AS unit_id, tu.label AS placa, tu.label2 AS prefixo, tu.unit_category_id AS categoria_id, tu.group_id,
       g.name AS empresa, s.local_time, s.latitude::float AS lat, s.longitude::float AS lng, s.direction AS rumo,
       s.ignition AS ignicao, s.speed AS velocidade, coalesce(nullif(s.can_rpm, 0), nullif(s.rpm, 0)) AS rpm,
       s.faixa, fx.name AS faixa_nome, s.altitude, s.can_engine_coolant_temp AS temperatura,
       s.can_fuel_level_percent AS combustivel, s.driver_name AS motorista, s.address AS endereco,
       EXISTS (SELECT 1 FROM vcms.vcms_unit_device v WHERE v.unit_id = tu.id AND v.status = 1 AND v.release_date IS NULL) AS tem_camera
FROM mova.tracked_unit tu
JOIN mova.dev_status s ON s.unit_id = tu.id
LEFT JOIN mova."group" g ON g.id = tu.group_id
LEFT JOIN mova.faixas fx ON fx.id = s.faixa
WHERE tu.status = 1 AND s.latitude IS NOT NULL AND s.latitude <> 0 {filtro}
"""

SQL_EVENTOS = """
SELECT d.unit_id, d.tracker_event_id AS cod, count(*) AS n, max(d.local_time) AS ultimo, min(d.local_time) AS primeiro,
       max(d.speed) AS vel_max
FROM mova.dev_status_30 d
WHERE d.local_time >= :desde AND d.tracker_event_id = ANY(CAST(:codigos AS int[]))
  AND d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE status = 1 {filtro})
GROUP BY 1, 2
"""

SQL_CAMERA = """
SELECT h.unit_id, {nome} AS nome, count(*) AS n,
       max(h.local_time) AS ultimo, max(h.speed) AS vel_max
FROM vcms.vcms_history h {join_tipo}
WHERE h.local_time >= :desde AND h.unit_id IN (SELECT id FROM mova.tracked_unit WHERE status = 1 {filtro})
GROUP BY 1, 2
"""

# Parado com motor ligado há muito tempo (PM, 06/10/2026). Na CECOTI, em 05/10/2026: 120 paradas
# ligadas acima de 15 min, 37 acima de 30 min e 6 acima de 1 h — a partir de 30 min para não poluir o telão.
PARADO_LIGADO_MIN = 30
PARADO_LIGADO_CRITICO_MIN = 60
SQL_PARADO_LIGADO = """
WITH agora AS (
    SELECT s.unit_id, s.local_time FROM mova.dev_status s JOIN mova.tracked_unit tu ON tu.id = s.unit_id
    WHERE tu.status = 1 {filtro} AND s.ignition AND coalesce(s.speed, 0) <= 3 AND s.local_time >= now() - interval '30 minutes')
-- :pl_desde = agora − 3 h, calculado no Python: com `now() - interval` o banco não descarta as
-- partições diárias do dev_status_30 e a consulta passava de 50 s na frota toda.
-- Conta da primeira leitura LIGADA e PARADA depois da última vez que andou ou desligou
-- (31222, 06/10/2026: desligado das 19:44 às 20:23 aparecia como "parado ligado há 38 min").
, h AS (
    SELECT d.unit_id, d.local_time, (d.speed > 3 OR NOT d.ignition) AS quebra, coalesce(nullif(d.can_rpm, 0), d.rpm, 0) AS rpm
    FROM mova.dev_status_30 d WHERE d.local_time >= :pl_desde AND d.unit_id IN (SELECT unit_id FROM agora))
, q AS (SELECT unit_id, max(local_time) FILTER (WHERE quebra) AS q FROM h GROUP BY 1)
SELECT a.unit_id, a.local_time,
       coalesce(min(h.local_time) FILTER (WHERE NOT h.quebra AND (q.q IS NULL OR h.local_time > q.q)), a.local_time) AS desde,
       -- Rotação: o carro manda rpm (tem_rpm) mas ficou em 0 na parada = ignição ligada com motor desligado.
       coalesce(max(h.rpm), 0) > 0 AS tem_rpm,
       coalesce(max(h.rpm) FILTER (WHERE NOT h.quebra AND (q.q IS NULL OR h.local_time > q.q)), 0) AS rpm_parado
FROM agora a LEFT JOIN q ON q.unit_id = a.unit_id LEFT JOIN h ON h.unit_id = a.unit_id
GROUP BY a.unit_id, a.local_time
"""

SQL_ALARMES = """
SELECT av.unit_id, a.name AS nome, coalesce(a.level, 1) AS nivel, count(*) AS n, max(av.initial_time) AS ultimo
FROM mova.alarm_violation av JOIN mova.alarm a ON a.id = av.alarm_id
WHERE av.initial_time >= :desde AND av.unit_id IN (SELECT id FROM mova.tracked_unit WHERE status = 1 {filtro})
GROUP BY 1, 2, 3
"""


def _filtro(grupos: Optional[list[int]], alias: str = "") -> str:
    if grupos is None:
        return ""
    return f" AND {alias}group_id = ANY(CAST(:grupos AS int[]))"


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '60s'"))
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


async def _manutencao(grupo: int) -> list[dict]:
    """Alertas de manutenção do grupo (regras validadas de manutencao.py), com cache de 5 min."""
    k = ("manut", grupo)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_MANUT_S:
        return hit[1]  # type: ignore[return-value]
    frota = await man._frota(grupo)
    hist = await man._historico_24h(grupo)
    out = []
    for v in frota:
        for a in man._alertas_do_veiculo(v, hist.get(v["unit_id"], {})):
            out.append({"unit_id": v["unit_id"], "nome": a["titulo"], "gravidade": "critico" if a["nivel"] == "critico" else "moderado",
                        "detalhe": a.get("valor") or a.get("detalhe") or "", "chave_extra": a["chave"]})
    _CACHE[k] = (time.time(), out)
    return out


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/painel")
async def painel(group_id: Optional[int] = Query(None), horas: int = Query(JANELA_PADRAO_H, ge=1, le=12), user=Depends(require_permission("reports", "read"))):
    grupos = _grupos_do_usuario(user, group_id)
    chave_cache = ("painel", tuple(grupos) if grupos else None, horas)
    hit = _CACHE.get(chave_cache)
    if hit and time.time() - hit[0] < CACHE_S:
        dados = hit[1]
    else:
        desde = datetime.now() - timedelta(hours=horas)
        p = {"grupos": grupos or [], "desde": desde, "codigos": list(EVENTOS)}
        veics = await _ler(SQL_VEICULOS.format(filtro=_filtro(grupos, "tu.")), p)
        evs = await _ler(SQL_EVENTOS.format(filtro=_filtro(grupos)), p)
        cams = await _ler(SQL_CAMERA.format(filtro=_filtro(grupos), nome=NOME_SQL, join_tipo=JOIN_TIPO), p)
        alrs = await _ler(SQL_ALARMES.format(filtro=_filtro(grupos)), p)
        plig = await _ler(SQL_PARADO_LIGADO.format(filtro=_filtro(grupos, "tu.")), {**p, "pl_desde": datetime.now() - timedelta(hours=3)})
        manut = await _manutencao(grupos[0]) if grupos and len(grupos) == 1 else []
        dados = {"veics": veics, "evs": evs, "cams": cams, "alrs": alrs, "manut": manut, "plig": plig, "manut_ok": bool(grupos and len(grupos) == 1)}
        _CACHE[chave_cache] = (time.time(), dados)

    with _trava, _con() as c:
        marcas = {r["chave"]: dict(r) for r in c.execute("SELECT * FROM marcacao")}

    agora = datetime.now()
    avisos: list[dict] = []

    def add(chave: str, unit_id: int, nome: str, fonte: str, grav: str, n: int, ultimo, detalhe: str = "", estado: bool = False):
        m = marcas.get(chave)
        if m and ultimo and _iso(ultimo) <= m["ate"]:
            return  # visto/tratado e nada novo depois
        # `ate` é o que o Visto grava; aviso de estado (manutenção) não tem horário de ocorrência.
        avisos.append({"id": chave, "unit_id": unit_id, "nome": nome, "fonte": fonte, "gravidade": grav, "quantidade": int(n),
                       "ultimo": None if estado else _iso(ultimo), "ate": _iso(ultimo), "detalhe": detalhe,
                       "reaberto": bool(m), "marcado_antes": m["situacao"] if m else None})

    for e in dados["evs"]:
        nome = EVENTOS[e["cod"]][0]
        grav = gravidade_evento(e["cod"], int(e["n"]))
        add(f"ev:{e['unit_id']}:{e['cod']}", e["unit_id"], nome, "seguranca", grav, e["n"], e["ultimo"],
            f"velocidade máx. {round(e['vel_max'])} km/h" if e["cod"] == 7 and e["vel_max"] else "")
    for e in dados["cams"]:
        fonte, grav = gravidade_camera(e["nome"])
        add(f"cam:{e['unit_id']}:{_so_digitos_nome(e['nome']).lower()}", e["unit_id"], _so_digitos_nome(e["nome"]), fonte, grav, e["n"], e["ultimo"])
    for e in dados["alrs"]:
        add(f"alm:{e['unit_id']}:{_so_digitos_nome(e['nome']).lower()}", e["unit_id"], _so_digitos_nome(e["nome"]), "seguranca",
            "critico" if int(e["nivel"]) >= 3 else "moderado", e["n"], e["ultimo"], "alarme do Monitor")
    hoje = agora.date().isoformat()
    for e in dados["manut"]:
        # Alerta de manutenção é estado, não ocorrência: Visto vale para o dia.
        add(f"man:{e['unit_id']}:{e['chave_extra']}", e["unit_id"], e["nome"], "manutencao", e["gravidade"], 1, hoje + "T23:59:59", e["detalhe"], estado=True)

    for e in dados.get("plig", []):
        if not e["desde"]:
            continue
        minutos = int((e["local_time"] - e["desde"]).total_seconds() // 60)
        if minutos < PARADO_LIGADO_MIN:
            continue
        if e["tem_rpm"] and not e["rpm_parado"]:
            continue  # chave virada, motor desligado: não é marcha lenta
        sem_rpm = "" if e["tem_rpm"] else " · ignição ligada, sem leitura de rotação"
        # Chave pela hora em que parou: Visto vale para esta parada; a próxima abre de novo.
        add(f"pl:{e['unit_id']}:{_iso(e['desde'])}", e["unit_id"], "Parado com motor ligado", "operacao",
            "critico" if minutos >= PARADO_LIGADO_CRITICO_MIN else "moderado", 1, e["desde"],
            (f"há {minutos // 60} h {minutos % 60:02d} min" if minutos >= 60 else f"há {minutos} min") + sem_rpm)

    await _avisos_de_rota(dados["veics"], grupos, agora, add)

    por_unidade: dict[int, list[dict]] = {}
    for a in avisos:
        por_unidade.setdefault(a["unit_id"], []).append(a)

    veiculos = []
    for v in dados["veics"]:
        lt = v["local_time"]
        sem_com = not lt or (agora - lt) > timedelta(hours=SEM_COMUNICACAO_H)
        ig = bool(v["ignicao"]) and not sem_com
        # Velocidade só vale com posição recente: a leitura de 2 h atrás com 54 km/h deixava o carro
        # "em movimento" no telão (TZM-4E88, 06/10/2026).
        recente = bool(lt) and (agora - lt) <= timedelta(minutes=POSICAO_RECENTE_MIN)
        vel = float(v["velocidade"] or 0) if ig and recente else 0.0
        meus = por_unidade.get(v["unit_id"], [])
        cidade, uf, bairro = local_do_endereco(v["endereco"])
        veiculos.append({
            "cidade": cidade, "uf": uf, "bairro": bairro,
            "id": v["unit_id"], "placa": v["placa"],
            "prefixo": v["prefixo"] if v["prefixo"] and len(v["prefixo"]) <= 10 else v["placa"],
            "descricao": v["prefixo"] if v["prefixo"] and len(v["prefixo"]) > 10 else None, "empresa": v["empresa"], "group_id": v["group_id"],
            "tipo": tipo_icone(v["categoria_id"]), "lat": v["lat"], "lng": v["lng"], "rumo": v["rumo"],
            "ignicao": ig, "velocidade": round(vel), "rpm": int(v["rpm"]) if ig and v["rpm"] else None,
            "faixa": (v["faixa_nome"] or "").capitalize() if ig and v["faixa_nome"] and v["faixa"] != 8 else None,
            "altitude": round(float(v["altitude"])) if v["altitude"] not in (None, 0) else None,
            "temperatura": round(float(v["temperatura"])) if ig and v["temperatura"] and 0 < float(v["temperatura"]) < 150 else None,
            "combustivel": round(float(v["combustivel"])) if v["combustivel"] and 0 < float(v["combustivel"]) <= 100 else None,
            "motorista": v["motorista"] if v["motorista"] and "não informado" not in v["motorista"].lower() else None,
            "endereco": v["endereco"], "ultima_comunicacao": _iso(lt), "comunicando": not sem_com, "tem_camera": bool(v["tem_camera"]),
            "cor": cor_do_carro(meus, ig, vel),
        })
    ids = {v["id"] for v in veiculos}
    avisos = [a for a in avisos if a["unit_id"] in ids]
    avisos.sort(key=lambda a: (a["gravidade"] != "critico", -(datetime.fromisoformat(str(a["ultimo"])).timestamp() if a["ultimo"] else agora.timestamp())))
    return {
        "veiculos": veiculos, "avisos": avisos[:8000], "avisos_total": len(avisos),
        "manutencao_disponivel": dados["manut_ok"], "janela_horas": horas, "atualizado_em": agora.isoformat(timespec="seconds"),
    }


#: Sem posição há mais que isso, o carro não conta como em movimento.
POSICAO_RECENTE_MIN = 30
#: Parada fora do lugar: a menos disto de um ponto da programação ainda é "no lugar".
RAIO_PONTO_PROGRAMADO_M = 300


async def _avisos_de_rota(veics: list[dict], grupos: Optional[list[int]], agora: datetime, add) -> None:
    """Área de risco, desvio de rota e parada fora do lugar (PM/CEO, 06/10/2026).

    - Área de risco: veículo comunicando dentro de uma área "evitar" do próprio cliente.
    - Desvio de rota: veículo com programação em execução, andando, com TODAS as leituras dos
      últimos `desvio_min` minutos a mais de `tolerancia_m` do caminho programado.
    - Parada fora do lugar: parado há `parada_max_min` minutos fora do caminho, longe dos pontos
      da programação e fora de qualquer cerca do cliente (garagem, cliente, posto…).
    Visto/Tratado vale para o dia (a chave leva a data), como os avisos de manutenção.
    """
    hoje = agora.date().isoformat()
    fim_do_dia = hoje + "T23:59:59"
    vivos = {v["unit_id"]: v for v in veics if v["local_time"] and (agora - v["local_time"]) <= timedelta(minutes=30)}

    areas = [a for a in await areas_risco.areas(grupos) if a["nivel"] == "evitar"]
    for v in vivos.values():
        for a in areas:
            if a["group_id"] == v["group_id"] and any(geo.dentro(v["lat"], v["lng"], anel) for anel in a["aneis"]):
                add(f"risco:{v['unit_id']}:{a['chave']}:{hoje}", v["unit_id"], "Em área de risco", "seguranca", "critico", 1,
                    fim_do_dia, a["nome"] + (f" · {a['motivo']}" if a.get("motivo") else ""), estado=True)

    progs = [p for p in areas_risco.programacoes(grupos, so_ativas=True) if p["unit_id"] in vivos and areas_risco.em_execucao(p, agora)]
    if not progs:
        return
    # Os rastreadores mandam posição a cada ~2 min andando e menos parados: a janela cobre folgado.
    janela = max(max(p["desvio_min"] * 2 + 4, p["parada_max_min"] + 30) for p in progs)
    leit = await _ler("""SELECT unit_id, local_time, latitude::float AS lat, longitude::float AS lng, coalesce(speed, 0) AS vel
                         FROM mova.dev_status_30 WHERE unit_id = ANY(CAST(:u AS int[])) AND local_time >= :d
                           AND latitude IS NOT NULL AND latitude <> 0 ORDER BY local_time""",
                      {"u": [p["unit_id"] for p in progs], "d": agora - timedelta(minutes=janela)})
    por_u: dict[int, list[dict]] = {}
    for x in leit:
        por_u.setdefault(x["unit_id"], []).append(x)

    candidatos = []
    for p in progs:
        linha = geo.simplificar(p["trajeto"], 800)
        ls = por_u.get(p["unit_id"], [])
        for x in ls:
            x["dist"] = geo.dist_ponto_linha_m(x["lat"], x["lng"], linha)
        v = vivos[p["unit_id"]]
        d_agora = geo.dist_ponto_linha_m(v["lat"], v["lng"], linha)
        # Desvio: as últimas leituras seguidas fora da rota, cobrindo pelo menos `desvio_min` minutos.
        fora = []
        for x in reversed(ls):
            if x["dist"] <= p["tolerancia_m"]:
                break
            fora.append(x)
        cobre_desvio = len(fora) >= 2 and (fora[0]["local_time"] - fora[-1]["local_time"]) >= timedelta(minutes=p["desvio_min"]) - timedelta(seconds=20)
        if cobre_desvio and float(v["velocidade"] or 0) > 3 and d_agora > p["tolerancia_m"]:
            add(f"desvio:{p['id']}:{hoje}", p["unit_id"], "Desvio de rota", "operacao", "critico", 1, fim_do_dia,
                f"a {round(d_agora)} m da rota {p['nome']}", estado=True)
            continue
        # Parada: parado desde a última leitura em movimento (sem nenhuma na janela, desde o início dela).
        ult_mov = max((x["local_time"] for x in ls if x["vel"] > 3), default=None)
        parado_desde = ult_mov or (agora - timedelta(minutes=janela))
        if float(v["velocidade"] or 0) <= 3 and agora - parado_desde >= timedelta(minutes=p["parada_max_min"]) and d_agora > p["tolerancia_m"] \
                and all(geo.haversine_m(v["lat"], v["lng"], q["latitude"], q["longitude"]) > RAIO_PONTO_PROGRAMADO_M for q in p["pontos"]):
            candidatos.append((p, v, d_agora))
    if not candidatos:
        return
    # Dentro de uma cerca do cliente (garagem, cliente, posto…) é lugar permitido.
    pts = ",".join(f"({i}, {v['lng']:.6f}, {v['lat']:.6f}, {v['group_id']})" for i, (_, v, _) in enumerate(candidatos))
    dentro = {r["i"] for r in await _ler(f"""
        SELECT x.i FROM (VALUES {pts}) AS x(i, lng, lat, g)
        WHERE EXISTS (SELECT 1 FROM mova.cerca c WHERE c.status = 1 AND c.group_id = x.g
                        AND ST_Intersects(({areas_risco.GEO_CERCA}), ST_SetSRID(ST_MakePoint(x.lng, x.lat), 4326)))""", {})}
    for i, (p, v, d) in enumerate(candidatos):
        if i in dentro:
            continue
        add(f"parada:{p['id']}:{hoje}", p["unit_id"], "Parada fora do lugar", "operacao", "moderado", 1, fim_do_dia,
            f"parado há mais de {p['parada_max_min']} min a {round(d)} m da rota {p['nome']}", estado=True)


@router.get("/veiculo/{unit_id}")
async def veiculo(unit_id: int, user=Depends(require_permission("reports", "read"))):
    """Dados do card: consumo médio dos últimos minutos (pelo totalizador de combustível, em mL)."""
    rows = await _ler("SELECT group_id FROM mova.tracked_unit WHERE id = :u", {"u": unit_id})
    if not rows:
        raise HTTPException(404, "Veículo não encontrado.")
    _grupos_do_usuario(user, rows[0]["group_id"])
    leit = await _ler("""SELECT local_time, can_total_used_fuel AS f FROM mova.dev_status_30
                         WHERE unit_id = :u AND local_time >= :d AND can_total_used_fuel > 0 ORDER BY local_time""",
                      {"u": unit_id, "d": datetime.now() - timedelta(minutes=40)})
    consumo, minutos = None, None
    if len(leit) >= 2:
        dt_h = (leit[-1]["local_time"] - leit[0]["local_time"]).total_seconds() / 3600
        dl = (float(leit[-1]["f"]) - float(leit[0]["f"])) / 1000
        if dt_h >= 5 / 60 and 0 <= dl and dl / dt_h <= 150:  # plausível: até 150 L/h
            consumo, minutos = round(dl / dt_h, 1), round(dt_h * 60)
    return {"consumo_lh": consumo, "consumo_minutos": minutos}


@router.get("/posicao/{unit_id}")
async def posicao(unit_id: int, user=Depends(require_permission("reports", "read"))):
    """Posição atual de um veículo, para "Seguir veículo": consulta leve, uma linha do dev_status."""
    rows = await _ler("""SELECT tu.group_id, s.local_time, s.latitude::float AS lat, s.longitude::float AS lng, s.direction AS rumo,
                                s.ignition AS ignicao, s.speed AS velocidade, s.address AS endereco
                         FROM mova.tracked_unit tu JOIN mova.dev_status s ON s.unit_id = tu.id WHERE tu.id = :u""", {"u": unit_id})
    if not rows:
        raise HTTPException(404, "Veículo não encontrado.")
    r = dict(rows[0])
    _grupos_do_usuario(user, r.pop("group_id"))
    r["local_time"] = r["local_time"].isoformat() if r["local_time"] else None
    return r


class Marcar(BaseModel):
    situacao: str  # visto | tratado
    ate: str       # horário da última ocorrência vista (ISO)
    unit_id: int
    nota: Optional[str] = None
    nome: Optional[str] = None  # nome do aviso, para a lista de ocorrências do turno


@router.post("/avisos/{chave:path}/marcar")
async def marcar(chave: str, p: Marcar, user=Depends(require_permission("reports", "read"))):
    if p.situacao not in ("visto", "tratado"):
        raise HTTPException(422, "Situação deve ser visto ou tratado.")
    rows = await _ler("SELECT group_id FROM mova.tracked_unit WHERE id = :u", {"u": p.unit_id})
    if not rows:
        raise HTTPException(404, "Veículo não encontrado.")
    _grupos_do_usuario(user, rows[0]["group_id"])
    nome = getattr(user, "email", None) or str(getattr(user, "user_id", ""))
    agora = datetime.now().isoformat(timespec="seconds")
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO marcacao (chave, unit_id, situacao, ate, por, por_nome, nota, em) VALUES (?,?,?,?,?,?,?,?)",
                  (chave, p.unit_id, p.situacao, p.ate, getattr(user, "user_id", None), nome, p.nota, agora))
        if "aviso" not in {x[1] for x in c.execute("PRAGMA table_info(historico)")}:
            c.execute("ALTER TABLE historico ADD COLUMN aviso TEXT")
        c.execute("INSERT INTO historico (chave, unit_id, situacao, ate, nota, por, por_nome, em, aviso) VALUES (?,?,?,?,?,?,?,?,?)",
                  (chave, p.unit_id, p.situacao, p.ate, p.nota, getattr(user, "user_id", None), nome, agora, p.nome))
    return {"ok": True}


@router.get("/turno")
async def turno(desde: datetime = Query(...), group_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    """Ocorrências tratadas no turno (quem marcou o quê), para a passagem de turno."""
    grupos = _grupos_do_usuario(user, group_id)
    with _trava, _con() as c:
        if "aviso" not in {x[1] for x in c.execute("PRAGMA table_info(historico)")}:
            c.execute("ALTER TABLE historico ADD COLUMN aviso TEXT")
        rows = [dict(r) for r in c.execute("SELECT * FROM historico WHERE em >= ? ORDER BY id DESC LIMIT 1000", (desde.isoformat(),))]
    if not rows:
        return {"data": []}
    veics = await _ler("SELECT id, label AS placa, label2 AS prefixo, group_id FROM mova.tracked_unit WHERE id = ANY(CAST(:ids AS int[]))",
                       {"ids": list({r["unit_id"] for r in rows if r["unit_id"]})})
    por_id = {v["id"]: v for v in veics if grupos is None or v["group_id"] in grupos}
    out = []
    for r in rows:
        v = por_id.get(r["unit_id"])
        if not v:
            continue
        pref = v["prefixo"] if v["prefixo"] and len(v["prefixo"]) <= 10 else v["placa"]
        out.append({"em": r["em"], "situacao": r["situacao"], "por": r["por_nome"], "nota": r["nota"],
                    "aviso": r.get("aviso"), "unit_id": r["unit_id"], "veiculo": pref, "placa": v["placa"]})
    return {"data": out}


@router.get("/avisos/historico")
async def historico(unit_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    """Quem marcou o quê e quando (para auditoria do CCO)."""
    with _trava, _con() as c:
        sql, args = "SELECT * FROM historico", ()
        if unit_id:
            sql, args = sql + " WHERE unit_id = ?", (unit_id,)
        rows = [dict(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT 500", args)]
    if unit_id:
        r = await _ler("SELECT group_id FROM mova.tracked_unit WHERE id = :u", {"u": unit_id})
        if r:
            _grupos_do_usuario(user, r[0]["group_id"])
    elif not (getattr(user, "master", 0) or getattr(user, "is_super_admin", False)):
        raise HTTPException(403, "Informe o veículo.")
    return {"data": rows}
