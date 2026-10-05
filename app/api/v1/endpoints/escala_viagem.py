"""
Escala de Viagem — plano de viagem para a gerenciadora de risco (GR) e
relatório de conformidade (previsto × realizado pelo rastreador).

Decisões de produto (PM, 01/10/2026): a SS não é gerenciadora de risco; é a
fonte de evidência. O entregável central é o relatório de conformidade por
viagem. A posição vem do rastreador, sem digitação dupla.

⚠️ ARMAZENAMENTO PROVISÓRIO: o banco de produção é só leitura neste projeto,
então o plano (o que foi declarado à GR) fica num SQLite local
(`data/escala_viagem.sqlite`). Onde ele vive em produção é decisão da
engenharia. O realizado vem do banco: paradas de `mova.con_stop` e último
sinal de `mova.dev_status`.

Regras desta primeira versão (configuráveis, a validar com o cliente):
- parada conta a partir de PARADA_MIN_MIN minutos parado;
- parada dentro do raio de um ponto autorizado (padrão 300 m) é conforme;
  fora de todos é desvio e abre ocorrência;
- a janela da viagem vai de 2 h antes da saída prevista até a chegada real
  (ou 12 h depois da chegada prevista, se não chegou).
"""

import json
import math
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "escala_viagem.sqlite"
PARADA_MIN_MIN = 10
RAIO_PADRAO_M = 300
_trava = threading.Lock()


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS viagem (
            id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, group_id INTEGER, dados TEXT,
            criado_em TEXT, criado_por INTEGER, atualizado_em TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS rota (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, nome TEXT, dados TEXT, atualizado_em TEXT)"""
    )
    return c


# ------------------------------- Modelos ---------------------------------


class Ponto(BaseModel):
    tipo: str = Field(..., description="origem, destino, posto, lanchonete, pernoite, carga, descarga")
    nome: str
    poi_id: Optional[int] = None
    latitude: float
    longitude: float
    raio_m: int = RAIO_PADRAO_M
    previsto: Optional[str] = Field(None, description="Horário previsto (ISO)")


class Destinatario(BaseModel):
    nome: str
    papel: Optional[str] = None
    email: Optional[str] = None


class ViagemEntrada(BaseModel):
    group_id: int
    unit_id: int
    motorista: Optional[str] = None
    driver_id: Optional[int] = None
    rota_nome: Optional[str] = None
    rodovia: Optional[str] = None
    carga_cliente: Optional[str] = None
    carga_descricao: Optional[str] = None
    carga_valor: Optional[float] = None
    saida_prevista: datetime
    chegada_prevista: datetime
    pontos: list[Ponto]
    destinatarios: list[Destinatario] = []
    status_gr: str = "aguardando"
    observacao: Optional[str] = None


class ViagemAlteracao(BaseModel):
    status_gr: Optional[str] = None
    destinatarios: Optional[list[Destinatario]] = None
    pontos: Optional[list[Ponto]] = None
    observacao: Optional[str] = None
    cancelada: Optional[bool] = None


class RotaEntrada(BaseModel):
    group_id: int
    nome: str
    pontos: list[Ponto]
    destinatarios: list[Destinatario] = []


# ------------------------------- Apoio -----------------------------------


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12_742_000 * math.asin(math.sqrt(max(0.0, a)))


def _grupos_permitidos(user) -> Optional[set[int]]:
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return None
    grupos, _ = escopo_do_usuario(user)
    return {g for g in grupos if g and g > 0}


def _checar_grupo(user, group_id: int):
    p = _grupos_permitidos(user)
    if p is not None and group_id not in p:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a esta empresa")


def _ler_viagens(group_id: Optional[int], dia_ini: Optional[datetime], dia_fim: Optional[datetime]) -> list[dict]:
    with _trava, _con() as c:
        rows = c.execute("SELECT * FROM viagem" + (" WHERE group_id = ?" if group_id else ""), (group_id,) if group_id else ()).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["dados"])
        d.update(id=r["id"], codigo=r["codigo"], criado_em=r["criado_em"], atualizado_em=r["atualizado_em"])
        sp = datetime.fromisoformat(d["saida_prevista"])
        if dia_ini and (sp < dia_ini or sp >= dia_fim):
            continue
        out.append(d)
    return out


async def _realizado(db: AsyncSession, v: dict) -> dict:
    """Paradas do rastreador na janela da viagem, classificadas contra os pontos autorizados."""
    sp = datetime.fromisoformat(v["saida_prevista"])
    cp = datetime.fromisoformat(v["chegada_prevista"])
    agora = datetime.now()
    ini, fim = sp - timedelta(hours=2), min(agora, cp + timedelta(hours=12))
    trechos = (
        await db.execute(
            text(
                """
                SELECT move_stop, initial_time, final_time, total_time, initial_lat, initial_lon,
                       initial_address, initial_poi_name, initial_poi_distance, total_km, driver_name, max_spd
                FROM mova.con_stop
                WHERE unit_id = :u AND initial_time >= :ini AND initial_time < :fim
                ORDER BY initial_time
                """
            ),
            {"u": v["unit_id"], "ini": ini, "fim": fim},
        )
    ).mappings().all()
    pontos = v["pontos"]
    origem = next((p for p in pontos if p["tipo"] == "origem"), None)
    destino = next((p for p in pontos if p["tipo"] == "destino"), None)

    saida_real = None
    chegada_real = None
    paradas = []
    km = 0.0
    vel_max = 0
    motoristas = set()
    # Saída: o primeiro deslocamento que começa no raio da origem; sem isso, o
    # primeiro deslocamento da janela (manobra antes de sair também conta).
    def _na_origem(t) -> bool:
        if not origem or t["initial_lat"] is None:
            return False
        return _dist_m(float(t["initial_lat"]), float(t["initial_lon"]), origem["latitude"], origem["longitude"]) <= origem.get("raio_m", RAIO_PADRAO_M)

    marco_saida = None
    if origem:
        saidas_origem = [t["initial_time"] for t in trechos if t["move_stop"] != 0 and _na_origem(t)]
        # Saída da origem entre 2 h antes e 6 h depois do previsto; fora disso,
        # o veículo não saiu da origem declarada e vale o primeiro movimento.
        janela = [x for x in saidas_origem if sp - timedelta(hours=2) <= x <= sp + timedelta(hours=6)]
        marco_saida = janela[0] if janela else None
    for t in trechos:
        if marco_saida and saida_real is None and t["initial_time"] < marco_saida:
            continue
        if t["driver_name"] and t["driver_name"] != "Não Informado":
            motoristas.add(t["driver_name"])
        if t["move_stop"] != 0:
            if saida_real is None and t["initial_time"] >= sp - timedelta(hours=2):
                saida_real = t["initial_time"]
            if saida_real and not chegada_real:
                km += float(t["total_km"] or 0) / 1000
                vel_max = max(vel_max, t["max_spd"] or 0)
            continue
        if saida_real is None or chegada_real is not None:
            continue
        dur_min = (t["total_time"] or ((t["final_time"] or agora) - t["initial_time"]).total_seconds()) / 60
        if dur_min < PARADA_MIN_MIN or t["initial_lat"] is None:
            continue
        lat, lon = float(t["initial_lat"]), float(t["initial_lon"])
        perto = min(
            ((p, _dist_m(lat, lon, p["latitude"], p["longitude"])) for p in pontos),
            key=lambda x: x[1],
            default=(None, None),
        )
        autorizado = perto[0] if perto[0] and perto[1] <= perto[0].get("raio_m", RAIO_PADRAO_M) else None
        par = {
            "inicio": t["initial_time"].isoformat(),
            "fim": t["final_time"].isoformat() if t["final_time"] else None,
            "em_andamento": t["final_time"] is None,
            "minutos": round(dur_min),
            "latitude": lat,
            "longitude": lon,
            "endereco": t["initial_address"],
            "poi_proximo": t["initial_poi_name"] if (t["initial_poi_distance"] or 99999) <= 1000 else None,
            "conforme": autorizado is not None,
            "ponto": autorizado["nome"] if autorizado else None,
            "tipo": autorizado["tipo"] if autorizado else None,
            "ponto_mais_proximo": perto[0]["nome"] if perto[0] else None,
            "distancia_ponto_m": round(perto[1]) if perto[1] is not None else None,
        }
        paradas.append(par)
        if destino and autorizado is destino:
            chegada_real = t["initial_time"]

    intermediarios = [p for p in pontos if p["tipo"] not in ("origem", "destino")]
    visitados = {p["ponto"] for p in paradas if p["conforme"]}
    ocorrencias = [
        {
            "id": f"OC-{v['id']}-{i + 1}",
            "tipo": "parada_fora_de_ponto",
            "titulo": "Parada fora de ponto autorizado",
            "inicio": p["inicio"],
            "em_andamento": p["em_andamento"],
            "minutos": p["minutos"],
            "latitude": p["latitude"],
            "longitude": p["longitude"],
            "endereco": p["endereco"],
            "ponto_mais_proximo": p["ponto_mais_proximo"],
            "distancia_ponto_m": p["distancia_ponto_m"],
            # Envio do alerta ainda não implementado: canal a definir com o cliente.
            "notificacao": "pendente (canal a definir)",
            "destinatarios": [d.get("email") or d.get("nome") for d in v.get("destinatarios", [])],
        }
        for i, p in enumerate([p for p in paradas if not p["conforme"]])
    ]
    if chegada_real and cp and chegada_real > cp + timedelta(minutes=60):
        ocorrencias.append({"id": f"OC-{v['id']}-atraso", "tipo": "atraso", "titulo": "Chegada mais de 1 h depois do previsto", "inicio": chegada_real.isoformat(), "em_andamento": False, "minutos": round((chegada_real - cp).total_seconds() / 60), "notificacao": "pendente (canal a definir)", "destinatarios": []})

    sinal = (
        await db.execute(
            text("SELECT local_time, latitude, longitude, speed, address FROM mova.dev_status WHERE unit_id = :u ORDER BY local_time DESC LIMIT 1"),
            {"u": v["unit_id"]},
        )
    ).mappings().first()
    if v.get("cancelada"):
        situacao = "cancelada"
    elif chegada_real:
        situacao = "concluida"
    elif saida_real:
        situacao = "em_rota"
    else:
        situacao = "aguardando_saida"
    return {
        "situacao": situacao,
        "com_desvio": any(o["tipo"] == "parada_fora_de_ponto" for o in ocorrencias),
        "saida_real": saida_real.isoformat() if saida_real else None,
        "chegada_real": chegada_real.isoformat() if chegada_real else None,
        "km": round(km, 1),
        "vel_max": vel_max,
        "paradas": paradas,
        "paradas_cumpridas": len([p for p in intermediarios if p["nome"] in visitados]),
        "paradas_previstas": len(intermediarios),
        "ocorrencias": ocorrencias,
        "motoristas_identificados": sorted(motoristas),
        "ultimo_sinal": {
            "hora": sinal["local_time"].isoformat() if sinal and sinal["local_time"] else None,
            "latitude": float(sinal["latitude"]) if sinal and sinal["latitude"] is not None else None,
            "longitude": float(sinal["longitude"]) if sinal and sinal["longitude"] is not None else None,
            "velocidade": sinal["speed"] if sinal else None,
            "endereco": sinal["address"] if sinal else None,
        },
    }


async def _veiculos(db, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = (await db.execute(text("SELECT id, label, label2, model FROM mova.tracked_unit WHERE id = ANY(:ids)"), {"ids": ids})).mappings().all()
    return {r["id"]: dict(r) for r in rows}


# -------------------------------- Rotas ----------------------------------


@router.get("/pois")
async def pois(
    group_id: int = Query(...),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Pontos de interesse da empresa, para montar os pontos autorizados."""
    _checar_grupo(current_user, group_id)
    sql = "SELECT id, name, latitude, longitude, radius FROM mova.poi WHERE group_id = :g AND status = 1"
    p: dict = {"g": group_id}
    if q:
        sql += " AND name ILIKE :q"
        p["q"] = f"%{q}%"
    rows = (await db.execute(text(sql + " ORDER BY name LIMIT 2000"), p)).mappings().all()
    return [{"id": r["id"], "nome": r["name"].strip(), "latitude": float(r["latitude"]), "longitude": float(r["longitude"]), "raio_m": int(r["radius"] or RAIO_PADRAO_M)} for r in rows if r["latitude"] is not None]


@router.get("/rotas")
async def listar_rotas(group_id: int = Query(...), current_user=Depends(require_permission("reports", "read"))):
    _checar_grupo(current_user, group_id)
    with _trava, _con() as c:
        rows = c.execute("SELECT * FROM rota WHERE group_id = ? ORDER BY nome", (group_id,)).fetchall()
    return [{"id": r["id"], "nome": r["nome"], **json.loads(r["dados"])} for r in rows]


@router.post("/rotas")
async def salvar_rota(r: RotaEntrada, current_user=Depends(require_permission("reports", "read"))):
    """Rota padrão: pontos autorizados e destinatários que cada viagem herda."""
    _checar_grupo(current_user, r.group_id)
    dados = json.dumps({"pontos": [p.model_dump() for p in r.pontos], "destinatarios": [d.model_dump() for d in r.destinatarios]})
    with _trava, _con() as c:
        ex = c.execute("SELECT id FROM rota WHERE group_id = ? AND nome = ?", (r.group_id, r.nome)).fetchone()
        if ex:
            c.execute("UPDATE rota SET dados = ?, atualizado_em = ? WHERE id = ?", (dados, datetime.now().isoformat(), ex["id"]))
            rid = ex["id"]
        else:
            rid = c.execute("INSERT INTO rota (group_id, nome, dados, atualizado_em) VALUES (?, ?, ?, ?)", (r.group_id, r.nome, dados, datetime.now().isoformat())).lastrowid
    return {"id": rid}


@router.post("/viagens")
async def criar_viagem(v: ViagemEntrada, current_user=Depends(require_permission("reports", "read"))):
    _checar_grupo(current_user, v.group_id)
    if not any(p.tipo == "destino" for p in v.pontos):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o destino da viagem")
    agora = datetime.now().isoformat()
    dados = v.model_dump(mode="json")
    with _trava, _con() as c:
        n = c.execute("SELECT COUNT(*) FROM viagem WHERE group_id = ?", (v.group_id,)).fetchone()[0] + 1
        codigo = f"VG-{2800 + n}"
        vid = c.execute(
            "INSERT INTO viagem (codigo, group_id, dados, criado_em, criado_por, atualizado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (codigo, v.group_id, json.dumps(dados), agora, getattr(current_user, "user_id", None), agora),
        ).lastrowid
    return {"id": vid, "codigo": codigo}


@router.patch("/viagens/{vid}")
async def alterar_viagem(vid: int, a: ViagemAlteracao, current_user=Depends(require_permission("reports", "read"))):
    with _trava, _con() as c:
        r = c.execute("SELECT * FROM viagem WHERE id = ?", (vid,)).fetchone()
        if not r:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Viagem não encontrada")
        _checar_grupo(current_user, r["group_id"])
        d = json.loads(r["dados"])
        for k, val in a.model_dump(exclude_none=True, mode="json").items():
            d[k] = val
        historico = d.setdefault("historico", [])
        historico.append({"em": datetime.now().isoformat(), "por": getattr(current_user, "user_id", None), "mudou": list(a.model_dump(exclude_none=True).keys())})
        c.execute("UPDATE viagem SET dados = ?, atualizado_em = ? WHERE id = ?", (json.dumps(d), datetime.now().isoformat(), vid))
    return {"ok": True}


@router.get("/viagens")
async def listar_viagens(
    group_id: int = Query(...),
    dia: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Escala do dia com o realizado de cada viagem."""
    _checar_grupo(current_user, group_id)
    dia = dia or date.today()
    ini = datetime.combine(dia, datetime.min.time())
    lista = _ler_viagens(group_id, ini, ini + timedelta(days=1))
    veic = await _veiculos(db, list({v["unit_id"] for v in lista}))
    saida = []
    for v in sorted(lista, key=lambda x: x["saida_prevista"]):
        real = await _realizado(db, v)
        saida.append({**v, "veiculo": veic.get(v["unit_id"]), "realizado": real})
    return {"dia": dia.isoformat(), "viagens": saida}


@router.get("/viagens/{vid}")
async def detalhe_viagem(vid: int, db: AsyncSession = Depends(get_db_read), current_user=Depends(require_permission("reports", "read"))):
    lista = [v for v in _ler_viagens(None, None, None) if v["id"] == vid]
    if not lista:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Viagem não encontrada")
    v = lista[0]
    _checar_grupo(current_user, v["group_id"])
    veic = await _veiculos(db, [v["unit_id"]])
    return {**v, "veiculo": veic.get(v["unit_id"]), "realizado": await _realizado(db, v), "gerado_em": datetime.now().isoformat()}


@router.get("/indicadores")
async def indicadores(
    group_id: int = Query(...),
    meta_pct: float = Query(95.0, description="Meta de conformidade do seguro (SUPOSIÇÃO: 95%)"),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Conformidade do mês: viagens concluídas sem parada fora de ponto ÷ concluídas."""
    _checar_grupo(current_user, group_id)
    hoje = date.today()
    ini = datetime.combine(hoje.replace(day=1), datetime.min.time())
    lista = _ler_viagens(group_id, ini, datetime.combine(hoje + timedelta(days=1), datetime.min.time()))
    concluidas = conformes = 0
    for v in lista:
        if v.get("cancelada"):
            continue
        r = await _realizado(db, v)
        if r["situacao"] == "concluida":
            concluidas += 1
            conformes += 0 if r["com_desvio"] else 1
    return {"mes": hoje.strftime("%Y-%m"), "concluidas": concluidas, "conformes": conformes, "conformidade_pct": round(100 * conformes / concluidas, 1) if concluidas else None, "meta_pct": meta_pct}
