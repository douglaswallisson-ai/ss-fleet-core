"""Áreas de risco e programação de viagens (rota + veículo + horário).

Decisão do PM com o CEO (06/10/2026): nenhum motor de rotas sabe sozinho
quais lugares são perigosos; a roteirização desvia das áreas que NÓS
informamos, o rotograma mostra as que ficam perto da rota e o CCO avisa
quando um veículo entra numa delas.

Origem das áreas, nesta ordem:
1. Cercas do sistema atual com a categoria "Área de Risco" (8) ou
   "Cruzamento Perigoso" (9) de `mova.cerca_category`. As categorias já
   existem no banco, mas em 06/10/2026 nenhuma cerca as usava.
2. Cercas existentes marcadas como risco na plataforma nova.
3. Áreas desenhadas na plataforma nova.

Nível: "evitar" (a roteirização desvia e o CCO avisa) ou "atencao" (só
aparece no rotograma). Cruzamento perigoso entra como "atencao".

⚠️ ARMAZENAMENTO PROVISÓRIO: marcações, áreas desenhadas e programações
ficam em `data/rotas.sqlite` — o banco de produção é só leitura neste
projeto. O destino definitivo natural é a própria `mova.cerca` com a
categoria 8, decisão da engenharia.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica

ARQUIVO = Path(__file__).resolve().parents[2] / "data" / "rotas.sqlite"
_trava = threading.Lock()
_CACHE: dict[tuple, tuple[float, list]] = {}
CACHE_S = 300

CATEGORIA_RISCO = 8
CATEGORIA_CRUZAMENTO = 9
NIVEIS = ("evitar", "atencao")


@contextmanager
def con():
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS area_desenhada (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
            nome TEXT NOT NULL, nivel TEXT NOT NULL, motivo TEXT, anel TEXT NOT NULL,
            criado_por INTEGER, criado_por_nome TEXT, criado_em TEXT, ativo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS cerca_marcada (cerca_id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL,
            nivel TEXT NOT NULL, motivo TEXT, por INTEGER, por_nome TEXT, em TEXT);
        CREATE TABLE IF NOT EXISTS programacao (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL, nome TEXT NOT NULL, pontos TEXT NOT NULL, trajeto TEXT NOT NULL,
            dias TEXT NOT NULL, hora_ini TEXT NOT NULL, hora_fim TEXT NOT NULL,
            tolerancia_m INTEGER DEFAULT 300, desvio_min INTEGER DEFAULT 2, parada_max_min INTEGER DEFAULT 10,
            ativo INTEGER DEFAULT 1, criado_por INTEGER, criado_por_nome TEXT, criado_em TEXT);
        """
    )
    try:
        with c:
            yield c
    finally:
        c.close()


def limpar_cache():
    _CACHE.clear()


def agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# Cerca do sistema atual em anel [lng, lat]. Os polígonos estão gravados com
# latitude no X (16.272 cercas conferidas em 06/10/2026): só o círculo usa as
# colunas latitude/longitude, e a "linha" (tipo 4) vira um corredor de 50 m.
GEO_CERCA = """
CASE WHEN c.polygroup_type = 1 THEN
         ST_Buffer(ST_SetSRID(ST_MakePoint(c.longitude::float, c.latitude::float), 4326)::geography,
                   greatest(coalesce(c.radius, 100), 30))::geometry
     WHEN c.polygon IS NULL THEN NULL
     WHEN c.polygroup_type = 4 THEN
         ST_Buffer((CASE WHEN ST_X(ST_Centroid(c.polygon)) < -34 THEN c.polygon ELSE ST_FlipCoordinates(c.polygon) END)::geography, 50)::geometry
     ELSE CASE WHEN ST_X(ST_Centroid(c.polygon)) < -34 THEN c.polygon ELSE ST_FlipCoordinates(c.polygon) END
END
"""


def aneis_do_geojson(gj: dict) -> list[list[list[float]]]:
    if not gj:
        return []
    if gj.get("type") == "Polygon":
        return [gj["coordinates"][0]]
    if gj.get("type") == "MultiPolygon":
        return [p[0] for p in gj["coordinates"]]
    return []


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


async def cercas_com_geometria(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = await _ler(
        f"""SELECT c.id, c.name, c.group_id, ST_AsGeoJSON(ST_SimplifyPreserveTopology(({GEO_CERCA}), 0.00005), 6) AS geo
            FROM mova.cerca c WHERE c.id = ANY(CAST(:ids AS int[]))""",
        {"ids": ids},
    )
    return {r["id"]: r for r in rows}


async def areas(grupos: Optional[list[int]]) -> list[dict]:
    """Áreas de risco dos grupos (None = todos, só a SS)."""
    k = (tuple(grupos) if grupos else None,)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    filtro = " AND c.group_id = ANY(CAST(:g AS int[]))" if grupos else ""
    cat = await _ler(
        f"""SELECT c.id, c.name, c.group_id, cca.category_id,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(({GEO_CERCA}), 0.00005), 6) AS geo
            FROM mova.cerca c JOIN mova.cerca_category_account cca ON cca.id = c.cerca_category_account_id
            WHERE c.status = 1 AND cca.category_id IN ({CATEGORIA_RISCO}, {CATEGORIA_CRUZAMENTO}) {filtro}""",
        {"g": grupos or []},
    )
    with _trava, con() as c:
        sg = " WHERE group_id IN (%s)" % ",".join("?" * len(grupos)) if grupos else ""
        marcadas = [dict(r) for r in c.execute("SELECT * FROM cerca_marcada" + sg, tuple(grupos or ()))]
        desenhadas = [dict(r) for r in c.execute(
            "SELECT * FROM area_desenhada WHERE ativo = 1" + (" AND group_id IN (%s)" % ",".join("?" * len(grupos)) if grupos else ""),
            tuple(grupos or ()))]
    geo_marc = await cercas_com_geometria([m["cerca_id"] for m in marcadas])

    out: list[dict] = []
    vistos: set[int] = set()
    for m in marcadas:
        g = geo_marc.get(m["cerca_id"])
        if not g or not g["geo"]:
            continue
        vistos.add(m["cerca_id"])
        out.append({"chave": f"cerca:{m['cerca_id']}", "cerca_id": m["cerca_id"], "nome": (g["name"] or "").strip() or f"Cerca {m['cerca_id']}",
                    "group_id": m["group_id"], "nivel": m["nivel"], "motivo": m["motivo"], "origem": "marcada",
                    "aneis": aneis_do_geojson(json.loads(g["geo"]))})
    for r in cat:
        if r["id"] in vistos or not r["geo"]:
            continue
        risco = r["category_id"] == CATEGORIA_RISCO
        out.append({"chave": f"cerca:{r['id']}", "cerca_id": r["id"], "nome": (r["name"] or "").strip() or f"Cerca {r['id']}",
                    "group_id": r["group_id"], "nivel": "evitar" if risco else "atencao",
                    "motivo": "Área de Risco (categoria da cerca)" if risco else "Cruzamento Perigoso (categoria da cerca)",
                    "origem": "categoria", "aneis": aneis_do_geojson(json.loads(r["geo"]))})
    for d in desenhadas:
        out.append({"chave": f"area:{d['id']}", "area_id": d["id"], "nome": d["nome"], "group_id": d["group_id"], "nivel": d["nivel"],
                    "motivo": d["motivo"], "origem": "desenhada", "aneis": [json.loads(d["anel"])],
                    "criado_por": d["criado_por_nome"], "criado_em": d["criado_em"]})
    out = [a for a in out if a["aneis"]]
    _CACHE[k] = (time.time(), out)
    return out


# ------------------------------------------------------------- programação

def _prog(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["pontos"] = json.loads(d["pontos"])
    d["trajeto"] = json.loads(d["trajeto"])
    d["dias"] = [int(x) for x in d["dias"].split(",") if x != ""]
    d["ativo"] = bool(d["ativo"])
    return d


def programacoes(grupos: Optional[list[int]], so_ativas: bool = False) -> list[dict]:
    with _trava, con() as c:
        sql, args = "SELECT * FROM programacao WHERE 1=1", []
        if grupos:
            sql += " AND group_id IN (%s)" % ",".join("?" * len(grupos))
            args += grupos
        if so_ativas:
            sql += " AND ativo = 1"
        return [_prog(r) for r in c.execute(sql + " ORDER BY id DESC", args)]


def em_execucao(p: dict, agora: Optional[datetime] = None) -> bool:
    """A programação vale agora? Dias 0 = segunda … 6 = domingo; faixa que cruza a meia-noite vale."""
    agora = agora or datetime.now()
    hm = agora.strftime("%H:%M")
    ini, fim = p["hora_ini"], p["hora_fim"]
    if ini <= fim:
        return agora.weekday() in p["dias"] and ini <= hm <= fim
    # Ex.: 22:00 → 06:00. Depois da meia-noite vale o dia em que começou.
    if hm >= ini:
        return agora.weekday() in p["dias"]
    return hm <= fim and (agora.weekday() - 1) % 7 in p["dias"]
