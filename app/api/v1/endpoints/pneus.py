"""Gestão de pneus sem sensores: MVP (US-01 a US-08 e US-01B). Regras em `app/core/pneus.py`.

Pneus e eventos ficam em `data/cco.sqlite` (o banco de produção é só leitura). Placa e km vêm do banco, só leitura, com a
regra única de `app/core/odometro.py`. Nenhum evento é apagado. Cada empresa só vê os próprios pneus.
"""

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.endpoints.cco import ARQUIVO, _grupos_do_usuario, _ler
from app.core import pneus as pn
from app.core.odometro import odometro_km
from app.middleware.auth import require_permission

router = APIRouter()

FUSO = ZoneInfo("America/Sao_Paulo")
CACHE_KM_S = 60
_trava = threading.Lock()
_cache: dict[tuple, tuple[float, object]] = {}


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS pneu (
            id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL, fogo TEXT NOT NULL, marca TEXT NOT NULL, modelo TEXT NOT NULL, medida TEXT NOT NULL,
            compra_em TEXT, preco REAL, fornecedor TEXT, nota_fiscal TEXT, dot TEXT, tipo_uso TEXT, status TEXT NOT NULL, vida INTEGER NOT NULL,
            km_anterior REAL NOT NULL DEFAULT 0, carga_inicial INTEGER NOT NULL DEFAULT 0, UNIQUE (group_id, fogo));
        CREATE TABLE IF NOT EXISTS pneu_montagem (
            id INTEGER PRIMARY KEY, pneu_id INTEGER NOT NULL, vida INTEGER NOT NULL, unit_id INTEGER NOT NULL, placa TEXT NOT NULL, posicao TEXT NOT NULL,
            entrou_em TEXT NOT NULL, km_entrada REAL NOT NULL, saiu_em TEXT, km_saida REAL, motivo TEXT);
        CREATE INDEX IF NOT EXISTS ix_pneu_montagem ON pneu_montagem (pneu_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_pneu_posicao_aberta ON pneu_montagem (unit_id, posicao) WHERE saiu_em IS NULL;
        CREATE TABLE IF NOT EXISTS pneu_evento (
            id INTEGER PRIMARY KEY, pneu_id INTEGER NOT NULL, vida INTEGER NOT NULL, tipo TEXT NOT NULL, em TEXT NOT NULL, custo REAL, dados TEXT NOT NULL,
            registrado_por TEXT, registrado_em TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_pneu_evento ON pneu_evento (pneu_id);
        CREATE TABLE IF NOT EXISTS pneu_servico (
            id INTEGER PRIMARY KEY, pneu_id INTEGER NOT NULL, tipo TEXT NOT NULL, numero INTEGER, fornecedor TEXT, enviado_em TEXT NOT NULL, prazo_dias INTEGER,
            banda TEXT, custo_previsto REAL, retorno_em TEXT, custo REAL, resultado TEXT, garantia TEXT);
        CREATE TABLE IF NOT EXISTS pneu_config (group_id INTEGER PRIMARY KEY, limite_recapagens INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS pneu_veiculo (unit_id INTEGER PRIMARY KEY, modelo_eixos TEXT NOT NULL);
    """)
    return c


def _hoje() -> date:
    return datetime.now(FUSO).date()


def _quem(user) -> str:
    return getattr(user, "email", None) or f"usuário {user.user_id}"


def _evento(c, pneu_id: int, vida: int, evento: str, em: date, user, custo: Optional[float] = None, **dados) -> None:
    c.execute("INSERT INTO pneu_evento (pneu_id, vida, tipo, em, custo, dados, registrado_por, registrado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (pneu_id, vida, evento, em.isoformat(), custo, json.dumps(dados, ensure_ascii=False), _quem(user), datetime.now(FUSO).isoformat(timespec="seconds")))


def _pneu(c, user, pneu_id: int) -> dict:
    r = c.execute("SELECT * FROM pneu WHERE id = ?", (pneu_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Pneu não encontrado.")
    _grupos_do_usuario(user, r["group_id"])
    return dict(r)


def _aberta(c, pneu_id: int) -> Optional[dict]:
    r = c.execute("SELECT * FROM pneu_montagem WHERE pneu_id = ? AND saiu_em IS NULL", (pneu_id,)).fetchone()
    return dict(r) if r else None


def _onde(m: dict) -> str:
    return f'{m["placa"]}, {pn.rotulo_posicao(m["posicao"])}'


def _limite(c, group_id: int) -> int:
    r = c.execute("SELECT limite_recapagens FROM pneu_config WHERE group_id = ?", (group_id,)).fetchone()
    return r[0] if r else pn.LIMITE_RECAPAGENS_PADRAO


def _servico_aberto(c, pneu_id: int) -> Optional[dict]:
    r = c.execute("SELECT * FROM pneu_servico WHERE pneu_id = ? AND retorno_em IS NULL ORDER BY id DESC LIMIT 1", (pneu_id,)).fetchone()
    return dict(r) if r else None


def _br(iso: Optional[str]) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y") if iso else ""


def _recusar(p: dict, c, acao: str) -> None:
    m, s = _aberta(c, p["id"]), _servico_aberto(c, p["id"])
    motivo = pn.recusa_status(p["status"], acao, _onde(m) if m else "", _br(s["enviado_em"]) if s else "")
    if motivo:
        raise HTTPException(409, motivo)


def _checar_data(d: date, depois_de: Optional[str] = None) -> None:
    motivo = pn.erro_data(d, _hoje(), date.fromisoformat(depois_de[:10]) if depois_de else None)
    if motivo:
        raise HTTPException(422, motivo)


async def _frota(grupos: Optional[list[int]]) -> dict[int, dict]:
    """Veículos ativos com km atual, em cache curto por empresa."""
    k = tuple(grupos) if grupos is not None else None
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < CACHE_KM_S:
        return hit[1]  # type: ignore[return-value]
    rows = await _ler("""
        SELECT tu.id AS unit_id, tu.group_id, tu.label AS placa, tu.label2 AS prefixo, ds.local_time, ds.odom_total, ds.odom, ds.can_total_odometer
        FROM mova.tracked_unit tu LEFT JOIN mova.dev_status ds ON ds.unit_id = tu.id
        WHERE tu.status = 1 AND (CAST(:todos AS boolean) OR tu.group_id = ANY(:g))""", {"todos": grupos is None, "g": grupos or []})
    out = {}
    for r in rows:
        km = odometro_km(r["odom_total"], r["odom"], r["can_total_odometer"])
        out[r["unit_id"]] = {"unit_id": r["unit_id"], "group_id": r["group_id"], "placa": r["placa"], "prefixo": r["prefixo"], "km": round(km) if km is not None else None,
                             "lido_em": r["local_time"].isoformat() if r["local_time"] else None, "fonte_km": pn.fonte_do_km(r["can_total_odometer"])}
    _cache[k] = (time.time(), out)
    return out


async def _veiculo(group_id: int, unit_id: int) -> dict:
    v = (await _frota([group_id])).get(unit_id)
    if not v:
        raise HTTPException(404, "Veículo não encontrado nesta empresa.")
    return v


async def _km_na_data(unit_id: int, d: date) -> Optional[float]:
    """Última leitura válida do dia (até 3 dias antes). `dev_status_30` guarda o histórico; o veículo pode não ter leitura."""
    rows = await _ler("""SELECT odom_total, odom, can_total_odometer FROM mova.dev_status_30
                         WHERE unit_id = :u AND local_time >= :a AND local_time < :b ORDER BY local_time DESC LIMIT 20""",
                      {"u": unit_id, "a": datetime.combine(d - timedelta(days=3), datetime.min.time()), "b": datetime.combine(d + timedelta(days=1), datetime.min.time())})
    for r in rows:
        km = odometro_km(r["odom_total"], r["odom"], r["can_total_odometer"])
        if km is not None:
            return round(km)
    return None


async def _km_de_referencia(unit_id: int, group_id: int, d: date) -> Optional[float]:
    if d >= _hoje():
        return (await _veiculo(group_id, unit_id))["km"]
    return await _km_na_data(unit_id, d)


async def _exigir_km_coerente(unit_id: int, group_id: int, d: date, km: float, confirmar: bool) -> None:
    if confirmar:
        return
    ref = await _km_de_referencia(unit_id, group_id, d)
    if pn.km_suspeito(km, ref):
        raise HTTPException(409, {"codigo": "km_inconsistente", "mensagem": f"O km informado ({km:,.0f}) é menor que o km registrado do veículo na data ({ref:,.0f}). Confirme se está certo.".replace(",", ".")})


def _ocupante(c, unit_id: int, posicao: str, exceto: Optional[int] = None) -> Optional[dict]:
    r = c.execute("""SELECT m.*, p.fogo FROM pneu_montagem m JOIN pneu p ON p.id = m.pneu_id
                     WHERE m.unit_id = ? AND m.posicao = ? AND m.saiu_em IS NULL""", (unit_id, posicao)).fetchone()
    return dict(r) if r and r["pneu_id"] != exceto else None


def _modelo_do_veiculo(c, unit_id: int) -> Optional[str]:
    r = c.execute("SELECT modelo_eixos FROM pneu_veiculo WHERE unit_id = ?", (unit_id,)).fetchone()
    return r[0] if r else None


def _validar_posicao(c, unit_id: int, posicao: str, modelo_eixos: Optional[str]) -> str:
    modelo = modelo_eixos or _modelo_do_veiculo(c, unit_id)
    if not modelo:
        raise HTTPException(422, "Escolha o modelo de eixos do veículo.")
    if modelo not in pn.MODELOS_EIXOS:
        raise HTTPException(422, "Modelo de eixos inexistente.")
    if posicao not in {x["chave"] for x in pn.posicoes(modelo)}:
        raise HTTPException(422, "Esta posição não existe neste veículo.")
    return modelo


def _abrir_montagem(c, p: dict, v: dict, posicao: str, modelo: str, d: date, km: float) -> None:
    c.execute("INSERT INTO pneu_veiculo (unit_id, modelo_eixos) VALUES (?, ?) ON CONFLICT (unit_id) DO UPDATE SET modelo_eixos = excluded.modelo_eixos", (v["unit_id"], modelo))
    c.execute("INSERT INTO pneu_montagem (pneu_id, vida, unit_id, placa, posicao, entrou_em, km_entrada) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (p["id"], p["vida"], v["unit_id"], v["placa"], posicao, d.isoformat(), km))
    c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.MONTADO, p["id"]))


def _fechar_montagem(c, m: dict, d: date, km: float, motivo: str) -> float:
    c.execute("UPDATE pneu_montagem SET saiu_em = ?, km_saida = ?, motivo = ? WHERE id = ?", (d.isoformat(), km, motivo, m["id"]))
    return pn.km_montagem(m["km_entrada"], km)[0]


def _custos(c, pneu_id: int) -> dict:
    rec = c.execute("SELECT coalesce(sum(custo), 0) FROM pneu_servico WHERE pneu_id = ?", (pneu_id,)).fetchone()[0]
    oco = c.execute("SELECT coalesce(sum(custo), 0) FROM pneu_evento WHERE pneu_id = ? AND tipo = 'ocorrencia'", (pneu_id,)).fetchone()[0]
    return {"servicos": rec, "ocorrencias": oco}


def _linha(c, p: dict, frota: dict, hoje: date) -> dict:
    montagens = [dict(r) for r in c.execute("SELECT * FROM pneu_montagem WHERE pneu_id = ? ORDER BY id", (p["id"],))]
    aberta = next((m for m in montagens if not m["saiu_em"]), None)
    v = frota.get(aberta["unit_id"]) if aberta else None
    res = pn.resumo_km(montagens, v["km"] if v else None, p["km_anterior"])
    lido = date.fromisoformat(v["lido_em"][:10]) if v and v["lido_em"] else None
    s = _servico_aberto(c, p["id"])
    custos = _custos(c, p["id"])
    dias_fora = (hoje - date.fromisoformat(s["enviado_em"])).days if s else None
    return {
        **{k: p[k] for k in ("id", "group_id", "fogo", "marca", "modelo", "medida", "compra_em", "preco", "fornecedor", "nota_fiscal", "dot", "tipo_uso", "status", "vida", "carga_inicial")},
        "status_rotulo": pn.STATUS_ROTULO[p["status"]], "recapagens": pn.recapagens_feitas(p["vida"]),
        "km_total": res["km_total"], "km_medido": res["km_medido"], "km_informado": res["km_informado"], "km_inconsistente": res["inconsistente"],
        "placa": aberta["placa"] if aberta else None, "unit_id": aberta["unit_id"] if aberta else None,
        "posicao": aberta["posicao"] if aberta else None, "posicao_rotulo": pn.rotulo_posicao(aberta["posicao"]) if aberta else None,
        "km_desatualizado": bool(aberta) and pn.km_desatualizado(lido, hoje), "fonte_km": v["fonte_km"] if v else None,
        "km_veiculo_lido_em": v["lido_em"] if v else None,
        "custo_acumulado": round((p["preco"] or 0) + custos["servicos"] + custos["ocorrencias"], 2), "preco_informado": p["preco"] is not None,
        "servico": {**s, "dias_fora": dias_fora, "atrasado": bool(s["prazo_dias"] and dias_fora > s["prazo_dias"])} if s else None,
    }


@router.get("")
async def listar(group_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    grupos = _grupos_do_usuario(user, group_id)
    frota = await _frota(grupos)
    hoje = _hoje()
    with _con() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM pneu ORDER BY fogo") if grupos is None or r["group_id"] in grupos]
        pneus = [_linha(c, p, frota, hoje) for p in rows]
    return {"pneus": pneus, "motivos_saida": pn.MOTIVOS_SAIDA, "tipos_ocorrencia": pn.TIPOS_OCORRENCIA, "resultados_ocorrencia": pn.RESULTADOS_OCORRENCIA,
            "modelos_eixos": [{"chave": k, "rotulo": v[0], "posicoes": pn.posicoes(k)} for k, v in pn.MODELOS_EIXOS.items()],
            "sugestoes": {x: sorted({p[x] for p in pneus if p[x]}) for x in ("marca", "medida", "fornecedor")}}


@router.get("/veiculos")
async def veiculos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupos_do_usuario(user, group_id)
    frota = await _frota([group_id])
    with _con() as c:
        modelos = {r[0]: r[1] for r in c.execute("SELECT unit_id, modelo_eixos FROM pneu_veiculo")}
    return {"veiculos": sorted(({**v, "modelo_eixos": modelos.get(v["unit_id"])} for v in frota.values()), key=lambda v: v["placa"] or "")}


@router.get("/veiculos/{unit_id}")
async def pneus_do_veiculo(unit_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Aba Pneus no veículo: cada posição do modelo de eixos, com o pneu e o km dele."""
    _grupos_do_usuario(user, group_id)
    v = await _veiculo(group_id, unit_id)
    frota = {unit_id: v}
    hoje = _hoje()
    with _con() as c:
        modelo = _modelo_do_veiculo(c, unit_id)
        abertas = {m["posicao"]: m["pneu_id"] for m in c.execute("SELECT posicao, pneu_id FROM pneu_montagem WHERE unit_id = ? AND saiu_em IS NULL", (unit_id,))}
        ocupadas = {pos: _linha(c, dict(c.execute("SELECT * FROM pneu WHERE id = ?", (i,)).fetchone()), frota, hoje) for pos, i in abertas.items()}
    return {"veiculo": {**v, "modelo_eixos": modelo},
            "posicoes": [{**x, "pneu": ocupadas.get(x["chave"])} for x in pn.posicoes(modelo)] if modelo else [],
            "sem_posicao_no_modelo": [p for k, p in ocupadas.items() if not modelo or k not in {x["chave"] for x in pn.posicoes(modelo)}]}


@router.get("/km-do-veiculo")
async def km_do_veiculo(group_id: int, unit_id: int, data: date, user=Depends(require_permission("reports", "read"))):
    """Sugestão de km na data: atual para hoje; histórico para data passada, se houver."""
    _grupos_do_usuario(user, group_id)
    v = await _veiculo(group_id, unit_id)
    return {"km": await _km_de_referencia(unit_id, group_id, data), "fonte": v["fonte_km"], "historico": data < _hoje()}


class Cadastro(BaseModel):
    group_id: int
    fogos: list[str] = Field(min_length=1, max_length=200)
    marca: str = Field(min_length=1)
    modelo: str = Field(min_length=1)
    medida: str = Field(min_length=1)
    compra_em: Optional[date] = None
    preco: Optional[float] = None
    fornecedor: Optional[str] = None
    nota_fiscal: Optional[str] = None
    dot: Optional[str] = None
    tipo_uso: Optional[str] = None
    carga_inicial: bool = False
    vida: int = Field(1, ge=1, le=20)
    km_anterior: float = Field(0, ge=0)


@router.post("")
async def cadastrar(corpo: Cadastro, user=Depends(require_permission("reports", "read"))):
    """Um ou vários pneus iguais (US-01) ou carga de pneus que já rodam (US-01B). Montar vem depois, pela ação Montar."""
    _grupos_do_usuario(user, corpo.group_id)
    erro = pn.erro_cadastro(corpo.preco, corpo.compra_em, _hoje(), corpo.carga_inicial)
    if erro:
        raise HTTPException(422, erro)
    fogos = [f.strip().upper() for f in corpo.fogos if f.strip()]
    if not fogos or len(set(fogos)) != len(fogos):
        raise HTTPException(422, "Informe números de fogo sem repetição.")
    vida = corpo.vida if corpo.carga_inicial else 1
    km_anterior = corpo.km_anterior if corpo.carga_inicial else 0
    criados = []
    with _trava, _con() as c:
        for f in fogos:
            r = c.execute("SELECT marca, modelo, medida FROM pneu WHERE group_id = ? AND fogo = ?", (corpo.group_id, f)).fetchone()
            if r:
                raise HTTPException(409, f"O número de fogo {f} já é usado pelo pneu {r['marca']} {r['modelo']} {r['medida']}.")
        limite = _limite(c, corpo.group_id)
        for f in fogos:
            cur = c.execute("""INSERT INTO pneu (group_id, fogo, marca, modelo, medida, compra_em, preco, fornecedor, nota_fiscal, dot, tipo_uso, status, vida, km_anterior, carga_inicial)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (corpo.group_id, f, corpo.marca.strip(), corpo.modelo.strip(), corpo.medida.strip(), corpo.compra_em.isoformat() if corpo.compra_em else None, corpo.preco,
                             corpo.fornecedor, corpo.nota_fiscal, corpo.dot, corpo.tipo_uso, pn.ESTOQUE, vida, km_anterior, int(corpo.carga_inicial)))
            if corpo.carga_inicial:
                _evento(c, cur.lastrowid, vida, "carga_inicial", corpo.compra_em or _hoje(), user, vida_informada=vida, recapagens=pn.recapagens_feitas(vida), km_informado=km_anterior, preco=corpo.preco, fornecedor=corpo.fornecedor)
            else:
                _evento(c, cur.lastrowid, vida, "compra", corpo.compra_em, user, preco=corpo.preco, fornecedor=corpo.fornecedor, nota_fiscal=corpo.nota_fiscal)
            criados.append(cur.lastrowid)
    aviso = f"O pneu já atingiu o limite de {limite} recapagens: novo envio ficará bloqueado." if pn.limite_atingido(vida, limite) else None
    return {"ids": criados, "aviso": aviso}


class Montar(BaseModel):
    unit_id: int
    posicao: str
    modelo_eixos: Optional[str] = None
    data: date
    km: float = Field(ge=0)
    confirmar: bool = False


@router.post("/{pneu_id}/montar")
async def montar(pneu_id: int, corpo: Montar, user=Depends(require_permission("reports", "read"))):
    with _con() as c:
        p = _pneu(c, user, pneu_id)
    v = await _veiculo(p["group_id"], corpo.unit_id)
    await _exigir_km_coerente(corpo.unit_id, p["group_id"], corpo.data, corpo.km, corpo.confirmar)
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        _recusar(p, c, "montar")
        _checar_data(corpo.data, p["compra_em"])
        modelo = _validar_posicao(c, corpo.unit_id, corpo.posicao, corpo.modelo_eixos)
        o = _ocupante(c, corpo.unit_id, corpo.posicao)
        if o:
            raise HTTPException(409, {"codigo": "posicao_ocupada", "pneu_id": o["pneu_id"], "mensagem": f'A posição já tem o pneu {o["fogo"]}. Desmonte-o antes.'})
        _abrir_montagem(c, p, v, corpo.posicao, modelo, corpo.data, corpo.km)
        _evento(c, pneu_id, p["vida"], "montagem", corpo.data, user, placa=v["placa"], unit_id=v["unit_id"], posicao=corpo.posicao, km=corpo.km)
    return {"ok": True}


class Desmontar(BaseModel):
    data: date
    km: float = Field(ge=0)
    motivo: str = Field(min_length=1)
    destino: str = pn.ESTOQUE
    fornecedor: Optional[str] = None
    prazo_dias: Optional[int] = None
    confirmar: bool = False


def _aplicar_destino(c, p: dict, destino: str, d: date, user, fornecedor: Optional[str], prazo_dias: Optional[int], motivo: str) -> None:
    if destino == pn.ESTOQUE:
        c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.ESTOQUE, p["id"]))
    elif destino == pn.RECAPAGEM:
        c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.ESTOQUE, p["id"]))
        _enviar_servico(c, {**p, "status": pn.ESTOQUE}, pn.RECAPAGEM, d, user, fornecedor, prazo_dias)
    elif destino == pn.REPARO:
        c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.ESTOQUE, p["id"]))
        _enviar_servico(c, {**p, "status": pn.ESTOQUE}, pn.REPARO, d, user, fornecedor, prazo_dias)
    elif destino == pn.DESCARTADO:
        c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.DESCARTADO, p["id"]))
        _evento(c, p["id"], p["vida"], "descarte", d, user, motivo=motivo)
    else:
        raise HTTPException(422, "Destino inexistente.")


def _enviar_servico(c, p: dict, tipo: str, d: date, user, fornecedor: Optional[str], prazo_dias: Optional[int], banda: Optional[str] = None, custo_previsto: Optional[float] = None) -> None:
    numero = None
    if tipo == pn.RECAPAGEM:
        limite = _limite(c, p["group_id"])
        if pn.limite_atingido(p["vida"], limite):
            raise HTTPException(409, f"O pneu atingiu o limite de {limite} recapagens. Avalie o descarte.")
        numero = pn.recapagens_feitas(p["vida"]) + 1
    c.execute("INSERT INTO pneu_servico (pneu_id, tipo, numero, fornecedor, enviado_em, prazo_dias, banda, custo_previsto) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (p["id"], tipo, numero, fornecedor, d.isoformat(), prazo_dias, banda, custo_previsto))
    c.execute("UPDATE pneu SET status = ? WHERE id = ?", (tipo, p["id"]))
    _evento(c, p["id"], p["vida"], f"envio_{tipo}", d, user, fornecedor=fornecedor, numero=numero, banda=banda)


@router.post("/{pneu_id}/desmontar")
async def desmontar(pneu_id: int, corpo: Desmontar, user=Depends(require_permission("reports", "read"))):
    with _con() as c:
        p = _pneu(c, user, pneu_id)
        m = _aberta(c, pneu_id)
    if m:
        await _exigir_km_coerente(m["unit_id"], p["group_id"], corpo.data, corpo.km, corpo.confirmar)
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        _recusar(p, c, "desmontar")
        m = _aberta(c, pneu_id)
        _checar_data(corpo.data, m["entrou_em"])
        km = _fechar_montagem(c, m, corpo.data, corpo.km, corpo.motivo)
        _evento(c, pneu_id, p["vida"], "desmontagem", corpo.data, user, placa=m["placa"], unit_id=m["unit_id"], posicao=m["posicao"], km=corpo.km, km_rodado=km, motivo=corpo.motivo)
        _aplicar_destino(c, p, corpo.destino, corpo.data, user, corpo.fornecedor, corpo.prazo_dias, corpo.motivo)
    return {"ok": True}


class Rodizio(BaseModel):
    posicao: str
    data: date
    km: float = Field(ge=0)
    motivo: str = Field(min_length=1)
    confirmar: bool = False


@router.post("/{pneu_id}/rodizio")
async def rodizio(pneu_id: int, corpo: Rodizio, user=Depends(require_permission("reports", "read"))):
    """Troca de posição no mesmo veículo. Com a posição ocupada, os dois trocam de lugar no mesmo km."""
    with _con() as c:
        p = _pneu(c, user, pneu_id)
        m = _aberta(c, pneu_id)
    if not m:
        raise HTTPException(409, "O pneu não está montado.")
    v = await _veiculo(p["group_id"], m["unit_id"])
    await _exigir_km_coerente(m["unit_id"], p["group_id"], corpo.data, corpo.km, corpo.confirmar)
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        m = _aberta(c, pneu_id)
        _checar_data(corpo.data, m["entrou_em"])
        if corpo.posicao == m["posicao"]:
            raise HTTPException(422, "O pneu já está nesta posição.")
        modelo = _validar_posicao(c, m["unit_id"], corpo.posicao, None)
        o = _ocupante(c, m["unit_id"], corpo.posicao, pneu_id)
        trocas = [(p, m, corpo.posicao)]
        if o:
            outra = dict(c.execute("SELECT * FROM pneu WHERE id = ?", (o["pneu_id"],)).fetchone())
            trocas.append((outra, _aberta(c, outra["id"]), m["posicao"]))
        for pneu, mont, _ in trocas:
            _fechar_montagem(c, mont, corpo.data, corpo.km, corpo.motivo)
        for pneu, mont, nova in trocas:
            _abrir_montagem(c, pneu, v, nova, modelo, corpo.data, corpo.km)
            _evento(c, pneu["id"], pneu["vida"], "rodizio", corpo.data, user, placa=v["placa"], unit_id=v["unit_id"], de=mont["posicao"], para=nova, km=corpo.km, motivo=corpo.motivo)
    return {"ok": True}


class Mover(BaseModel):
    unit_id: int
    posicao: str
    modelo_eixos: Optional[str] = None
    data: date
    km_saida: float = Field(ge=0)
    km_entrada: float = Field(ge=0)
    motivo: str = Field(min_length=1)
    confirmar: bool = False


@router.post("/{pneu_id}/mover")
async def mover(pneu_id: int, corpo: Mover, user=Depends(require_permission("reports", "read"))):
    with _con() as c:
        p = _pneu(c, user, pneu_id)
        m = _aberta(c, pneu_id)
    if not m:
        raise HTTPException(409, "O pneu não está montado.")
    if corpo.unit_id == m["unit_id"]:
        raise HTTPException(422, "Para trocar de posição no mesmo veículo, use o rodízio.")
    v = await _veiculo(p["group_id"], corpo.unit_id)
    await _exigir_km_coerente(m["unit_id"], p["group_id"], corpo.data, corpo.km_saida, corpo.confirmar)
    await _exigir_km_coerente(corpo.unit_id, p["group_id"], corpo.data, corpo.km_entrada, corpo.confirmar)
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        m = _aberta(c, pneu_id)
        _checar_data(corpo.data, m["entrou_em"])
        modelo = _validar_posicao(c, corpo.unit_id, corpo.posicao, corpo.modelo_eixos)
        o = _ocupante(c, corpo.unit_id, corpo.posicao)
        if o:
            raise HTTPException(409, {"codigo": "posicao_ocupada", "pneu_id": o["pneu_id"], "mensagem": f'A posição já tem o pneu {o["fogo"]}. Desmonte-o antes.'})
        _fechar_montagem(c, m, corpo.data, corpo.km_saida, corpo.motivo)
        _abrir_montagem(c, p, v, corpo.posicao, modelo, corpo.data, corpo.km_entrada)
        _evento(c, pneu_id, p["vida"], "mudanca_veiculo", corpo.data, user, de_placa=m["placa"], de_unit_id=m["unit_id"], para_placa=v["placa"], para_unit_id=v["unit_id"], posicao=corpo.posicao, km_saida=corpo.km_saida, km_entrada=corpo.km_entrada, motivo=corpo.motivo)
    return {"ok": True}


class DesmontarTodos(BaseModel):
    group_id: int
    data: date
    km: float = Field(ge=0)
    motivo: str = Field(min_length=1)
    destino: str = pn.ESTOQUE
    confirmar: bool = False


@router.post("/veiculos/{unit_id}/desmontar-todos")
async def desmontar_todos(unit_id: int, corpo: DesmontarTodos, user=Depends(require_permission("reports", "read"))):
    _grupos_do_usuario(user, corpo.group_id)
    await _veiculo(corpo.group_id, unit_id)
    await _exigir_km_coerente(unit_id, corpo.group_id, corpo.data, corpo.km, corpo.confirmar)
    if corpo.destino in (pn.RECAPAGEM, pn.REPARO):
        raise HTTPException(422, "Para enviar a recapagem ou reparo, desmonte um pneu de cada vez, informando o fornecedor.")
    _checar_data(corpo.data)
    with _trava, _con() as c:
        abertas = [dict(r) for r in c.execute("SELECT * FROM pneu_montagem WHERE unit_id = ? AND saiu_em IS NULL", (unit_id,))]
        for m in abertas:
            p = _pneu(c, user, m["pneu_id"])
            km = _fechar_montagem(c, m, corpo.data, corpo.km, corpo.motivo)
            _evento(c, p["id"], p["vida"], "desmontagem", corpo.data, user, placa=m["placa"], unit_id=unit_id, posicao=m["posicao"], km=corpo.km, km_rodado=km, motivo=corpo.motivo)
            _aplicar_destino(c, p, corpo.destino, corpo.data, user, None, None, corpo.motivo)
    return {"desmontados": len(abertas)}


class Ocorrencia(BaseModel):
    tipo: str
    data: date
    km: Optional[float] = Field(None, ge=0)
    gravidade: str = "media"
    custo: Optional[float] = Field(None, ge=0)
    local: Optional[str] = None
    resultado: str
    observacao: Optional[str] = None


@router.post("/{pneu_id}/ocorrencia")
async def ocorrencia(pneu_id: int, corpo: Ocorrencia, user=Depends(require_permission("reports", "read"))):
    if corpo.tipo not in pn.TIPOS_OCORRENCIA or corpo.resultado not in pn.RESULTADOS_OCORRENCIA or corpo.gravidade not in pn.GRAVIDADES:
        raise HTTPException(422, "Tipo, gravidade ou resultado inexistente.")
    if corpo.tipo == "Outro" and not (corpo.observacao or "").strip():
        raise HTTPException(422, "Descreva a ocorrência do tipo Outro.")
    _checar_data(corpo.data)
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        if p["status"] == pn.DESCARTADO:
            raise HTTPException(409, "Pneu descartado não recebe nova ocorrência.")
        m = _aberta(c, pneu_id)
        _evento(c, pneu_id, p["vida"], "ocorrencia", corpo.data, user, corpo.custo, tipo=corpo.tipo, gravidade=corpo.gravidade, km=corpo.km, local=corpo.local, resultado=corpo.resultado, observacao=corpo.observacao,
                placa=m["placa"] if m else None, unit_id=m["unit_id"] if m else None, posicao=m["posicao"] if m else None)
        if corpo.resultado == "Descartado":
            if m:
                km = corpo.km if corpo.km is not None else m["km_entrada"]
                _fechar_montagem(c, m, corpo.data, km, "Ocorrência")
            c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.DESCARTADO, pneu_id))
            _evento(c, pneu_id, p["vida"], "descarte", corpo.data, user, motivo=f"Ocorrência: {corpo.tipo}")
    return {"ok": True}


class EnvioServico(BaseModel):
    tipo: str
    fornecedor: Optional[str] = None
    data: date
    prazo_dias: Optional[int] = Field(None, ge=0)
    banda: Optional[str] = None
    custo_previsto: Optional[float] = Field(None, ge=0)


@router.post("/{pneu_id}/servico/enviar")
async def enviar_servico(pneu_id: int, corpo: EnvioServico, user=Depends(require_permission("reports", "read"))):
    """Recapagem ou reparo fora do veículo. Só pneu em estoque: montado precisa desmontar antes."""
    if corpo.tipo not in (pn.RECAPAGEM, pn.REPARO):
        raise HTTPException(422, "Serviço inexistente.")
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        _recusar(p, c, corpo.tipo)
        _checar_data(corpo.data, p["compra_em"])
        _enviar_servico(c, p, corpo.tipo, corpo.data, user, corpo.fornecedor, corpo.prazo_dias, corpo.banda, corpo.custo_previsto)
    return {"ok": True}


class RetornoServico(BaseModel):
    data: date
    custo: Optional[float] = Field(None, ge=0)
    aprovado: bool
    garantia: Optional[str] = None


@router.post("/{pneu_id}/servico/retorno")
async def retorno_servico(pneu_id: int, corpo: RetornoServico, user=Depends(require_permission("reports", "read"))):
    """Recapagem aprovada sobe a vida; reparo volta na mesma vida; reprovado vai para descarte."""
    with _trava, _con() as c:
        p = _pneu(c, user, pneu_id)
        s = _servico_aberto(c, pneu_id)
        if not s or p["status"] not in (pn.RECAPAGEM, pn.REPARO):
            raise HTTPException(409, "O pneu não está em recapagem nem em reparo.")
        _checar_data(corpo.data, s["enviado_em"])
        recap = s["tipo"] == pn.RECAPAGEM
        c.execute("UPDATE pneu_servico SET retorno_em = ?, custo = ?, resultado = ?, garantia = ? WHERE id = ?",
                  (corpo.data.isoformat(), corpo.custo, "aprovado" if corpo.aprovado else "reprovado", corpo.garantia, s["id"]))
        _evento(c, pneu_id, p["vida"], f"retorno_{s['tipo']}", corpo.data, user, corpo.custo, resultado="aprovado" if corpo.aprovado else "reprovado", fornecedor=s["fornecedor"], numero=s["numero"], garantia=corpo.garantia)
        if corpo.aprovado:
            c.execute("UPDATE pneu SET status = ?, vida = ? WHERE id = ?", (pn.ESTOQUE, p["vida"] + 1 if recap else p["vida"], pneu_id))
        else:
            c.execute("UPDATE pneu SET status = ? WHERE id = ?", (pn.DESCARTADO, pneu_id))
            _evento(c, pneu_id, p["vida"], "descarte", corpo.data, user, motivo="Carcaça reprovada na recapagem" if recap else "Reparo inviável")
    return {"ok": True}


class Config(BaseModel):
    group_id: int
    limite_recapagens: int = Field(ge=0, le=20)


@router.get("/config")
async def ler_config(group_id: int, user=Depends(require_permission("reports", "read"))):
    _grupos_do_usuario(user, group_id)
    with _con() as c:
        return {"limite_recapagens": _limite(c, group_id)}


@router.put("/config")
async def gravar_config(corpo: Config, user=Depends(require_permission("reports", "read"))):
    _grupos_do_usuario(user, corpo.group_id)
    with _trava, _con() as c:
        c.execute("INSERT INTO pneu_config (group_id, limite_recapagens) VALUES (?, ?) ON CONFLICT (group_id) DO UPDATE SET limite_recapagens = excluded.limite_recapagens", (corpo.group_id, corpo.limite_recapagens))
    return {"limite_recapagens": corpo.limite_recapagens}


@router.get("/{pneu_id}")
async def detalhe(pneu_id: int, user=Depends(require_permission("reports", "read"))):
    """Cabeçalho, km por vida e linha do tempo. Os eventos vêm das ações, sem digitação extra."""
    with _con() as c:
        p = _pneu(c, user, pneu_id)
    frota = await _frota([p["group_id"]])
    with _con() as c:
        linha = _linha(c, p, frota, _hoje())
        montagens = [dict(r) for r in c.execute("SELECT * FROM pneu_montagem WHERE pneu_id = ? ORDER BY id", (pneu_id,))]
        aberta = next((m for m in montagens if not m["saiu_em"]), None)
        v = frota.get(aberta["unit_id"]) if aberta else None
        res = pn.resumo_km(montagens, v["km"] if v else None, p["km_anterior"])
        eventos = [dict(e) for e in c.execute("SELECT * FROM pneu_evento WHERE pneu_id = ? ORDER BY em DESC, id DESC", (pneu_id,))]
        servicos = [dict(s) for s in c.execute("SELECT * FROM pneu_servico WHERE pneu_id = ? ORDER BY id", (pneu_id,))]
        limite = _limite(c, p["group_id"])
    vidas = []
    for n in range(1, p["vida"] + 1):
        s = res["por_vida"].get(n, {"km": 0, "montagens": 0})
        ev = [e for e in eventos if e["vida"] == n]
        rec = next((x for x in servicos if x["tipo"] == pn.RECAPAGEM and x["numero"] == n - 1), None)
        custo = sum(e["custo"] or 0 for e in ev if e["tipo"] == "ocorrencia" or e["tipo"] == "retorno_reparo")
        custo_base = (p["preco"] or 0) if n == 1 else (rec["custo"] or 0 if rec else 0)
        inicio = next((e["em"] for e in reversed(ev)), None)
        vidas.append({"vida": n, "rotulo": "1ª vida (novo)" if n == 1 else f"{n}ª vida ({n - 1}ª recapagem)", "km": round(s["km"]), "montagens": s["montagens"], "ocorrencias": sum(e["tipo"] == "ocorrencia" for e in ev),
                      "custo": round(custo_base + custo, 2), "inicio": inicio, "atual": n == p["vida"]})
    feitas = max(len(vidas), 1)
    return {"pneu": linha, "vidas": vidas, "km_medio_por_vida": round(res["km_total"] / feitas), "montagens": res["montagens"], "servicos": servicos,
            "limite_recapagens": limite, "limite_atingido": pn.limite_atingido(p["vida"], limite),
            "eventos": [{**e, "dados": json.loads(e["dados"]),} for e in eventos]}
