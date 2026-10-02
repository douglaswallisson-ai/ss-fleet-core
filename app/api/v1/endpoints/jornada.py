"""
Jornada do motorista: Lei do Motorista, escala planejada × realizada e
espelho de ponto.

De onde vem a jornada (só leitura no banco):
- Telemetria: trechos de viagem de `mova.con_telemetry` com o motorista
  identificado no veículo (`driver_id`). Jornada = do primeiro trecho do dia
  ao último; direção = soma dos trechos; pausa = intervalo entre trechos.
- Diário de bordo (`mova.driver_logbook`, eventos 47 login / 5 logout), quando
  o cliente usa (hoje a Quataí): o login e o logout marcam entrada e saída.

SUPOSIÇÃO: a jornada pela telemetria é uma estimativa (o motorista trabalha
antes de ligar o veículo e depois de desligar). Confirmar com o RH de cada
cliente se vale como ponto ou só como conferência.

Regras usadas (parâmetros em REGRAS, por tipo de operação). Pesquisa de
02/10/2026; confirmar com o jurídico antes de usar em autuação:
- CLT art. 235-C (Lei 13.103/2015): jornada de 8 h + até 2 h extras (até 4 h
  com convenção/acordo coletivo); intervalo de refeição de no mínimo 1 h;
- CTB art. 67-C caput: no máximo 5h30 de direção ininterrupta (carga e
  passageiros);
- CTB art. 67-C §1º: carga = 30 min de descanso dentro de cada 6 h de direção;
  §1º-A: passageiros = 30 min a cada 4 h. Os dois podem ser fracionados, então
  somamos as pausas até completar 30 min;
- interjornada de 11 h ININTERRUPTAS em 24 h: o STF (ADI 5322, 30/06/2023)
  derrubou o fracionamento e a coincidência com as paradas do CTB, e também
  passou a contar o tempo de espera como jornada. Por isso a jornada vai do
  primeiro ao último trecho, sem descontar paradas;
- CLT art. 73: hora noturna das 22 h às 5 h.
SUPOSIÇÃO: a lei não diz o tamanho mínimo de cada fração do descanso. Aqui,
parada de 10 min ou mais conta como pausa; intervalo de 1 h ou mais conta como
refeição.

⚠️ ARMAZENAMENTO PROVISÓRIO: escala planejada e justificativas ficam num
SQLite local (`data/jornada.sqlite`) — o banco de produção é só leitura.
"""

import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "jornada.sqlite"
_trava = threading.Lock()
_CACHE: dict[tuple, tuple[float, object]] = {}

REGRAS = {
    # descanso_a_cada_min: direção somada que exige 30 min de descanso (fracionável) — CTB 67-C §1º e §1º-A.
    "carga": {"jornada_h": 8, "extra_max_h": 2, "extra_convencao_h": 4, "direcao_continua_min": 330,
              "descanso_a_cada_min": 330, "descanso_min": 30, "refeicao_min": 60, "interjornada_h": 11},
    "passageiros": {"jornada_h": 8, "extra_max_h": 2, "extra_convencao_h": 4, "direcao_continua_min": 330,
                    "descanso_a_cada_min": 240, "descanso_min": 30, "refeicao_min": 60, "interjornada_h": 11},
}
PAUSA_MIN = 10
JORNADA_REFEICAO_H = 6
NOTURNO = (22, 5)
CATEGORIAS_PASSAGEIROS = {12, 22}
#: Sem trecho por este tempo, começa outra jornada (SUPOSIÇÃO; a lei pede 11 h de interjornada,
#: mas descanso menor ainda separa jornadas — e vira infração de interjornada).
DESCANSO_SEPARA_H = 6
#: Acima disso quase sempre é motorista que não saiu do veículo (identificação ficou presa).
JORNADA_SUSPEITA_H = 16
CORINGA = re.compile(r"^\s*n[aã]o\s+(informado|identificado)\s*$", re.I)
MOTIVOS = ["Trânsito", "Escala errada", "Socorro", "Manutenção do veículo", "Treinamento", "Outro"]


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS escala (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, driver_id INTEGER NOT NULL,
            dia TEXT NOT NULL, inicio TEXT NOT NULL, fim TEXT NOT NULL, unit_id INTEGER, linha TEXT, obs TEXT,
            atualizado_em TEXT, atualizado_por INTEGER, UNIQUE (group_id, driver_id, dia));
        CREATE TABLE IF NOT EXISTS justificativa (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, driver_id INTEGER NOT NULL,
            dia TEXT NOT NULL, motivo TEXT NOT NULL, texto TEXT, folga INTEGER DEFAULT 0,
            registrado_em TEXT, registrado_por INTEGER, UNIQUE (group_id, driver_id, dia));
        """
    )
    return c


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


def _h(minutos: float) -> float:
    return round(minutos / 60, 2)


def _noturno_min(a: datetime, b: datetime) -> float:
    """Minutos entre a e b que caem das 22 h às 5 h."""
    total = 0.0
    d = datetime.combine(a.date() - timedelta(days=1), dtime(0))
    while d < b:
        ini = d.replace(hour=NOTURNO[0])
        fim = (d + timedelta(days=1)).replace(hour=NOTURNO[1])
        lo, hi = max(a, ini), min(b, fim)
        if hi > lo:
            total += (hi - lo).total_seconds() / 60
        d += timedelta(days=1)
    return total


async def _frota(group_id: int) -> tuple[list[int], str]:
    rows = await _ler("SELECT id, unit_category_id FROM mova.tracked_unit WHERE status = 1 AND group_id = :g", {"g": group_id})
    ids = [r["id"] for r in rows]
    pass_ = sum(1 for r in rows if r["unit_category_id"] in CATEGORIAS_PASSAGEIROS)
    return ids, "passageiros" if pass_ > len(rows) / 2 else "carga"


async def _trechos(ids: list[int], ini: datetime, fim: datetime) -> list[dict]:
    rows = await _ler(
        """SELECT ct.unit_id, tu.label AS placa, tu.label2 AS prefixo, ct.start_time, COALESCE(ct.end_time, ct.start_time) AS end_time,
                  ct.driver_id, NULLIF(NULLIF(TRIM(ct.driver_name), ''), 'NULL') AS motorista
           FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
           WHERE ct.unit_id = ANY(CAST(:ids AS bigint[])) AND ct.start_time >= :i AND ct.start_time < :f
             AND COALESCE(ct.driver_id, 0) > 0""",
        {"ids": ids, "i": ini, "f": fim},
    )
    return [dict(r) for r in rows]


def _apurar(trechos: list[dict], regra: dict, fim_anterior: Optional[datetime]) -> dict:
    """Jornada de um motorista num dia a partir dos trechos ordenados."""
    trechos.sort(key=lambda t: t["start_time"])
    ini, fim = trechos[0]["start_time"], max(t["end_time"] for t in trechos)
    direcao = 0.0
    pausas = []
    bloco_ini = trechos[0]["start_time"]
    bloco_fim = trechos[0]["end_time"]
    maior_bloco = 0.0
    # Descanso fracionado: direção somada até juntar 30 min de pausas.
    dir_acum = pausa_acum = 0.0
    maior_sem_descanso = 0.0
    ult_fim: Optional[datetime] = None
    for t in trechos:
        if ult_fim is not None and (t["start_time"] - ult_fim).total_seconds() / 60 >= PAUSA_MIN:
            pausa_acum += (t["start_time"] - ult_fim).total_seconds() / 60
            if pausa_acum >= regra["descanso_min"]:
                dir_acum = pausa_acum = 0.0
        ini_util = t["start_time"] if ult_fim is None else max(t["start_time"], ult_fim)
        dir_acum += max(0.0, (t["end_time"] - ini_util).total_seconds() / 60)
        maior_sem_descanso = max(maior_sem_descanso, dir_acum)
        ult_fim = t["end_time"] if ult_fim is None else max(ult_fim, t["end_time"])
        if t["start_time"] > bloco_fim and (t["start_time"] - bloco_fim).total_seconds() / 60 >= PAUSA_MIN:
            pausas.append({"de": bloco_fim.strftime("%H:%M"), "ate": t["start_time"].strftime("%H:%M"),
                           "min": round((t["start_time"] - bloco_fim).total_seconds() / 60)})
            maior_bloco = max(maior_bloco, (bloco_fim - bloco_ini).total_seconds() / 60)
            bloco_ini = t["start_time"]
        bloco_fim = max(bloco_fim, t["end_time"])
        direcao += max(0.0, (t["end_time"] - t["start_time"]).total_seconds() / 60)
    maior_bloco = max(maior_bloco, (bloco_fim - bloco_ini).total_seconds() / 60)
    jornada = (fim - ini).total_seconds() / 60
    maior_pausa = max((p["min"] for p in pausas), default=0)
    limite_jornada = regra["jornada_h"] * 60
    extra = max(0.0, jornada - limite_jornada)
    interjornada = (ini - fim_anterior).total_seconds() / 60 if fim_anterior else None

    infracoes = []
    if maior_bloco > regra["direcao_continua_min"]:
        infracoes.append({"regra": "direcao_continua", "titulo": "Direção contínua acima do limite",
                          "detalhe": f"{_fmt(maior_bloco)} sem pausa de {PAUSA_MIN} min (limite {_fmt(regra['direcao_continua_min'])}, CTB art. 67-C)."})
    if maior_sem_descanso > regra["descanso_a_cada_min"] and maior_bloco <= regra["direcao_continua_min"]:
        infracoes.append({"regra": "descanso", "titulo": "Descanso de 30 min não cumprido",
                          "detalhe": f"{_fmt(maior_sem_descanso)} de direção sem somar {regra['descanso_min']} min de descanso "
                                     f"(exigido a cada {_fmt(regra['descanso_a_cada_min'])}, CTB art. 67-C §1º)."})
    if jornada > limite_jornada + regra["extra_max_h"] * 60:
        infracoes.append({"regra": "jornada", "titulo": "Jornada acima do permitido",
                          "detalhe": f"{_fmt(jornada)} de jornada (máximo {regra['jornada_h'] + regra['extra_max_h']} h com extras; {regra['jornada_h'] + regra['extra_convencao_h']} h se houver convenção coletiva)."})
    if jornada > JORNADA_REFEICAO_H * 60 and maior_pausa < regra["refeicao_min"]:
        infracoes.append({"regra": "refeicao", "titulo": "Sem intervalo de refeição",
                          "detalhe": f"Maior pausa de {maior_pausa} min numa jornada de {_fmt(jornada)} (mínimo {regra['refeicao_min']} min)."})
    if interjornada is not None and interjornada < regra["interjornada_h"] * 60:
        infracoes.append({"regra": "interjornada", "titulo": "Interjornada curta",
                          "detalhe": f"Só {_fmt(interjornada)} de descanso desde o fim da jornada anterior (mínimo {regra['interjornada_h']} h seguidas)."})
    veics = sorted({" · ".join(x for x in (t["prefixo"], t["placa"]) if x and x.strip()) for t in trechos})
    return {
        "inicio": ini.isoformat(timespec="minutes"), "fim": fim.isoformat(timespec="minutes"),
        "jornada_h": _h(jornada), "direcao_h": _h(direcao), "extra_h": _h(extra),
        "noturno_h": _h(_noturno_min(ini, fim)), "maior_direcao_continua_min": round(maior_bloco),
        "maior_direcao_sem_descanso_min": round(maior_sem_descanso),
        "maior_pausa_min": maior_pausa, "pausas": pausas[:30], "interjornada_h": _h(interjornada) if interjornada is not None else None,
        "veiculos": veics, "infracoes": infracoes,
    }


def _fmt(minutos: float) -> str:
    m = int(round(minutos))
    return f"{m // 60}h{m % 60:02d}"


async def _diario(group_id: int, dia: date) -> dict[int, dict]:
    """Login/logout do diário de bordo (quando o cliente usa)."""
    rows = await _ler(
        """SELECT d.driver_id, min(d.local_time) FILTER (WHERE d.tracker_event_id = 47) AS login,
                  max(d.local_time) FILTER (WHERE d.tracker_event_id = 5) AS logout
           FROM mova.driver_logbook d JOIN mova.tracked_unit tu ON tu.id = d.unit_id
           WHERE tu.group_id = :g AND d.local_time >= :i AND d.local_time < :f AND COALESCE(d.driver_id, 0) > 0
           GROUP BY 1""",
        {"g": group_id, "i": datetime.combine(dia, dtime(0)), "f": datetime.combine(dia + timedelta(days=1), dtime(0))},
    )
    return {r["driver_id"]: dict(r) for r in rows}


async def _dia(group_id: int, dia: date) -> dict:
    """Jornadas que COMEÇAM no dia, separadas por descanso (não pelo calendário)."""
    chave = ("dia", group_id, dia)
    ttl = 300 if dia >= date.today() - timedelta(days=1) else 6 * 3600
    if (c := _CACHE.get(chave)) and time.time() - c[0] < ttl:
        return c[1]
    ids, operacao = await _frota(group_id)
    regra = REGRAS[operacao]
    if not ids:
        return {"operacao": operacao, "regra": regra, "motoristas": {}, "sem_identificacao": 0}
    # Um dia antes (para achar a jornada anterior) e meio dia depois (jornada que vira a noite).
    trechos = await _trechos(ids, datetime.combine(dia - timedelta(days=1), dtime(0)),
                             datetime.combine(dia + timedelta(days=1), dtime(12)))
    diario = await _diario(group_id, dia)
    por: dict[int, list] = defaultdict(list)
    nomes: dict[int, str] = {}
    sem_id = 0
    for t in trechos:
        if t["motorista"] and CORINGA.match(t["motorista"]):
            sem_id += 1
            continue
        por[t["driver_id"]].append(t)
        if t["motorista"]:
            nomes[t["driver_id"]] = t["motorista"]
    motoristas = {}
    for drv, tt in por.items():
        tt.sort(key=lambda x: x["start_time"])
        jornadas: list[list] = []
        for x in tt:
            if jornadas and (x["start_time"] - max(y["end_time"] for y in jornadas[-1])).total_seconds() < DESCANSO_SEPARA_H * 3600:
                jornadas[-1].append(x)
            else:
                jornadas.append([x])
        for k, jj in enumerate(jornadas):
            if jj[0]["start_time"].date() != dia:
                continue
            fim_ant = max(y["end_time"] for y in jornadas[k - 1]) if k > 0 else None
            j = _apurar(jj, regra, fim_ant)
            j["a_conferir"] = j["jornada_h"] > JORNADA_SUSPEITA_H
            dg = diario.get(drv)
            j["fonte"] = "diario" if dg and dg.get("login") else "telemetria"
            if dg and dg.get("login"):
                j["entrada_diario"] = dg["login"].isoformat(timespec="minutes")
                j["saida_diario"] = dg["logout"].isoformat(timespec="minutes") if dg.get("logout") else None
            j["nome"] = nomes.get(drv) or f"Motorista {drv}"
            if drv in motoristas:  # duas jornadas começando no mesmo dia: fica a mais longa
                if motoristas[drv]["jornada_h"] >= j["jornada_h"]:
                    continue
            motoristas[drv] = j
    saida = {"operacao": operacao, "regra": regra, "motoristas": motoristas, "sem_identificacao": sem_id}
    _CACHE[chave] = (time.time(), saida)
    return saida


def _escalas(group_id: int, ini: date, fim: date) -> dict[tuple, dict]:
    with _con() as c:
        rows = c.execute("SELECT * FROM escala WHERE group_id = ? AND dia BETWEEN ? AND ?", (group_id, ini.isoformat(), fim.isoformat())).fetchall()
    return {(r["driver_id"], r["dia"]): dict(r) for r in rows}


def _justificativas(group_id: int, ini: date, fim: date) -> dict[tuple, dict]:
    with _con() as c:
        rows = c.execute("SELECT * FROM justificativa WHERE group_id = ? AND dia BETWEEN ? AND ?", (group_id, ini.isoformat(), fim.isoformat())).fetchall()
    return {(r["driver_id"], r["dia"]): dict(r) for r in rows}


def _comparar(plano: Optional[dict], j: Optional[dict], dia: date) -> Optional[dict]:
    """Escala planejada × realizada."""
    if not plano:
        return {"situacao": "nao_escalado"} if j else None
    if not j:
        return {"situacao": "falta", "planejado": f"{plano['inicio']}–{plano['fim']}"}
    p_ini = datetime.combine(dia, dtime.fromisoformat(plano["inicio"]))
    p_fim = datetime.combine(dia, dtime.fromisoformat(plano["fim"]))
    if p_fim <= p_ini:
        p_fim += timedelta(days=1)
    r_ini, r_fim = datetime.fromisoformat(j["inicio"]), datetime.fromisoformat(j["fim"])
    atraso = round((r_ini - p_ini).total_seconds() / 60)
    saida = round((r_fim - p_fim).total_seconds() / 60)
    sit = "no_horario" if abs(atraso) <= 15 and abs(saida) <= 15 else "atrasou" if atraso > 15 else "fora_do_horario"
    return {"situacao": sit, "planejado": f"{plano['inicio']}–{plano['fim']}", "atraso_min": atraso, "saida_min": saida}


# ------------------------------- Rotas -------------------------------------


@router.get("/dia")
async def jornada_do_dia(group_id: int = Query(...), dia: date = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _dia(group_id, dia)
    esc = _escalas(group_id, dia, dia)
    jus = _justificativas(group_id, dia, dia)
    linhas = []
    ids = set(d["motoristas"]) | {k[0] for k in esc}
    nomes_esc = {}
    if ids - set(d["motoristas"]):
        rows = await _ler("SELECT id, name FROM mova.driver WHERE id = ANY(CAST(:i AS bigint[]))", {"i": list(ids - set(d["motoristas"]))})
        nomes_esc = {r["id"]: r["name"] for r in rows}
    for drv in ids:
        j = d["motoristas"].get(drv)
        p = esc.get((drv, dia.isoformat()))
        linhas.append({
            "driver_id": drv, "nome": (j or {}).get("nome") or nomes_esc.get(drv) or f"Motorista {drv}",
            "jornada": j, "escala": _comparar(p, j, dia), "justificativa": jus.get((drv, dia.isoformat())),
        })
    linhas.sort(key=lambda x: (-len((x["jornada"] or {}).get("infracoes", [])), -((x["jornada"] or {}).get("jornada_h") or 0)))
    total_inf = defaultdict(int)
    for l in linhas:
        if (l["jornada"] or {}).get("a_conferir"):
            continue
        for i in (l["jornada"] or {}).get("infracoes", []):
            total_inf[i["regra"]] += 1
    return {
        "dia": dia.isoformat(), "operacao": d["operacao"], "regra": d["regra"], "pausa_min": PAUSA_MIN, "motivos": MOTIVOS,
        "totais": {
            "motoristas": sum(1 for l in linhas if l["jornada"]),
            "com_infracao": sum(1 for l in linhas if (l["jornada"] or {}).get("infracoes") and not l["jornada"].get("a_conferir")),
            "a_conferir": sum(1 for l in linhas if (l["jornada"] or {}).get("a_conferir")),
            "trechos_sem_identificacao": d["sem_identificacao"],
            "infracoes": dict(total_inf),
            "extra_h": round(sum((l["jornada"] or {}).get("extra_h", 0) for l in linhas), 1),
            "faltas": sum(1 for l in linhas if (l["escala"] or {}).get("situacao") == "falta"),
            "fora_escala": sum(1 for l in linhas if (l["escala"] or {}).get("situacao") in ("atrasou", "fora_do_horario")),
            "escalados": sum(1 for k in esc),
        },
        "linhas": linhas,
    }


@router.get("/espelho")
async def espelho_de_ponto(
    group_id: int = Query(...),
    driver_id: int = Query(...),
    inicio: date = Query(...),
    fim: date = Query(...),
    user=Depends(require_permission("reports", "read")),
):
    """Espelho de ponto de um motorista no período (até 31 dias)."""
    _grupo_ok(user, group_id)
    if fim < inicio or (fim - inicio).days > 31:
        raise HTTPException(422, "Escolha um período de até 31 dias.")
    esc = _escalas(group_id, inicio, fim)
    jus = _justificativas(group_id, inicio, fim)
    dias = []
    d = inicio
    nome = None
    while d <= fim:
        j = (await _dia(group_id, d))["motoristas"].get(driver_id)
        if j:
            nome = j["nome"]
        dias.append({"dia": d.isoformat(), "jornada": j, "escala": _comparar(esc.get((driver_id, d.isoformat())), j, d),
                     "justificativa": jus.get((driver_id, d.isoformat()))})
        d += timedelta(days=1)
    trab = [x["jornada"] for x in dias if x["jornada"]]
    return {
        "driver_id": driver_id, "nome": nome, "inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": dias,
        "totais": {
            "dias_trabalhados": len(trab),
            "jornada_h": round(sum(j["jornada_h"] for j in trab), 2),
            "direcao_h": round(sum(j["direcao_h"] for j in trab), 2),
            "extra_h": round(sum(j["extra_h"] for j in trab), 2),
            "noturno_h": round(sum(j["noturno_h"] for j in trab), 2),
            "infracoes": sum(len(j["infracoes"]) for j in trab),
            "faltas": sum(1 for x in dias if (x["escala"] or {}).get("situacao") == "falta" and not (x["justificativa"] or {}).get("folga")),
        },
    }


class Escala(BaseModel):
    group_id: int
    driver_id: int
    dias: list[date] = Field(..., min_length=1, max_length=62)
    inicio: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    fim: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    unit_id: Optional[int] = None
    linha: Optional[str] = Field(None, max_length=40)
    obs: Optional[str] = Field(None, max_length=200)


@router.post("/escala")
async def salvar_escala(e: Escala, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, e.group_id)
    agora = datetime.now().isoformat(timespec="seconds")
    with _trava, _con() as c:
        for d in e.dias:
            c.execute(
                """INSERT INTO escala (group_id, driver_id, dia, inicio, fim, unit_id, linha, obs, atualizado_em, atualizado_por)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (group_id, driver_id, dia) DO UPDATE SET inicio=excluded.inicio, fim=excluded.fim,
                     unit_id=excluded.unit_id, linha=excluded.linha, obs=excluded.obs, atualizado_em=excluded.atualizado_em,
                     atualizado_por=excluded.atualizado_por""",
                (e.group_id, e.driver_id, d.isoformat(), e.inicio, e.fim, e.unit_id, e.linha, e.obs, agora, user.user_id),
            )
    return {"dias": len(e.dias)}


@router.delete("/escala", status_code=204)
async def apagar_escala(group_id: int = Query(...), driver_id: int = Query(...), dia: date = Query(...),
                        user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _trava, _con() as c:
        c.execute("DELETE FROM escala WHERE group_id = ? AND driver_id = ? AND dia = ?", (group_id, driver_id, dia.isoformat()))


@router.get("/escala")
async def ver_escala(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                     user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    if (fim - inicio).days > 62:
        raise HTTPException(422, "Até 62 dias.")
    return list(_escalas(group_id, inicio, fim).values())


class Justificativa(BaseModel):
    group_id: int
    driver_id: int
    dia: date
    motivo: str = Field(..., min_length=2, max_length=60)
    texto: Optional[str] = Field(None, max_length=500)
    folga: bool = False


@router.post("/justificativa")
async def justificar(j: Justificativa, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, j.group_id)
    with _trava, _con() as c:
        c.execute(
            """INSERT INTO justificativa (group_id, driver_id, dia, motivo, texto, folga, registrado_em, registrado_por)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (group_id, driver_id, dia) DO UPDATE SET motivo=excluded.motivo, texto=excluded.texto,
                 folga=excluded.folga, registrado_em=excluded.registrado_em, registrado_por=excluded.registrado_por""",
            (j.group_id, j.driver_id, j.dia.isoformat(), j.motivo, j.texto, int(j.folga), datetime.now().isoformat(timespec="seconds"), user.user_id),
        )
    return {"ok": True}


@router.get("/motoristas")
async def motoristas(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Motoristas ativos do grupo, para escala e espelho."""
    _grupo_ok(user, group_id)
    rows = await _ler(
        "SELECT id, name AS nome, login AS matricula FROM mova.driver WHERE status = 1 AND group_id = :g ORDER BY name LIMIT 3000",
        {"g": group_id},
    )
    return [dict(r) for r in rows]
