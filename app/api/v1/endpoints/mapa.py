"""
Camadas do mapa: cercas (`mova.cerca`) e pontos de interesse (`mova.poi`).

Vault (Telas/Cerca): cerca tipo 1 = circular (centro + raio em metros);
2 e 3 = polígono; 4 = linha.

⚠️ No banco, os polígonos estão gravados com X = latitude e Y = longitude
(ordem trocada em relação ao padrão) e o centro (latitude/longitude) fica 0.
A busca usa as duas ordens e a resposta entrega `pontos` já como
[latitude, longitude], conferindo cada geometria. Só leitura; sempre dentro do escopo do usuário
e da área visível do mapa (índices geográficos das duas tabelas).
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

LIMITE = 3000


def _lat_lon(x: float, y: float) -> list[float]:
    """Par em [lat, lon]: no Brasil a latitude vai de +6 a -34 e a longitude de -74 a -28."""
    if -35 <= x <= 7 and -75 <= y <= -27:
        return [round(x, 6), round(y, 6)]
    return [round(y, 6), round(x, 6)]


def _pontos(geo: dict) -> list:
    """Anéis do polígono (ou a linha) como listas de [lat, lon]."""
    tipo, c = geo.get("type"), geo.get("coordinates") or []
    if tipo == "Polygon":
        return [[_lat_lon(*p[:2]) for p in anel] for anel in c[:1]]
    if tipo == "MultiPolygon":
        return [[_lat_lon(*p[:2]) for p in pol[0]] for pol in c]
    if tipo == "LineString":
        return [[_lat_lon(*p[:2]) for p in c]]
    return []


def _grupos(user, group_id: Optional[int]) -> Optional[list[int]]:
    """Grupos a mostrar: o escolhido (se no acesso) ou todos do acesso; None = sem restrição (master)."""
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return [group_id] if group_id else None
    grupos, _ = escopo_do_usuario(user)
    permitidos = [g for g in grupos if g and g > 0]
    if group_id:
        return [group_id] if group_id in permitidos else [-1]
    return permitidos or [-1]


@router.get("/camadas")
async def camadas(
    min_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lat: float = Query(...),
    max_lon: float = Query(...),
    group_id: Optional[int] = Query(None),
    pois: bool = Query(True),
    cercas: bool = Query(True),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    grupos = _grupos(current_user, group_id)
    filtro = "" if grupos is None else " AND group_id = ANY(CAST(:g AS integer[]))"
    p = {"a": min_lon, "b": min_lat, "c": max_lon, "d": max_lat, "g": grupos or [], "lim": LIMITE + 1}
    saida: dict = {"pois": [], "cercas": [], "truncado": False}
    if pois:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT id, name, latitude::float AS lat, longitude::float AS lon, radius, color, description
                    FROM mova.poi
                    WHERE status = 1 {filtro}
                      AND latitude BETWEEN :b AND :d AND longitude BETWEEN :a AND :c
                    LIMIT :lim
                    """
                ),
                p,
            )
        ).mappings().all()
        saida["truncado"] |= len(rows) > LIMITE
        saida["pois"] = [
            {"id": r["id"], "nome": (r["name"] or "").strip(), "lat": r["lat"], "lon": r["lon"], "raio": r["radius"], "cor": r["color"], "descricao": r["description"]}
            for r in rows[:LIMITE]
        ]
    if cercas:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT id, name, polygroup_type AS tipo, color, radius, speed, description,
                           latitude::float AS lat, longitude::float AS lon,
                           CASE WHEN polygroup_type <> 1 AND polygon IS NOT NULL
                                THEN ST_AsGeoJSON(ST_SimplifyPreserveTopology(polygon, 0.00003), 6) END AS geo
                    FROM mova.cerca
                    WHERE status = 1 {filtro}
                      AND (
                        (polygroup_type = 1 AND latitude BETWEEN :b AND :d AND longitude BETWEEN :a AND :c)
                        OR (polygroup_type <> 1 AND (polygon && ST_MakeEnvelope(:b, :a, :d, :c, 4326)
                                                     OR polygon && ST_MakeEnvelope(:a, :b, :c, :d, 4326)))
                      )
                    LIMIT :lim
                    """
                ),
                p,
            )
        ).mappings().all()
        saida["truncado"] |= len(rows) > LIMITE
        saida["cercas"] = [
            {
                "id": r["id"],
                "nome": (r["name"] or "").strip(),
                "tipo": "circular" if r["tipo"] == 1 else "linha" if r["tipo"] == 4 else "poligono",
                "cor": r["color"],
                "raio": r["radius"],
                "velocidade_max": r["speed"],
                "descricao": r["description"],
                "lat": r["lat"],
                "lon": r["lon"],
                "pontos": _pontos(json.loads(r["geo"])) if r["geo"] else None,
            }
            for r in rows[:LIMITE]
        ]
    return saida
