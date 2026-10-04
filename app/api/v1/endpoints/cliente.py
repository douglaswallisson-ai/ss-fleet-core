"""
Módulos de cada cliente (segmento): Frota (carga) é de todos; Urbano e
Fretamento só aparecem para quem opera nesse nicho. Decisão do PM (04/10/2026):
"cada cliente desse nicho terá o seu módulo — a FERTRAN verá somente frota".

Não há campo de segmento no cadastro (mova."group"), então o segmento sai dos
dados:
- Urbano: ônibus rodando em linha nos últimos 15 dias (con_telemetry.line_number);
- Fretamento: frota com 5 ou mais ônibus/micro e sem linha urbana rodando.
SUPOSIÇÃO: a regra acerta os clientes conferidos em 04/10/2026 (Fênix urbano;
VTR e Quataí fretamento; FERTRAN, RCA, Figueiredo só frota; JTP frota +
urbano). A SS pode corrigir cliente a cliente (PUT /cliente/modulos), gravado
no armazenamento provisório `data/cliente.sqlite`.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "cliente.sqlite"
_trava = threading.Lock()
_CACHE: dict[int, tuple[float, dict]] = {}
CATEGORIAS_ONIBUS = (12, 22)


def _con():
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS modulos (group_id INTEGER PRIMARY KEY, dados TEXT NOT NULL, autor INTEGER, em TEXT)")
    return c


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


async def _inferir(g: int) -> dict:
    hit = _CACHE.get(g)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    async with AsyncSessionLocalReplica() as db:
        v = (await db.execute(text(
            "SELECT count(*) AS n, count(*) FILTER (WHERE unit_category_id = ANY(:cats)) AS onibus "
            "FROM mova.tracked_unit WHERE group_id = :g AND status = 1"), {"g": g, "cats": list(CATEGORIAS_ONIBUS)})).mappings().first()
        linhas = (await db.execute(text(
            "SELECT count(DISTINCT ct.line_number) FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id "
            "WHERE tu.group_id = :g AND ct.start_time > now() - interval '15 days' AND ct.line_number > 0"), {"g": g})).scalar() or 0
    urbano = linhas > 0
    d = {"frota": True, "urbano": urbano, "fretamento": (v["onibus"] or 0) >= 5 and not urbano,
         "base": {"veiculos": v["n"], "onibus": v["onibus"], "linhas_rodando_15d": linhas}}
    _CACHE[g] = (time.time(), d)
    return d


@router.get("/modulos")
async def modulos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _inferir(group_id)
    with _trava, _con() as c:
        r = c.execute("SELECT dados FROM modulos WHERE group_id = ?", (group_id,)).fetchone()
    if r:
        d = {**d, **json.loads(r["dados"]), "ajustado": True}
    return d


class Modulos(BaseModel):
    urbano: Optional[bool] = None
    fretamento: Optional[bool] = None


@router.put("/modulos")
async def ajustar(m: Modulos, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Só a SS ajusta o segmento de um cliente."""
    if not getattr(user, "is_super_admin", False):
        raise HTTPException(403, "Só a SS ajusta os módulos de um cliente.")
    dados = {k: v for k, v in m.model_dump().items() if v is not None}
    with _trava, _con() as c:
        if dados:
            c.execute("INSERT OR REPLACE INTO modulos (group_id, dados, autor, em) VALUES (?,?,?,datetime('now','localtime'))",
                      (group_id, json.dumps(dados), getattr(user, "user_id", None)))
        else:
            c.execute("DELETE FROM modulos WHERE group_id = ?", (group_id,))
    return {"ok": True}


# ------------------------------------------------------------------ logo

@router.get("/logo")
async def logo(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Logo do cliente: a enviada no cadastro do grupo (Cadastros > Grupos, provisória)
    ou, se a conta é só deste cliente, a `account.imglogo`. Decisão do PM
    (02/10/2026): sem logo, o cliente envia. Todas as empresas da conta 539
    dividem a mesma logo (a da SS), por isso a conta compartilhada não vale."""
    _grupo_ok(user, group_id)
    from app.api.v1.endpoints import cadastros
    for r in cadastros._overlay("empresa", group_id):
        d = json.loads(r["dados"])
        if d.get("logo"):
            return {"logo": d["logo"], "origem": "cadastro"}
    async with AsyncSessionLocalReplica() as db:
        r = (await db.execute(text(
            'SELECT a.imglogo, (SELECT count(*) FROM mova."group" g2 WHERE g2.account_id = a.id) AS grupos '
            'FROM mova."group" g JOIN mova.account a ON a.id = g.account_id WHERE g.id = :g'), {"g": group_id})).mappings().first()
    if r and r["imglogo"] and r["grupos"] == 1:
        return {"logo": r["imglogo"], "origem": "conta"}
    return {"logo": None, "origem": None}
