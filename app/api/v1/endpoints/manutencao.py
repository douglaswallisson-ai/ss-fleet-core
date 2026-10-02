"""
Manutenção preventiva e corretiva com dados reais.

Decisões de produto (PM, 02/10/2026):
- Preventiva: o CLIENTE cadastra o plano (por tipo de veículo, por modelo ou
  por veículo), partindo de modelos prontos que a SS oferece como sugestão.
- Corretiva: nasce de ALERTA automático (sinal real fora do normal) e vira
  ORDEM DE SERVIÇO, acompanhada até fechar. Também dá para abrir OS à mão.

Fontes (só leitura no banco):
- Odômetro atual: `mova.dev_status.odom` (metros), respeitando a marcação de
  qualidade (vault: dev_status-estado-atual-e-qualidade-do-odometro): 0 e
  100.000.000 são inválidos; se inválido, usa `can_total_odometer` (metros).
  Nunca `tracked_unit.initial_odometer` (valor da instalação).
- Sinais do motor: colunas `can_*`, `voltage` do `dev_status` (última leitura).

⚠️ ARMAZENAMENTO PROVISÓRIO: planos, serviços feitos e ordens de serviço
ficam num SQLite local (`data/manutencao.sqlite`) — o banco de produção é só
leitura neste projeto. A tabela definitiva é decisão da engenharia.

SUPOSIÇÃO (limites dos alertas, configuráveis em LIMITES): derivados da
distribuição real dos sinais em 02/10/2026 (1.864 veículos), não de manual
de fabricante. Confirmar com a engenharia/oficina dos clientes.
O horímetro fica de fora dos vencimentos até a unidade ser confirmada
(mediana 193.629 — não é hora).
"""

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "manutencao.sqlite"
_trava = threading.Lock()

ODOM_INVALIDOS = {0, 100_000_000}
#: Antecedência para "vence em breve".
AVISO_KM = 1_000
AVISO_DIAS = 15

LIMITES = {
    "temp_atencao": 100.0, "temp_critico": 105.0,          # °C
    "oleo_min_kpa": 70.0, "oleo_rpm_min": 900.0,            # kPa, só com motor girando
    "v24_atencao": 24.0, "v24_critico": 23.0,               # sistema 24 V (leitura > 18 V)
    "v12_atencao": 12.0, "v12_critico": 11.5,               # sistema 12 V
    "arla_min": 10.0,                                       # %
}

# Modelos de plano oferecidos como SUGESTÃO (o cliente ajusta ao manual do fabricante).
MODELOS_PLANO = [
    {"id": "pesado", "nome": "Caminhão pesado (sugestão)", "categorias": [3, 7, 20, 21], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 30000, "dias": 180},
        {"servico": "Filtro de combustível", "km": 30000, "dias": 180},
        {"servico": "Filtro de ar", "km": 60000, "dias": 365},
        {"servico": "Revisão de freios", "km": 40000, "dias": 180},
        {"servico": "Lubrificação do chassi", "km": 10000, "dias": 60},
        {"servico": "Revisão geral", "km": 100000, "dias": 365},
    ]},
    {"id": "onibus", "nome": "Ônibus urbano (sugestão)", "categorias": [12, 22], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 20000, "dias": 120},
        {"servico": "Filtro de combustível", "km": 20000, "dias": 120},
        {"servico": "Filtro de ar", "km": 40000, "dias": 240},
        {"servico": "Revisão de freios", "km": 25000, "dias": 120},
        {"servico": "Ar-condicionado", "km": None, "dias": 90},
        {"servico": "Revisão geral", "km": 80000, "dias": 365},
    ]},
    {"id": "leve", "nome": "Carro, pickup ou van (sugestão)", "categorias": [1, 2, 10, 14, 15], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 10000, "dias": 180},
        {"servico": "Filtro de ar", "km": 20000, "dias": 365},
        {"servico": "Revisão de freios", "km": 20000, "dias": 365},
        {"servico": "Revisão geral", "km": 40000, "dias": 365},
    ]},
]

STATUS_OS = ("aberta", "em_andamento", "aguardando_peca", "concluida", "cancelada")


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS plano (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, nome TEXT NOT NULL,
            escopo TEXT NOT NULL, alvo TEXT NOT NULL, itens TEXT NOT NULL, atualizado_em TEXT, atualizado_por INTEGER);
        CREATE TABLE IF NOT EXISTS servico (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, unit_id INTEGER NOT NULL,
            servico TEXT NOT NULL, data TEXT NOT NULL, odometro_km REAL, custo REAL, oficina TEXT, obs TEXT,
            ordem_id INTEGER, registrado_em TEXT, registrado_por INTEGER);
        CREATE TABLE IF NOT EXISTS ordem (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, unit_id INTEGER NOT NULL,
            tipo TEXT NOT NULL, titulo TEXT NOT NULL, descricao TEXT, prioridade TEXT NOT NULL,
            status TEXT NOT NULL, origem TEXT, aberta_em TEXT NOT NULL, concluida_em TEXT, custo REAL,
            responsavel TEXT, historico TEXT, aberta_por INTEGER);
        """
    )
    return c


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


async def _frota(group_id: int) -> list[dict]:
    """Veículos ativos do grupo com odômetro atual confiável e últimos sinais do motor."""
    rows = await _ler(
        """
        SELECT tu.id AS unit_id, tu.label AS placa, tu.label2 AS prefixo, tu.model AS modelo, tu.vehicle_year AS ano,
               tu.unit_category_id AS categoria_id, uc.name AS categoria,
               ds.local_time, ds.odom, ds.can_total_odometer, ds.odom_quality_flag,
               ds.can_engine_coolant_temp AS temp, ds.can_engine_oil_pressure AS oleo, ds.voltage,
               ds.can_def_level_percent AS arla, ds.can_fuel_level_percent AS combustivel,
               ds.can_pneumatic_system1_pressure AS ar_freio, COALESCE(ds.can_rpm, ds.rpm) AS rpm, ds.ignition
        FROM mova.tracked_unit tu
        LEFT JOIN mova.unit_category uc ON uc.id = tu.unit_category_id
        LEFT JOIN mova.dev_status ds ON ds.unit_id = tu.id
        WHERE tu.status = 1 AND tu.group_id = :g
        """,
        {"g": group_id},
    )
    out = []
    for r in rows:
        d = dict(r)
        odom, alt = d.pop("odom"), d.pop("can_total_odometer")
        km = None
        qualidade = d.pop("odom_quality_flag")
        if odom is not None and int(odom) not in ODOM_INVALIDOS:
            km = float(odom) / 1000
        elif alt is not None and float(alt) > 0:
            km = float(alt) / 1000
        d["odometro_km"] = round(km) if km is not None else None
        d["odometro_travado"] = qualidade == "frozen_business_rule"
        for k in ("temp", "oleo", "voltage", "arla", "combustivel", "ar_freio", "rpm"):
            d[k] = float(d[k]) if d[k] is not None and float(d[k]) != 0 else None
        out.append(d)
    return out


# ------------------------------- Alertas -----------------------------------


def _alertas_do_veiculo(v: dict) -> list[dict]:
    """Sinais fora do normal na última leitura (corretiva). Cada alerta pode virar OS."""
    a = []
    L = LIMITES
    recente = v.get("local_time") and v["local_time"] > datetime.now() - timedelta(hours=6)
    if not recente:
        return a
    t = v.get("temp")
    if t is not None and t < 150:
        if t >= L["temp_critico"]:
            a.append({"chave": "temperatura", "titulo": "Motor superaquecendo", "nivel": "critico", "valor": f"{t:.0f} °C",
                      "detalhe": f"Temperatura do líquido de arrefecimento em {t:.0f} °C (crítico a partir de {L['temp_critico']:.0f} °C)."})
        elif t >= L["temp_atencao"]:
            a.append({"chave": "temperatura", "titulo": "Motor quente", "nivel": "atencao", "valor": f"{t:.0f} °C",
                      "detalhe": f"Temperatura em {t:.0f} °C (atenção a partir de {L['temp_atencao']:.0f} °C)."})
    o, rpm = v.get("oleo"), v.get("rpm")
    if o is not None and rpm is not None and rpm >= L["oleo_rpm_min"] and o < L["oleo_min_kpa"]:
        a.append({"chave": "oleo", "titulo": "Pressão de óleo baixa", "nivel": "critico", "valor": f"{o:.0f} kPa",
                  "detalhe": f"Pressão do óleo em {o:.0f} kPa com o motor a {rpm:.0f} rpm (mínimo {L['oleo_min_kpa']:.0f} kPa)."})
    tensao = v.get("voltage")
    if tensao is not None:
        if tensao > 18:
            crit, aten, sist = L["v24_critico"], L["v24_atencao"], "24 V"
        else:
            crit, aten, sist = L["v12_critico"], L["v12_atencao"], "12 V"
        if tensao < crit:
            a.append({"chave": "bateria", "titulo": "Bateria/alternador", "nivel": "critico", "valor": f"{tensao:.1f} V",
                      "detalhe": f"Tensão em {tensao:.1f} V num sistema de {sist} (crítico abaixo de {crit} V)."})
        elif tensao < aten:
            a.append({"chave": "bateria", "titulo": "Tensão baixa", "nivel": "atencao", "valor": f"{tensao:.1f} V",
                      "detalhe": f"Tensão em {tensao:.1f} V num sistema de {sist} (atenção abaixo de {aten} V)."})
    arla = v.get("arla")
    if arla is not None and arla <= 100 and arla < L["arla_min"]:
        a.append({"chave": "arla", "titulo": "ARLA 32 no fim", "nivel": "atencao", "valor": f"{arla:.0f}%",
                  "detalhe": f"Nível de ARLA 32 em {arla:.0f}%. Sem ARLA o motor perde potência."})
    # Odômetro travado é qualidade do dado, não defeito do veículo: vai em
    # `odometro_travado` (aviso à parte), não aqui — na Fênix seriam 180 "alertas".
    return a


# ------------------------------- Planos ------------------------------------


class ItemPlano(BaseModel):
    servico: str = Field(..., min_length=2, max_length=80)
    km: Optional[int] = Field(None, ge=100, le=1_000_000)
    dias: Optional[int] = Field(None, ge=1, le=3650)


class Plano(BaseModel):
    group_id: int
    nome: str = Field(..., min_length=2, max_length=80)
    escopo: Literal["categoria", "modelo", "veiculo"]
    alvo: str = Field(..., min_length=1, max_length=120, description="id da categoria, nome do modelo ou id do veículo")
    itens: list[ItemPlano] = Field(..., min_length=1, max_length=40)


@router.get("/modelos-plano")
async def modelos_plano(user=Depends(require_permission("reports", "read"))):
    return MODELOS_PLANO


@router.get("/planos")
async def listar_planos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        rows = c.execute("SELECT * FROM plano WHERE group_id = ? ORDER BY nome", (group_id,)).fetchall()
    return [{**dict(r), "itens": json.loads(r["itens"])} for r in rows]


@router.post("/planos")
async def salvar_plano(p: Plano, plano_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    itens = [i.model_dump() for i in p.itens if i.km or i.dias]
    if not itens:
        raise HTTPException(422, "Cada serviço precisa de um intervalo em km ou em dias.")
    with _trava, _con() as c:
        if plano_id:
            c.execute("UPDATE plano SET nome=?, escopo=?, alvo=?, itens=?, atualizado_em=?, atualizado_por=? WHERE id=? AND group_id=?",
                      (p.nome, p.escopo, p.alvo, json.dumps(itens, ensure_ascii=False), _agora(), user.user_id, plano_id, p.group_id))
            return {"id": plano_id}
        cur = c.execute("INSERT INTO plano (group_id, nome, escopo, alvo, itens, atualizado_em, atualizado_por) VALUES (?,?,?,?,?,?,?)",
                        (p.group_id, p.nome, p.escopo, p.alvo, json.dumps(itens, ensure_ascii=False), _agora(), user.user_id))
        return {"id": cur.lastrowid}


@router.delete("/planos/{plano_id}", status_code=204)
async def apagar_plano(plano_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _trava, _con() as c:
        c.execute("DELETE FROM plano WHERE id = ? AND group_id = ?", (plano_id, group_id))


def _plano_do_veiculo(v: dict, planos: list[dict]) -> Optional[dict]:
    """Veículo > modelo > categoria: o mais específico vale."""
    for escopo, valor in (("veiculo", str(v["unit_id"])), ("modelo", (v.get("modelo") or "").strip().lower()),
                          ("categoria", str(v.get("categoria_id") or ""))):
        for p in planos:
            alvo = p["alvo"].strip().lower() if escopo == "modelo" else p["alvo"]
            if p["escopo"] == escopo and valor and alvo == valor:
                return p
    return None


# ------------------------------- Preventiva --------------------------------


class Servico(BaseModel):
    group_id: int
    unit_id: int
    servico: str = Field(..., min_length=2, max_length=80)
    data: date
    odometro_km: Optional[float] = Field(None, ge=0)
    custo: Optional[float] = Field(None, ge=0)
    oficina: Optional[str] = Field(None, max_length=120)
    obs: Optional[str] = Field(None, max_length=1000)
    ordem_id: Optional[int] = None


@router.post("/servicos")
async def registrar_servico(s: Servico, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, s.group_id)
    with _trava, _con() as c:
        cur = c.execute(
            "INSERT INTO servico (group_id, unit_id, servico, data, odometro_km, custo, oficina, obs, ordem_id, registrado_em, registrado_por)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s.group_id, s.unit_id, s.servico, s.data.isoformat(), s.odometro_km, s.custo, s.oficina, s.obs, s.ordem_id, _agora(), user.user_id),
        )
        if s.ordem_id:
            _mudar_status(c, s.ordem_id, s.group_id, "concluida", user.user_id, f"Serviço registrado: {s.servico}", custo=s.custo)
    return {"id": cur.lastrowid}


@router.get("/servicos")
async def historico(group_id: int = Query(...), unit_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        sql, par = "SELECT * FROM servico WHERE group_id = ?", [group_id]
        if unit_id:
            sql += " AND unit_id = ?"
            par.append(unit_id)
        return [dict(r) for r in c.execute(sql + " ORDER BY data DESC, id DESC LIMIT 500", par).fetchall()]


def _situacao_item(item: dict, ultimo: Optional[dict], km_atual: Optional[float]) -> dict:
    hoje = date.today()
    res = {"servico": item["servico"], "intervalo_km": item.get("km"), "intervalo_dias": item.get("dias"),
           "ultimo": ultimo and {"data": ultimo["data"], "odometro_km": ultimo["odometro_km"]}}
    if not ultimo:
        return {**res, "situacao": "sem_registro", "falta_km": None, "falta_dias": None, "proximo_km": None, "proxima_data": None}
    falta_km = falta_dias = proximo_km = proxima_data = None
    if item.get("km") and ultimo["odometro_km"] is not None:
        proximo_km = ultimo["odometro_km"] + item["km"]
        if km_atual is not None:
            falta_km = round(proximo_km - km_atual)
    if item.get("dias"):
        d = date.fromisoformat(ultimo["data"]) + timedelta(days=item["dias"])
        proxima_data = d.isoformat()
        falta_dias = (d - hoje).days
    vencido = (falta_km is not None and falta_km <= 0) or (falta_dias is not None and falta_dias <= 0)
    breve = (falta_km is not None and falta_km <= AVISO_KM) or (falta_dias is not None and falta_dias <= AVISO_DIAS)
    return {**res, "situacao": "vencido" if vencido else "vence_em_breve" if breve else "em_dia",
            "falta_km": falta_km, "falta_dias": falta_dias, "proximo_km": proximo_km and round(proximo_km), "proxima_data": proxima_data}


ORDEM_SIT = {"vencido": 0, "vence_em_breve": 1, "sem_registro": 2, "em_dia": 3}


@router.get("/painel")
async def painel(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Situação de cada veículo: plano, itens vencidos/vencendo, alertas e OS abertas."""
    _grupo_ok(user, group_id)
    frota = await _frota(group_id)
    with _con() as c:
        planos = [{**dict(r), "itens": json.loads(r["itens"])} for r in c.execute("SELECT * FROM plano WHERE group_id = ?", (group_id,))]
        servicos = [dict(r) for r in c.execute("SELECT * FROM servico WHERE group_id = ? ORDER BY data DESC, id DESC", (group_id,))]
        ordens = [dict(r) for r in c.execute(
            "SELECT * FROM ordem WHERE group_id = ? AND status NOT IN ('concluida','cancelada')", (group_id,))]
    ultimo: dict[tuple, dict] = {}
    for s in servicos:
        ultimo.setdefault((s["unit_id"], s["servico"].strip().lower()), s)
    os_por_veiculo: dict[int, list] = {}
    for o in ordens:
        os_por_veiculo.setdefault(o["unit_id"], []).append(o)

    veiculos = []
    for v in frota:
        plano = _plano_do_veiculo(v, planos)
        itens = []
        if plano:
            for it in plano["itens"]:
                itens.append(_situacao_item(it, ultimo.get((v["unit_id"], it["servico"].strip().lower())), v["odometro_km"]))
            itens.sort(key=lambda i: (ORDEM_SIT[i["situacao"]], i["falta_km"] if i["falta_km"] is not None else 10**9))
        alertas = _alertas_do_veiculo(v)
        oss = os_por_veiculo.get(v["unit_id"], [])
        veiculos.append({
            **{k: v[k] for k in ("unit_id", "placa", "prefixo", "modelo", "ano", "categoria_id", "categoria", "odometro_km", "odometro_travado")},
            "ultimo_sinal": v["local_time"],
            "sinais": {k: v[k] for k in ("temp", "oleo", "voltage", "arla", "combustivel", "ar_freio", "rpm")},
            "plano": plano and {"id": plano["id"], "nome": plano["nome"], "escopo": plano["escopo"]},
            "itens": itens,
            "vencidos": sum(1 for i in itens if i["situacao"] == "vencido"),
            "vencendo": sum(1 for i in itens if i["situacao"] == "vence_em_breve"),
            "sem_registro": sum(1 for i in itens if i["situacao"] == "sem_registro"),
            "alertas": alertas,
            "ordens_abertas": len(oss),
            "alertas_com_os": sorted({(o.get("origem") or "").replace("alerta:", "") for o in oss if (o.get("origem") or "").startswith("alerta:")}),
        })
    veiculos.sort(key=lambda x: (-sum(1 for a in x["alertas"] if a["nivel"] == "critico"), -x["vencidos"], -len(x["alertas"]), -x["vencendo"], x["prefixo"] or ""))
    return {
        "totais": {
            "veiculos": len(veiculos),
            "odometro_travado": sum(1 for v in veiculos if v["odometro_travado"]),
            "com_plano": sum(1 for v in veiculos if v["plano"]),
            "itens_vencidos": sum(v["vencidos"] for v in veiculos),
            "veiculos_com_vencido": sum(1 for v in veiculos if v["vencidos"]),
            "itens_vencendo": sum(v["vencendo"] for v in veiculos),
            "alertas_criticos": sum(1 for v in veiculos for a in v["alertas"] if a["nivel"] == "critico"),
            "alertas": sum(len(v["alertas"]) for v in veiculos),
            "ordens_abertas": len(ordens),
        },
        "limites": LIMITES,
        "veiculos": veiculos,
    }


# ------------------------------- Ordens de serviço -------------------------


class NovaOrdem(BaseModel):
    group_id: int
    unit_id: int
    tipo: Literal["corretiva", "preventiva"] = "corretiva"
    titulo: str = Field(..., min_length=3, max_length=120)
    descricao: Optional[str] = Field(None, max_length=2000)
    prioridade: Literal["baixa", "media", "alta", "critica"] = "media"
    origem: Optional[str] = Field(None, max_length=60, description="alerta:<chave>, plano:<serviço> ou manual")
    responsavel: Optional[str] = Field(None, max_length=120)


class MudancaOrdem(BaseModel):
    group_id: int
    status: Optional[Literal["aberta", "em_andamento", "aguardando_peca", "concluida", "cancelada"]] = None
    responsavel: Optional[str] = Field(None, max_length=120)
    custo: Optional[float] = Field(None, ge=0)
    nota: Optional[str] = Field(None, max_length=1000)


def _mudar_status(c, ordem_id: int, group_id: int, status: Optional[str], por: int, nota: Optional[str] = None, **campos):
    r = c.execute("SELECT * FROM ordem WHERE id = ? AND group_id = ?", (ordem_id, group_id)).fetchone()
    if not r:
        raise HTTPException(404, "Ordem de serviço não encontrada.")
    hist = json.loads(r["historico"] or "[]")
    hist.append({"em": _agora(), "por": por, "status": status or r["status"], "nota": nota})
    sets, vals = ["historico = ?"], [json.dumps(hist, ensure_ascii=False)]
    if status:
        sets.append("status = ?")
        vals.append(status)
        if status in ("concluida", "cancelada"):
            sets.append("concluida_em = ?")
            vals.append(_agora())
    for k, v in campos.items():
        if v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    c.execute(f"UPDATE ordem SET {', '.join(sets)} WHERE id = ?", (*vals, ordem_id))


@router.post("/ordens")
async def abrir_ordem(o: NovaOrdem, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, o.group_id)
    with _trava, _con() as c:
        if o.origem and o.origem.startswith("alerta:"):
            ja = c.execute(
                "SELECT id FROM ordem WHERE group_id=? AND unit_id=? AND origem=? AND status NOT IN ('concluida','cancelada')",
                (o.group_id, o.unit_id, o.origem)).fetchone()
            if ja:
                return {"id": ja["id"], "ja_existia": True}
        hist = json.dumps([{"em": _agora(), "por": user.user_id, "status": "aberta", "nota": "Ordem aberta"}], ensure_ascii=False)
        cur = c.execute(
            "INSERT INTO ordem (group_id, unit_id, tipo, titulo, descricao, prioridade, status, origem, aberta_em, responsavel, historico, aberta_por)"
            " VALUES (?,?,?,?,?,?,'aberta',?,?,?,?,?)",
            (o.group_id, o.unit_id, o.tipo, o.titulo, o.descricao, o.prioridade, o.origem or "manual", _agora(), o.responsavel, hist, user.user_id),
        )
        return {"id": cur.lastrowid, "ja_existia": False}


@router.get("/ordens")
async def listar_ordens(group_id: int = Query(...), abertas: bool = Query(False), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        sql = "SELECT * FROM ordem WHERE group_id = ?"
        if abertas:
            sql += " AND status NOT IN ('concluida','cancelada')"
        rows = c.execute(sql + " ORDER BY aberta_em DESC LIMIT 500", (group_id,)).fetchall()
    return [{**dict(r), "historico": json.loads(r["historico"] or "[]")} for r in rows]


@router.patch("/ordens/{ordem_id}")
async def mudar_ordem(ordem_id: int, m: MudancaOrdem, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, m.group_id)
    with _trava, _con() as c:
        _mudar_status(c, ordem_id, m.group_id, m.status, user.user_id, m.nota, responsavel=m.responsavel, custo=m.custo)
    return {"ok": True}
