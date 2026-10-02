"""
Linha do tempo de eventos — por veículo ou por motorista, em tempo real.

Fontes (só leitura):
- Eventos: `mova.dev_status_30` (cada posição traz o código do evento do
  equipamento) + nome em `mova.tracker_event`. Não usa `mova.heatmap`, que
  chega com atraso e fica vazio nas últimas horas.
- Viagens (barras): `mova.con_telemetry` (trechos com início, fim e motorista).

Marcas técnicas do equipamento (posição periódica, mudança de rumo, ignição,
início/fim de viagem, "retornou velocidade", sensor de chuva, ficou online…)
não viram linha de evento: são registro, não ocorrência de condução.
A consulta usa o índice (unit_id, local_time) do histórico de posições.
"""

import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

#: Códigos que são registro técnico, não evento de condução.
TECNICOS = {1, 5, 6, 8, 40, 41, 42, 43, 47, 60, 61, 102, 145, 146, 156, 157, 274, 360, 391, 392, 403}
#: Gravidade para a cor da linha (o resto é "atenção").
CRITICOS = {7, 9, 13, 27, 37, 48, 153, 163, 288}
LEVES = {74, 159, 161, 361, 393}
MAX_HORAS = 72
_CACHE: dict[tuple, tuple[float, dict]] = {}


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


@router.get("")
async def linha_do_tempo(
    group_id: Optional[int] = Query(None),
    inicio: Optional[datetime] = Query(None, description="Início da janela (padrão: agora − horas)"),
    horas: int = Query(24, ge=1, le=MAX_HORAS),
    unit_id: Optional[int] = Query(None),
    driver_id: Optional[int] = Query(None),
    agrupar: Literal["veiculo", "motorista"] = Query("veiculo"),
    user=Depends(require_permission("reports", "read")),
):
    fim = (inicio + timedelta(hours=horas)) if inicio else datetime.now()
    ini = inicio or fim - timedelta(hours=horas)
    chave = (group_id, ini.replace(second=0, microsecond=0), horas, unit_id, driver_id, agrupar, user.user_id)
    if (c := _CACHE.get(chave)) and time.time() - c[0] < 60:
        return c[1]

    grupos, subgrupos = escopo_do_usuario(user)
    esc, p = clausula_escopo(grupos, subgrupos, alias="tu")
    if group_id:
        esc += " AND tu.group_id = :g"
        p["g"] = group_id
    if unit_id:
        esc += " AND tu.id = :u"
        p["u"] = unit_id
    veiculos = await _ler(
        f"SELECT tu.id, tu.label AS placa, tu.label2 AS prefixo, tu.model AS modelo, tu.unit_category_id AS categoria_id "
        f"FROM mova.tracked_unit tu WHERE tu.status = 1 {esc}",
        p,
    )
    if not veiculos:
        return {"inicio": ini.isoformat(), "fim": fim.isoformat(), "linhas": [], "tipos": []}
    if len(veiculos) > 600:
        raise HTTPException(422, "Escolha um grupo ou um veículo: são muitos veículos para uma linha do tempo só.")
    ids = [v["id"] for v in veiculos]
    pv = {"ids": ids, "i": ini, "f": fim}
    filtro_mot = " AND COALESCE(p.driver_id, 0) = :d" if driver_id is not None else ""
    if driver_id is not None:
        pv["d"] = driver_id

    eventos = await _ler(
        f"""SELECT p.unit_id, p.local_time, p.tracker_event_id AS cod, COALESCE(p.driver_id, 0) AS driver_id,
                   NULLIF(NULLIF(TRIM(p.driver_name), ''), 'NULL') AS motorista, p.speed
            FROM mova.dev_status_30 p
            WHERE p.unit_id = ANY(CAST(:ids AS bigint[])) AND p.local_time >= :i AND p.local_time < :f
              AND p.tracker_event_id IS NOT NULL AND NOT (p.tracker_event_id = ANY(CAST(:tec AS bigint[]))) {filtro_mot}""",
        {**pv, "tec": sorted(TECNICOS)},
    )
    filtro_mot_v = " AND COALESCE(ct.driver_id, 0) = :d" if driver_id is not None else ""
    viagens = await _ler(
        f"""SELECT ct.unit_id, ct.start_time, COALESCE(ct.end_time, LEAST(now()::timestamp, :f)) AS end_time,
                   COALESCE(ct.driver_id, 0) AS driver_id, NULLIF(NULLIF(TRIM(ct.driver_name), ''), 'NULL') AS motorista,
                   ct.distance_traveled AS dist, ct.trip_status AS produtiva
            FROM mova.con_telemetry ct
            WHERE ct.unit_id = ANY(CAST(:ids AS bigint[])) AND ct.start_time >= :i0 AND ct.start_time < :f
              AND COALESCE(ct.end_time, now()::timestamp) > :i {filtro_mot_v}""",
        {**pv, "i0": ini - timedelta(hours=12)},
    )
    nomes = {r["id"]: r["name"] for r in await _ler(
        "SELECT id, name FROM mova.tracker_event WHERE id = ANY(CAST(:c AS bigint[]))", {"c": sorted({e["cod"] for e in eventos}) or [0]})}

    vinfo = {v["id"]: v for v in veiculos}
    # Cadastros-coringa ("Não Informado") contam como sem motorista identificado.
    coringa = re.compile(r"^\s*n[aã]o\s+(informado|identificado)\s*$", re.I)
    eventos = [dict(r) for r in eventos]
    viagens = [dict(r) for r in viagens]
    for r in eventos + viagens:
        if r["motorista"] and coringa.match(r["motorista"]):
            r["driver_id"], r["motorista"] = 0, None
    mot_nome: dict[int, str] = {}
    for r in eventos + viagens:
        if r["driver_id"] and r["motorista"]:
            mot_nome[r["driver_id"]] = r["motorista"]

    def chave_linha(r) -> tuple:
        return ("v", r["unit_id"]) if agrupar == "veiculo" else ("m", r["driver_id"])

    linhas: dict[tuple, dict] = {}

    def linha(k: tuple) -> dict:
        if k not in linhas:
            if k[0] == "v":
                v = vinfo[k[1]]
                titulo = " · ".join(x.strip() for x in (v["prefixo"], v["placa"]) if x and x.strip()) or f"#{k[1]}"
                linhas[k] = {"id": k[1], "titulo": titulo, "sub": (v["modelo"] or "").strip() or None,
                             "categoria_id": v["categoria_id"], "viagens": [], "eventos": defaultdict(list), "outros": set()}
            else:
                titulo = mot_nome.get(k[1]) or ("Sem motorista identificado" if k[1] == 0 else f"Motorista {k[1]}")
                linhas[k] = {"id": k[1], "titulo": titulo, "sub": None, "categoria_id": None,
                             "viagens": [], "eventos": defaultdict(list), "outros": set()}
        return linhas[k]

    def minutos(t: datetime) -> float:
        return round((t - ini).total_seconds() / 60, 1)

    for r in viagens:
        a, b = max(r["start_time"], ini), min(r["end_time"], fim)
        if b <= a:
            continue
        L = linha(chave_linha(r))
        L["viagens"].append({"de": minutos(a), "ate": minutos(b), "motorista": mot_nome.get(r["driver_id"]),
                             "produtiva": bool(r["produtiva"])})
        L["outros"].add(r["unit_id"] if agrupar == "motorista" else r["driver_id"])
    tipos_total: dict[int, int] = defaultdict(int)
    for e in eventos:
        L = linha(chave_linha(e))
        L["eventos"][e["cod"]].append(minutos(e["local_time"]))
        tipos_total[e["cod"]] += 1
        L["outros"].add(e["unit_id"] if agrupar == "motorista" else e["driver_id"])

    def gravidade(c: int) -> str:
        return "critico" if c in CRITICOS else "leve" if c in LEVES else "atencao"

    saida_linhas = []
    for L in linhas.values():
        outros = [o for o in L["outros"] if o]
        if agrupar == "motorista":
            sub = ", ".join(sorted({" · ".join(x.strip() for x in (vinfo[o]["prefixo"], vinfo[o]["placa"]) if x and x.strip()) for o in outros if o in vinfo})[:3])
        else:
            sub_m = sorted({mot_nome.get(o, f"Motorista {o}") for o in outros})
            sub = L["sub"] if not sub_m else f"{L['sub'] + ' · ' if L['sub'] else ''}{', '.join(sub_m[:2])}{'…' if len(sub_m) > 2 else ''}"
        ev = [{"cod": c, "nome": (nomes.get(c) or f"Evento {c}").strip(), "gravidade": gravidade(c), "min": sorted(ms)}
              for c, ms in L["eventos"].items()]
        ev.sort(key=lambda x: (-len(x["min"])))
        total = sum(len(x["min"]) for x in ev)
        minutos_viagem = sum(v["ate"] - v["de"] for v in L["viagens"])
        saida_linhas.append({
            "id": L["id"], "titulo": L["titulo"], "sub": sub or None, "categoria_id": L["categoria_id"],
            "viagens": sorted(L["viagens"], key=lambda v: v["de"]), "eventos": ev, "total_eventos": total,
            "criticos": sum(len(x["min"]) for x in ev if x["gravidade"] == "critico"),
            "minutos_em_viagem": round(minutos_viagem),
            "eventos_por_hora": round(total / (minutos_viagem / 60), 1) if minutos_viagem >= 30 else None,
        })
    saida_linhas.sort(key=lambda x: (-x["criticos"], -x["total_eventos"], -x["minutos_em_viagem"]))
    saida = {
        "inicio": ini.isoformat(), "fim": fim.isoformat(), "minutos": round((fim - ini).total_seconds() / 60),
        "agrupar": agrupar,
        "tipos": sorted(({"cod": c, "nome": (nomes.get(c) or f"Evento {c}").strip(), "gravidade": gravidade(c), "total": n}
                         for c, n in tipos_total.items()), key=lambda x: -x["total"]),
        "linhas": saida_linhas,
        "motoristas": sorted(({"id": k, "nome": v} for k, v in mot_nome.items()), key=lambda x: x["nome"]),
    }
    _CACHE[chave] = (time.time(), saida)
    return saida
