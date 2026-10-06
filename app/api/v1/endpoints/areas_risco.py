"""Áreas de risco e programação de viagens. Regras e origem em app/core/areas_risco.py.

Rotas (/areas-risco):
- GET    /?group_id                 áreas de risco do cliente (cercas com categoria, marcadas e desenhadas)
- GET    /cercas?group_id&busca     cercas do cliente, para marcar como risco
- PUT    /cercas/{cerca_id}         marca (nivel) ou desmarca (nivel = null) uma cerca
- POST   /                          área desenhada
- PUT    /{id}  · DELETE /{id}      edita / desativa área desenhada
- GET/POST /programacao · PUT/DELETE /programacao/{id}   veículo + rota + dias e horário
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import areas_risco as ar
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()


def _ss(user) -> bool:
    return bool(getattr(user, "master", 0) or getattr(user, "is_super_admin", False))


def _grupo_ok(user, group_id: int):
    if _ss(user):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a esta empresa.")


def _quem(user) -> tuple[Optional[int], str]:
    return getattr(user, "user_id", None), getattr(user, "email", None) or str(getattr(user, "user_id", ""))


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


def _para_tela(a: dict) -> dict:
    """Anéis [lng, lat] → pontos [lat, lng], como o mapa desenha."""
    return {**{k: v for k, v in a.items() if k != "aneis"}, "poligonos": [[[p[1], p[0]] for p in anel] for anel in a["aneis"]]}


@router.get("/")
async def listar(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    return {"data": [_para_tela(a) for a in await ar.areas([group_id])]}


@router.get("/cercas")
async def cercas(group_id: int = Query(...), busca: Optional[str] = Query(None, max_length=80),
                 user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    p = {"g": group_id, "b": f"%{busca.strip()}%" if busca and busca.strip() else None}
    rows = await _ler(
        f"""SELECT c.id, c.name AS nome, c.polygroup_type AS tipo, cat.nome AS categoria, cca.category_id,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(({ar.GEO_CERCA}), 0.0001), 5) AS geo
            FROM mova.cerca c
            LEFT JOIN mova.cerca_category_account cca ON cca.id = c.cerca_category_account_id
            LEFT JOIN mova.cerca_category cat ON cat.id = cca.category_id
            WHERE c.status = 1 AND c.group_id = :g AND (CAST(:b AS text) IS NULL OR c.name ILIKE :b)
            ORDER BY c.name LIMIT 1000""",
        p,
    )
    with ar._trava, ar.con() as c:
        marc = {r["cerca_id"]: dict(r) for r in c.execute("SELECT * FROM cerca_marcada WHERE group_id = ?", (group_id,))}
    out = []
    for r in rows:
        m = marc.get(r["id"])
        nivel = m["nivel"] if m else ("evitar" if r["category_id"] == ar.CATEGORIA_RISCO else "atencao" if r["category_id"] == ar.CATEGORIA_CRUZAMENTO else None)
        aneis = ar.aneis_do_geojson(json.loads(r["geo"])) if r["geo"] else []
        out.append({"id": r["id"], "nome": (r["nome"] or "").strip() or f"Cerca {r['id']}",
                    "tipo": "circular" if r["tipo"] == 1 else "linha" if r["tipo"] == 4 else "poligono",
                    "categoria": r["categoria"], "nivel": nivel, "motivo": m["motivo"] if m else None,
                    "por_categoria": not m and nivel is not None,
                    "poligonos": [[[p[1], p[0]] for p in anel] for anel in aneis]})
    return {"data": out, "cortado": len(rows) >= 1000}


class MarcarCerca(BaseModel):
    group_id: int
    nivel: Optional[str] = Field(None, pattern="^(evitar|atencao)$")
    motivo: Optional[str] = Field(None, max_length=300)


@router.put("/cercas/{cerca_id}")
async def marcar_cerca(cerca_id: int, p: MarcarCerca, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    r = await _ler("SELECT group_id FROM mova.cerca WHERE id = :c AND status = 1", {"c": cerca_id})
    if not r or r[0]["group_id"] != p.group_id:
        raise HTTPException(404, "Cerca não encontrada nesta empresa.")
    uid, nome = _quem(user)
    with ar._trava, ar.con() as c:
        if p.nivel is None:
            c.execute("DELETE FROM cerca_marcada WHERE cerca_id = ?", (cerca_id,))
        else:
            c.execute("INSERT OR REPLACE INTO cerca_marcada (cerca_id, group_id, nivel, motivo, por, por_nome, em) VALUES (?,?,?,?,?,?,?)",
                      (cerca_id, p.group_id, p.nivel, p.motivo, uid, nome, ar.agora_iso()))
    ar.limpar_cache()
    return {"ok": True}


class AreaDesenhada(BaseModel):
    group_id: int
    nome: str = Field(min_length=2, max_length=120)
    nivel: str = Field("evitar", pattern="^(evitar|atencao)$")
    motivo: Optional[str] = Field(None, max_length=300)
    pontos: list[list[float]] = Field(min_length=3, max_length=500)  # [[lat, lng], ...]


def _anel(pontos: list[list[float]]) -> list[list[float]]:
    for p in pontos:
        if len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180):
            raise HTTPException(422, "Ponto inválido na área.")
    anel = [[round(p[1], 6), round(p[0], 6)] for p in pontos]
    return anel + [anel[0]] if anel[0] != anel[-1] else anel


@router.post("/")
async def criar_area(p: AreaDesenhada, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    uid, nome = _quem(user)
    with ar._trava, ar.con() as c:
        cur = c.execute("INSERT INTO area_desenhada (group_id, nome, nivel, motivo, anel, criado_por, criado_por_nome, criado_em) VALUES (?,?,?,?,?,?,?,?)",
                        (p.group_id, p.nome.strip(), p.nivel, p.motivo, json.dumps(_anel(p.pontos)), uid, nome, ar.agora_iso()))
        novo = cur.lastrowid
    ar.limpar_cache()
    return {"id": novo}


class EditarArea(BaseModel):
    nome: Optional[str] = Field(None, min_length=2, max_length=120)
    nivel: Optional[str] = Field(None, pattern="^(evitar|atencao)$")
    motivo: Optional[str] = Field(None, max_length=300)
    pontos: Optional[list[list[float]]] = Field(None, min_length=3, max_length=500)


def _area(c, area_id: int, user) -> dict:
    r = c.execute("SELECT * FROM area_desenhada WHERE id = ? AND ativo = 1", (area_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Área não encontrada.")
    _grupo_ok(user, r["group_id"])
    return dict(r)


@router.put("/{area_id}")
async def editar_area(area_id: int, p: EditarArea, user=Depends(require_permission("reports", "read"))):
    with ar._trava, ar.con() as c:
        a = _area(c, area_id, user)
        c.execute("UPDATE area_desenhada SET nome = ?, nivel = ?, motivo = ?, anel = ? WHERE id = ?",
                  (p.nome.strip() if p.nome else a["nome"], p.nivel or a["nivel"], p.motivo if p.motivo is not None else a["motivo"],
                   json.dumps(_anel(p.pontos)) if p.pontos else a["anel"], area_id))
    ar.limpar_cache()
    return {"ok": True}


@router.delete("/{area_id}")
async def remover_area(area_id: int, user=Depends(require_permission("reports", "read"))):
    with ar._trava, ar.con() as c:
        _area(c, area_id, user)
        c.execute("UPDATE area_desenhada SET ativo = 0 WHERE id = ?", (area_id,))
    ar.limpar_cache()
    return {"ok": True}


# ------------------------------------------------------------- programação

class Programacao(BaseModel):
    group_id: int
    unit_id: int
    nome: str = Field(min_length=2, max_length=120)
    pontos: list[dict] = Field(min_length=2, max_length=50)          # [{nome, latitude, longitude}]
    trajeto: list[list[float]] = Field(min_length=2, max_length=20_000)  # [[lat, lng], ...] da rota calculada
    dias: list[int] = Field(min_length=1, max_length=7)               # 0 = segunda … 6 = domingo
    hora_ini: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    hora_fim: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tolerancia_m: int = Field(300, ge=50, le=5000)
    desvio_min: int = Field(2, ge=1, le=60)
    parada_max_min: int = Field(10, ge=2, le=240)
    ativo: bool = True


async def _veiculo_do_grupo(unit_id: int, group_id: int):
    r = await _ler("SELECT group_id FROM mova.tracked_unit WHERE id = :u AND status = 1", {"u": unit_id})
    if not r or r[0]["group_id"] != group_id:
        raise HTTPException(422, "Veículo não pertence a esta empresa.")


def _valores(p: Programacao) -> tuple:
    if any(d < 0 or d > 6 for d in p.dias):
        raise HTTPException(422, "Dia da semana inválido.")
    return (p.group_id, p.unit_id, p.nome.strip(), json.dumps(p.pontos, ensure_ascii=False),
            json.dumps([[round(x[0], 6), round(x[1], 6)] for x in p.trajeto if len(x) == 2]),
            ",".join(str(d) for d in sorted(set(p.dias))), p.hora_ini, p.hora_fim, p.tolerancia_m, p.desvio_min, p.parada_max_min, int(p.ativo))


@router.get("/programacao")
async def listar_programacao(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    progs = ar.programacoes([group_id])
    ids = list({x["unit_id"] for x in progs})
    veics = {v["id"]: v for v in await _ler("SELECT id, label AS placa, label2 AS prefixo FROM mova.tracked_unit WHERE id = ANY(CAST(:i AS int[]))", {"i": ids})} if ids else {}
    for x in progs:
        v = veics.get(x["unit_id"]) or {}
        x["veiculo"] = " · ".join(s for s in (v.get("prefixo"), v.get("placa")) if s) or str(x["unit_id"])
        x["em_execucao"] = x["ativo"] and ar.em_execucao(x)
        x["trajeto_pontos"] = len(x["trajeto"])
        x.pop("trajeto")
    return {"data": progs}


@router.get("/programacao/{prog_id}")
async def ver_programacao(prog_id: int, user=Depends(require_permission("reports", "read"))):
    with ar._trava, ar.con() as c:
        r = c.execute("SELECT * FROM programacao WHERE id = ?", (prog_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Programação não encontrada.")
    x = ar._prog(r)
    _grupo_ok(user, x["group_id"])
    return x


@router.post("/programacao")
async def criar_programacao(p: Programacao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    await _veiculo_do_grupo(p.unit_id, p.group_id)
    uid, nome = _quem(user)
    with ar._trava, ar.con() as c:
        cur = c.execute(
            """INSERT INTO programacao (group_id, unit_id, nome, pontos, trajeto, dias, hora_ini, hora_fim, tolerancia_m, desvio_min,
                   parada_max_min, ativo, criado_por, criado_por_nome, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*_valores(p), uid, nome, ar.agora_iso()))
        return {"id": cur.lastrowid}


@router.put("/programacao/{prog_id}")
async def editar_programacao(prog_id: int, p: Programacao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    await _veiculo_do_grupo(p.unit_id, p.group_id)
    with ar._trava, ar.con() as c:
        r = c.execute("SELECT group_id FROM programacao WHERE id = ?", (prog_id,)).fetchone()
        if not r or r["group_id"] != p.group_id:
            raise HTTPException(404, "Programação não encontrada.")
        c.execute("""UPDATE programacao SET group_id=?, unit_id=?, nome=?, pontos=?, trajeto=?, dias=?, hora_ini=?, hora_fim=?,
                     tolerancia_m=?, desvio_min=?, parada_max_min=?, ativo=? WHERE id = ?""", (*_valores(p), prog_id))
    return {"ok": True}


@router.delete("/programacao/{prog_id}")
async def remover_programacao(prog_id: int, user=Depends(require_permission("reports", "read"))):
    with ar._trava, ar.con() as c:
        r = c.execute("SELECT group_id FROM programacao WHERE id = ?", (prog_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Programação não encontrada.")
        _grupo_ok(user, r["group_id"])
        c.execute("DELETE FROM programacao WHERE id = ?", (prog_id,))
    return {"ok": True}
