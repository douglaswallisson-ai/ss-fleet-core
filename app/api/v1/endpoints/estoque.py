"""
Estoque de equipamentos — onde está cada serial expedido, em que situação e a
qual contrato (ou aditivo) pertence.

Regra de negócio: especificação "MVP de Controle de Estoque de Equipamentos"
(Luiz Barreto, Operações, v0.3, 06/10/2026). O que mudou em relação ao protótipo:

- Base de seriais: os 2.589 seriais da planilha "Equipamentos em campo"
  (`data/estoque_base.json`, extraído do protótipo) viram o cadastro inicial.
  Expedições, manutenções e devoluções ficam gravadas (armazenamento provisório
  `data/estoque.sqlite`); no protótipo sumiam ao recarregar.
- Placa, grupo e última leitura: ao vivo do banco (só leitura), no lugar do CSV
  do Monitor de Unidades — `mova.tracked_unit_device` (rastreador/telemetria) e
  `vcms.vcms_unit_device` (câmeras), vínculo ativo = status 1 sem release_date.
- "Em estoque (desinstalado)": exato, pelo histórico de vínculos do banco
  (`tracked_unit_device` status 3 com `release_date`, ~731 mil). Resolve a
  decisão pendente 1 da especificação (o protótipo só achava 5 por pista).
- Câmeras: o serial da câmera é o `identifier` do equipamento ligado em
  `vcms_unit_device` — confirma a decisão pendente 7 sem depender da coluna
  "Modelo (Veículo)" do CSV.
- Contrato: ligado aos contratos da plataforma (contratos.py). Cliente da
  planilha → grupo pela maioria das placas onde os seriais dele estão (ou pelo
  nome); serial → contrato do grupo. Na expedição dá para escolher um aditivo,
  e o serial mostra o número dele (CT-00042-AD01).

Regras da especificação mantidas: chave = identificador normalizado + família
do modelo (identificadores curtos repetem entre modelos); status manual
(manutenção, devolução) > Ativo (tem placa) > Em estoque; alertas M, C, S, T;
expedição ignora serial que está com outro cliente; devolução confirmada leva
para "Estoque SS (matriz)" e desfaz o vínculo com o contrato.

Diferença conferida com o protótipo (06/10/2026): +110 Ativos estão ligados a
placa ativa que nunca enviou posição — o Monitor de Unidades só lista placa que
já transmitiu. Aqui contam como Ativo, com o alerta P ("placa sem comunicação").
Vínculo aberto em veículo desativado conta como Em estoque (alerta D).

SUPOSIÇÃO (saldo do contrato): o contrato conta veículos, e um veículo pode ter
rastreador e câmera. O saldo é contado por tipo — rastreadores contra os
veículos contratados e câmeras contra os veículos contratados — e não pela
soma dos dois. Confirmar com Operações (decisão pendente 2/3).
Excesso: bloqueia a expedição (como no protótipo, decisão pendente 2). Os
seriais que já estavam em campo nunca são bloqueados — só aparecem como
"acima do contratado".
"""

import json
import re
import sqlite3
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.v1.endpoints import contratos as ct
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

DADOS = Path(__file__).resolve().parents[4] / "data"
ARQUIVO = DADOS / "estoque.sqlite"
BASE = DADOS / "estoque_base.json"
MATRIZ = "Estoque SS (matriz)"
_trava = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}
CACHE_S = 120

STATUS = ("ativo", "estoque", "manutencao", "devolucao")
CAMERAS = {"MV 03", "G40 PRO", "G40 BASICA", "JIMI JC450"}
#: Família do modelo (seção 5.4): nomes da planilha e do banco que são o mesmo equipamento.
FAMILIA = {
    "VIRLOC 6": "VIRLOC 6", "VIRLOC 8": "VIRLOC 8", "VIRLOC 11": "VIRLOC 11", "VIRLOC 11 TELEMETRIA": "VIRLOC 11",
    "ST310": "ST3", "ST-310 UC2": "ST3", "ST340": "ST3", "ST-340 UR": "ST3",
    "MXT 140": "MXT 140", "MXT 141": "MXT 140", "MXT 150": "MXT 150", "MXT 151": "MXT 151",
    "GV75MG": "GV75MG", "VL CONE": "VL CONE",
    "MV 03": "MV03", "MV03": "MV03", "G40 PRO": "G40", "G40 BASICA": "G40", "G40": "G40",
    "JIMI JC450": "JC450", "JC450": "JC450",
}
PALAVRAS_VAZIAS = {"LTDA", "S/A", "SA", "ME", "EIRELI", "TRANSPORTES", "TRANSPORTE", "TRANSPORTADORA", "LOGISTICA",
                   "DE", "DA", "DO", "DOS", "DAS", "E", "GRUPO", "COMERCIO", "SERVICOS", "EMPRESA", "VIACAO"}


def familia(modelo: str) -> str:
    return FAMILIA.get((modelo or "").strip().upper(), (modelo or "").strip().upper())


def tipo_do_modelo(modelo: str) -> str:
    return "camera" if (modelo or "").strip().upper() in CAMERAS else "rastreador"


def normalizar(serial) -> str:
    """Seção 5.3: sem espaços, maiúsculas, sem '.0' do Excel, sem zeros à esquerda se numérico."""
    s = re.sub(r"\s+", "", str(serial or "")).upper()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s


def _sem_acento(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").upper()


def termos(nome: str) -> set[str]:
    return {t for t in re.split(r"[^A-Z0-9]+", _sem_acento(nome)) if len(t) >= 3 and t not in PALAVRAS_VAZIAS}


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _so_ss(user):
    if not (getattr(user, "master", 0) or getattr(user, "is_super_admin", False)):
        raise HTTPException(403, "Só a SS acessa o estoque de equipamentos.")


# ------------------------------------------------------------- armazenamento

@contextmanager
def _con():
    DADOS.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS serial (id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT NOT NULL, chave TEXT NOT NULL,
            modelo TEXT NOT NULL, cliente TEXT NOT NULL, contrato_id INTEGER, aditivo_id INTEGER, vinculo TEXT,
            status_manual TEXT, motivo TEXT, chamado TEXT, nf TEXT, origem TEXT, leitura_planilha TEXT,
            criado_em TEXT, atualizado_em TEXT, UNIQUE (chave, modelo));
        CREATE TABLE IF NOT EXISTS movimento (id INTEGER PRIMARY KEY AUTOINCREMENT, serial_id INTEGER, tipo TEXT,
            texto TEXT, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS cliente_grupo (cliente TEXT PRIMARY KEY, group_id INTEGER, origem TEXT, em TEXT);
        """
    )
    try:
        with c:
            yield c
    finally:
        c.close()


def _carregar_base():
    """Primeira vez: importa os seriais da planilha (deduplicados no protótipo: 2.603 → 2.589)."""
    with _trava, _con() as c:
        if c.execute("SELECT count(*) FROM serial").fetchone()[0] or not BASE.exists():
            return
        base = json.loads(BASE.read_text(encoding="utf-8"))
        for r in base["seriais"]:
            c.execute("""INSERT OR IGNORE INTO serial (serial, chave, modelo, cliente, origem, leitura_planilha, criado_em, atualizado_em)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (r["serial"], normalizar(r["serial"]), r["modelo"], r["cliente"], "planilha de 06/10/2026",
                       r.get("leitura_planilha") or None, _agora(), _agora()))
        c.execute("INSERT INTO movimento (serial_id, tipo, texto, em) SELECT id, 'importado', 'Importado da planilha Equipamentos em campo (06/10/2026).', ? FROM serial", (_agora(),))


# ------------------------------------------------------------- banco (leitura)

SQL_EQUIP = """
WITH d AS (
    SELECT dv.id, dv.status, dm.name AS modelo,
           CASE WHEN upper(regexp_replace(dv.identifier, '\\s', '', 'g')) ~ '^[0-9]+$'
                THEN coalesce(nullif(ltrim(regexp_replace(dv.identifier, '\\s', '', 'g'), '0'), ''), '0')
                ELSE upper(regexp_replace(dv.identifier, '\\s', '', 'g')) END AS chave
    FROM mova.device dv LEFT JOIN mova.device_model dm ON dm.id = dv.device_model_id
    WHERE dv.identifier IS NOT NULL)
SELECT d.id, d.status, d.modelo, d.chave,
       cur.unit_id, cur.placa, cur.group_id, cur.grupo, cur.desde, morto.placa AS placa_desativada,
       ant.placa AS placa_anterior, ant.retirado_em, s.local_time AS ultima_leitura
FROM d
LEFT JOIN LATERAL (
    SELECT tu.id AS unit_id, tu.label AS placa, tu.group_id, g.name AS grupo, x.desde FROM (
        SELECT tud.tracked_unit_id AS unit_id, tud.association_date AS desde FROM mova.tracked_unit_device tud
        WHERE tud.device_id = d.id AND tud.status = 1
        UNION ALL
        SELECT v.unit_id, v.association_date FROM vcms.vcms_unit_device v
        WHERE v.device_id = d.id AND v.status = 1 AND v.release_date IS NULL) x
    JOIN mova.tracked_unit tu ON tu.id = x.unit_id AND tu.status = 1 LEFT JOIN mova."group" g ON g.id = tu.group_id
    ORDER BY x.desde DESC NULLS LAST LIMIT 1) cur ON true
LEFT JOIN LATERAL (
    SELECT tu.label AS placa FROM mova.tracked_unit_device tud JOIN mova.tracked_unit tu ON tu.id = tud.tracked_unit_id
    WHERE tud.device_id = d.id AND tud.status = 1 AND tu.status <> 1 LIMIT 1) morto ON cur.unit_id IS NULL
LEFT JOIN LATERAL (
    SELECT tu.label AS placa, y.release_date AS retirado_em FROM (
        SELECT tud.tracked_unit_id AS unit_id, tud.release_date FROM mova.tracked_unit_device tud
        WHERE tud.device_id = d.id AND tud.release_date IS NOT NULL
        UNION ALL
        SELECT v.unit_id, v.release_date FROM vcms.vcms_unit_device v WHERE v.device_id = d.id AND v.release_date IS NOT NULL) y
    JOIN mova.tracked_unit tu ON tu.id = y.unit_id ORDER BY y.release_date DESC LIMIT 1) ant ON cur.unit_id IS NULL
LEFT JOIN mova.dev_status s ON s.unit_id = cur.unit_id
WHERE d.chave = ANY(CAST(:chaves AS text[]))
"""


async def _equipamentos(chaves: list[str]) -> list[dict]:
    hit = _CACHE.get("equip")
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]  # type: ignore[return-value]
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '90s'"))
        rows = [dict(r) for r in (await db.execute(text(SQL_EQUIP), {"chaves": chaves})).mappings().all()]
        grupos = [dict(r) for r in (await db.execute(text('SELECT id, name FROM mova."group"'))).mappings().all()]
    _CACHE["equip"] = (time.time(), rows)
    _CACHE["grupos"] = (time.time(), grupos)
    return rows


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ------------------------------------------------------------------ cálculo

def casar(serial: dict, por_chave: dict[str, list[dict]]) -> tuple[Optional[dict], Optional[dict]]:
    """(equipamento da mesma família, equipamento instalado de OUTRA família com o mesmo número)."""
    cands = por_chave.get(serial["chave"], [])
    fam = familia(serial["modelo"])
    longo = len(serial["chave"]) >= 9
    mesmo = [e for e in cands if familia(e["modelo"]) == fam or (longo and tipo_do_modelo(serial["modelo"]) == "camera")]
    # Preferência: instalado, depois o equipamento ativo no cadastro.
    mesmo.sort(key=lambda e: (e["unit_id"] is None, e["status"] != 1))
    outro = next((e for e in cands if e not in mesmo and e["unit_id"] is not None), None)
    return (mesmo[0] if mesmo else None), outro


def status_de(s: dict, eq: Optional[dict]) -> tuple[str, Optional[str], str]:
    """(status, subtipo, explicação). Ordem: manual > tem placa > em estoque."""
    if s["status_manual"] == "manutencao":
        return "manutencao", None, f"Lançado manualmente em manutenção{': ' + s['motivo'] if s['motivo'] else ''}. Não é recalculado pelo banco até concluir."
    if s["status_manual"] == "devolucao":
        return "devolucao", None, f"Devolução iniciada{': ' + s['motivo'] if s['motivo'] else ''}. Aguarda o recebimento na matriz."
    if s["cliente"] == MATRIZ:
        return "estoque", "matriz", "Devolvido e recebido na matriz da SS Telemática. Pode ser expedido de novo."
    if eq and eq["unit_id"]:
        return "ativo", None, f"Instalado na placa {eq['placa']} ({eq['grupo'] or 'sem grupo'}) desde {str(_iso(eq['desde']) or '')[:10] or 'data não registrada'}."
    if eq and eq["placa_desativada"]:
        return "estoque", "desinstalado", f"Com o cliente e sem placa ativa. Continua ligado ao veículo {eq['placa_desativada']}, que foi desativado."
    if eq and eq["placa_anterior"]:
        return "estoque", "desinstalado", f"Com o cliente e sem placa. Estava na placa {eq['placa_anterior']} até {str(_iso(eq['retirado_em']))[:10]}."
    if eq:
        return "estoque", None, "Com o cliente e nunca instalado em placa (cadastrado no sistema, sem vínculo)."
    return "estoque", None, "Com o cliente e não encontrado no cadastro de equipamentos da plataforma."


def alertas_de(s: dict, eq: Optional[dict], outro: Optional[dict]) -> list[dict]:
    a = []
    if not (eq and eq["unit_id"]) and outro:
        a.append({"codigo": "M", "texto": f"O número {s['serial']} está instalado como {outro['modelo']} na placa {outro['placa']}, mas foi expedido como {s['modelo']}. Corrigir o modelo."})
    if eq and eq["unit_id"]:
        grupo = eq["grupo"] or ""
        if "SS TELEMATICA" in _sem_acento(grupo):
            if "SS TELEMATICA" not in _sem_acento(s["cliente"]):
                a.append({"codigo": "S", "texto": f"Instalado em placa do grupo SS Telemática ({eq['placa']}), não do cliente {s['cliente']}. Placa de teste ou bancada?"})
        elif s["cliente"] != MATRIZ and not mesmo_cliente(grupo, s["cliente"]):
            a.append({"codigo": "C", "texto": f"Expedido para {s['cliente']}, mas instalado em placa do grupo {grupo}. Transferência ou erro de cadastro?"})
    if eq and eq["unit_id"] and not eq["ultima_leitura"]:
        a.append({"codigo": "P", "texto": f"Instalado na placa {eq['placa']}, mas a placa nunca enviou posição. Conferir a instalação."})
    if eq and eq.get("placa_desativada"):
        a.append({"codigo": "D", "texto": f"Ainda ligado ao veículo {eq['placa_desativada']}, que está desativado. Liberar o vínculo no cadastro."})
    leit = (s.get("leitura_planilha") or "").lower()
    if not (eq and eq["unit_id"]) and (leit.startswith("hoje") or leit.startswith("ontem")):
        a.append({"codigo": "T", "texto": f"Sem placa, mas a planilha de 06/10 mostra leitura \"{s['leitura_planilha']}\"."})
    return a


def mesmo_cliente(grupo: str, cliente: str) -> bool:
    """Grupo da placa e cliente da expedição são o mesmo cliente? (alerta C)

    Compara primeiro os termos significativos; quando um dos nomes só tem sigla
    curta ("FM TRANSPORTES", "T&J LOGISTICA"), compara todas as palavras.
    """
    a, b = _sem_acento(grupo).strip(), _sem_acento(cliente).strip()
    if a == b:
        return True
    ta, tb = termos(a), termos(b)
    if ta and tb:
        return bool(ta & tb)
    pa = {t for t in re.split(r"[^A-Z0-9&]+", a) if len(t) >= 2}
    pb = {t for t in re.split(r"[^A-Z0-9&]+", b) if len(t) >= 2}
    return bool(pa & pb)


def grupo_do_cliente(cliente: str, placas_por_grupo: Counter, grupos: list[dict]) -> tuple[Optional[int], str]:
    """Maioria das placas (fora o grupo SS); sem placa, o grupo com mais termos do nome em comum."""
    ss = {g["id"] for g in grupos if "SS TELEMATICA" in _sem_acento(g["name"] or "")}
    for gid, _ in placas_por_grupo.most_common():
        if gid not in ss:
            return gid, "placas"
    alvo = termos(cliente)
    melhor = max(grupos, key=lambda g: len(termos(g["name"] or "") & alvo), default=None)
    if melhor and len(termos(melhor["name"] or "") & alvo) >= max(1, min(2, len(alvo))):
        return melhor["id"], "nome"
    return None, "sem_grupo"


async def _montar() -> dict:
    _carregar_base()
    with _trava, _con() as c:
        seriais = [dict(r) for r in c.execute("SELECT * FROM serial")]
        mapa = {r["cliente"]: dict(r) for r in c.execute("SELECT * FROM cliente_grupo")}
    equips = await _equipamentos(sorted({s["chave"] for s in seriais}))
    grupos = _CACHE["grupos"][1]  # type: ignore[index]
    por_chave: dict[str, list[dict]] = {}
    for e in equips:
        por_chave.setdefault(e["chave"], []).append(e)

    # contratos e aditivos
    await ct._sincronizar()
    with ct._trava, ct._con() as c:
        contratos = {r["id"]: dict(r) for r in c.execute("SELECT * FROM contrato")}
        ads = ct._aditivos(c)
    contrato_do_grupo = {r["group_id"]: cid for cid, r in contratos.items() if r["group_id"]}
    aditivo_por_id = {a["id"]: a for lst in ads.values() for a in lst}

    casados = []
    placas_por_cliente: dict[str, Counter] = {}
    for s in seriais:
        eq, outro = casar(s, por_chave)
        casados.append((s, eq, outro))
        if eq and eq["unit_id"] and eq["group_id"]:
            placas_por_cliente.setdefault(s["cliente"], Counter())[eq["group_id"]] += 1

    # cliente → grupo (o que foi escolhido à mão prevalece)
    cliente_grupo: dict[str, tuple[Optional[int], str]] = {}
    for cli in {s["cliente"] for s in seriais if s["cliente"] != MATRIZ}:
        if cli in mapa:
            cliente_grupo[cli] = (mapa[cli]["group_id"], mapa[cli]["origem"])
        else:
            cliente_grupo[cli] = grupo_do_cliente(cli, placas_por_cliente.get(cli, Counter()), grupos)

    itens = []
    for s, eq, outro in casados:
        st, sub, porque = status_de(s, eq)
        gid = cliente_grupo.get(s["cliente"], (None, ""))[0]
        cid = s["contrato_id"] if s["vinculo"] == "manual" else (s["contrato_id"] or contrato_do_grupo.get(gid))
        if s["cliente"] == MATRIZ:
            cid = None
        contrato = contratos.get(cid) if cid else None
        aditivo = aditivo_por_id.get(s["aditivo_id"]) if s["aditivo_id"] else None
        cdados = json.loads(contrato["dados"]) if contrato else {}
        itens.append({
            "id": s["id"], "serial": s["serial"], "modelo": s["modelo"], "tipo": tipo_do_modelo(s["modelo"]), "cliente": s["cliente"],
            "status": st, "subtipo": sub, "explicacao": porque, "origem_status": "manual" if s["status_manual"] else "calculado",
            "placa": eq["placa"] if eq and eq["unit_id"] else None, "grupo_placa": eq["grupo"] if eq and eq["unit_id"] else None,
            "placa_anterior": eq["placa_anterior"] if eq else None, "retirado_em": _iso(eq["retirado_em"]) if eq else None,
            "ultima_leitura": _iso(eq["ultima_leitura"]) if eq and eq["unit_id"] else None,
            "leitura_planilha": s["leitura_planilha"], "no_cadastro": eq is not None,
            "contrato_id": cid, "contrato_numero": ct.numero_sistema(cid) if cid else None,
            "contrato_nome": cdados.get("nome_grupo") if contrato else None,
            "aditivo_id": aditivo["id"] if aditivo else None, "aditivo_numero": aditivo["numero"] if aditivo else None,
            "vinculo": None if not cid else (s["vinculo"] or "automatico"),
            "alertas": alertas_de(s, eq, outro), "nf": s["nf"], "motivo": s["motivo"], "chamado": s["chamado"],
            "origem": s["origem"],
        })

    # consumo por contrato (por tipo de equipamento; ver SUPOSIÇÃO no topo)
    painel = {}
    for cid, row in contratos.items():
        d = json.loads(row["dados"])
        tot = ct.totais_com_aditivos(d, ads.get(cid, []))
        painel[cid] = {"id": cid, "numero": ct.numero_sistema(cid), "nome": d.get("nome_grupo"), "status": row["status"],
                       "fim": d.get("data_fim"), "vencido": bool(ct._calcular(d)["vencido"]), "contratado": tot["qtd_veiculos_total"],
                       "rastreadores": 0, "cameras": 0,
                       "aditivos": [{"id": a["id"], "numero": a["numero"], "qtd": a["qtd_veiculos"], "tipo": a["tipo"]}
                                    for a in ads.get(cid, []) if a["status"] == "ativo"]}
    for i in itens:
        if i["contrato_id"] in painel and i["status"] != "devolucao":
            painel[i["contrato_id"]]["rastreadores" if i["tipo"] == "rastreador" else "cameras"] += 1
    for p in painel.values():
        q = p["contratado"]
        p["saldo_rastreadores"] = None if q is None else q - p["rastreadores"]
        p["saldo_cameras"] = None if q is None else q - p["cameras"]
        p["acima"] = q is not None and (p["rastreadores"] > q or p["cameras"] > q)

    clientes = []
    for cli in sorted({i["cliente"] for i in itens}):
        cont = Counter(i["status"] for i in itens if i["cliente"] == cli)
        gid, como = cliente_grupo.get(cli, (None, ""))
        cid = contrato_do_grupo.get(gid)
        clientes.append({"cliente": cli, "total": sum(cont.values()), **{k: cont.get(k, 0) for k in STATUS},
                         "group_id": gid, "grupo": next((g["name"] for g in grupos if g["id"] == gid), None),
                         "como_ligado": como, "contrato_id": cid})
    return {"itens": itens, "clientes": clientes, "contratos": painel}


# ------------------------------------------------------------------ endpoints

@router.get("")
async def listar(user=Depends(require_permission("reports", "read"))):
    _so_ss(user)
    m = await _montar()
    usados = {i["contrato_id"] for i in m["itens"] if i["contrato_id"]} | {c["contrato_id"] for c in m["clientes"] if c["contrato_id"]}
    return {
        "itens": m["itens"], "clientes": m["clientes"],
        "contratos": [m["contratos"][c] for c in sorted(usados) if c in m["contratos"]],
        "resumo": {"total": len(m["itens"]), **Counter(i["status"] for i in m["itens"]),
                   "com_alerta": sum(1 for i in m["itens"] if i["alertas"]),
                   "sem_contrato": sum(1 for i in m["itens"] if not i["contrato_id"])},
        "atualizado_em": _agora(),
    }


@router.get("/contratos-do-cliente")
async def contratos_do_cliente(cliente: str, user=Depends(require_permission("reports", "read"))):
    """Contratos que a expedição pode usar para o cliente (o do grupo dele e os já usados por ele)."""
    _so_ss(user)
    m = await _montar()
    cli = next((c for c in m["clientes"] if c["cliente"] == cliente), None)
    ids = {i["contrato_id"] for i in m["itens"] if i["cliente"] == cliente and i["contrato_id"]}
    if cli and cli["contrato_id"]:
        ids.add(cli["contrato_id"])
    return {"contratos": [m["contratos"][c] for c in sorted(ids) if c in m["contratos"]]}


class Expedicao(BaseModel):
    cliente: str
    contrato_id: int
    aditivo_id: Optional[int] = None
    modelo: str
    seriais: str
    nf: Optional[str] = None


def separar_seriais(txt: str) -> list[str]:
    vistos, out = set(), []
    for p in re.split(r"[\s,;]+", txt or ""):
        if p and normalizar(p) not in vistos:
            vistos.add(normalizar(p))
            out.append(p.strip())
    return out


def checar_saldo(contrato: dict, tipo: str, qtd: int):
    """Bloqueios da expedição (seção 8)."""
    if contrato["status"] == "encerrado":
        raise HTTPException(409, {"message": f"O contrato {contrato['numero']} está encerrado. Não é possível expedir.", "field": "contrato_id"})
    saldo = contrato["saldo_cameras" if tipo == "camera" else "saldo_rastreadores"]
    if saldo is None:
        return "O contrato não tem quantidade de veículos informada: expedição liberada sem conferir saldo."
    if saldo <= 0:
        raise HTTPException(409, {"message": f"O contrato {contrato['numero']} não tem saldo de {'câmeras' if tipo == 'camera' else 'rastreadores'}. Cadastre um aditivo.", "field": "contrato_id"})
    if qtd > saldo:
        raise HTTPException(409, {"message": f"São {qtd} seriais e o contrato {contrato['numero']} tem saldo de {saldo}. Cadastre um aditivo de {qtd - saldo} veículo(s).", "field": "seriais"})
    return None


@router.post("/expedicao")
async def expedir(p: Expedicao, user=Depends(require_permission("reports", "read"))):
    _so_ss(user)
    if not p.cliente.strip() or p.cliente == MATRIZ:
        raise HTTPException(422, {"message": "Escolha o cliente.", "field": "cliente"})
    if p.modelo not in FAMILIA:
        raise HTTPException(422, {"message": "Escolha o modelo dos seriais.", "field": "modelo"})
    lista = separar_seriais(p.seriais)
    if not lista:
        raise HTTPException(422, {"message": "Cole pelo menos um serial.", "field": "seriais"})
    m = await _montar()
    contrato = m["contratos"].get(p.contrato_id)
    if not contrato:
        raise HTTPException(422, {"message": "Escolha o contrato.", "field": "contrato_id"})
    if p.aditivo_id and p.aditivo_id not in {a["id"] for a in contrato["aditivos"]}:
        raise HTTPException(422, {"message": "Este aditivo não é do contrato escolhido (ou está cancelado).", "field": "aditivo_id"})
    por_chave_mod = {(normalizar(i["serial"]), familia(i["modelo"])): i for i in m["itens"]}
    novos, ignorados, mesmo_cliente = [], [], []
    for s in lista:
        atual = por_chave_mod.get((normalizar(s), familia(p.modelo)))
        if atual and atual["cliente"] not in (MATRIZ,):
            (mesmo_cliente if atual["cliente"] == p.cliente else ignorados).append(f"{s} ({atual['cliente']})")
        else:
            novos.append((s, atual))
    aviso = checar_saldo(contrato, tipo_do_modelo(p.modelo), len(novos)) if novos else None
    with _trava, _con() as c:
        for s, atual in novos:
            txt = f"Expedido para {p.cliente} no contrato {contrato['numero']}" + (f", aditivo {next(a['numero'] for a in contrato['aditivos'] if a['id'] == p.aditivo_id)}" if p.aditivo_id else "") + (f", NF {p.nf}" if p.nf else "") + "."
            if atual:  # volta da matriz
                c.execute("""UPDATE serial SET cliente = ?, contrato_id = ?, aditivo_id = ?, vinculo = 'manual', nf = ?, status_manual = NULL,
                             motivo = NULL, atualizado_em = ? WHERE id = ?""", (p.cliente, p.contrato_id, p.aditivo_id, p.nf, _agora(), atual["id"]))
                sid = atual["id"]
            else:
                c.execute("""INSERT INTO serial (serial, chave, modelo, cliente, contrato_id, aditivo_id, vinculo, nf, origem, criado_em, atualizado_em)
                             VALUES (?,?,?,?,?,?,'manual',?,'expedição',?,?)""",
                          (s, normalizar(s), p.modelo, p.cliente, p.contrato_id, p.aditivo_id, p.nf, _agora(), _agora()))
                sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO movimento (serial_id, tipo, texto, autor, em) VALUES (?,?,?,?,?)", (sid, "expedicao", txt, getattr(user, "user_id", None), _agora()))
    _CACHE.pop("equip", None)
    return {"expedidos": [s for s, _ in novos], "ignorados": ignorados, "ja_no_cliente": mesmo_cliente, "aviso": aviso}


class Lancamento(BaseModel):
    acao: str  # manutencao | concluir_manutencao | devolucao | confirmar_devolucao | cancelar_devolucao | vincular
    motivo: Optional[str] = None
    chamado: Optional[str] = None
    nf: Optional[str] = None
    contrato_id: Optional[int] = None
    aditivo_id: Optional[int] = None


@router.post("/{sid}/lancamento")
async def lancar(sid: int, p: Lancamento, user=Depends(require_permission("reports", "read"))):
    _so_ss(user)
    with _trava, _con() as c:
        s = c.execute("SELECT * FROM serial WHERE id = ?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "Serial não encontrado.")
    s = dict(s)
    sets, txt = {}, ""
    if p.acao == "manutencao":
        if s["status_manual"]:
            raise HTTPException(409, "O serial já tem um lançamento em aberto.")
        if not (p.motivo or "").strip():
            raise HTTPException(422, {"message": "Informe o motivo da manutenção.", "field": "motivo"})
        sets = {"status_manual": "manutencao", "motivo": p.motivo.strip(), "chamado": p.chamado}
        txt = f"Enviado para manutenção: {p.motivo.strip()}" + (f" (chamado {p.chamado})" if p.chamado else "") + "."
    elif p.acao == "concluir_manutencao":
        if s["status_manual"] != "manutencao":
            raise HTTPException(409, "O serial não está em manutenção.")
        sets, txt = {"status_manual": None, "motivo": None, "chamado": None}, "Manutenção concluída. Volta ao status calculado."
    elif p.acao == "devolucao":
        if s["status_manual"]:
            raise HTTPException(409, "O serial já tem um lançamento em aberto.")
        if s["cliente"] == MATRIZ:
            raise HTTPException(409, "O serial já está na matriz.")
        if not (p.motivo or "").strip():
            raise HTTPException(422, {"message": "Informe o motivo da devolução.", "field": "motivo"})
        sets = {"status_manual": "devolucao", "motivo": p.motivo.strip(), "nf": p.nf or s["nf"]}
        txt = f"Devolução iniciada: {p.motivo.strip()}" + (f" (NF de retorno {p.nf})" if p.nf else "") + "."
    elif p.acao == "confirmar_devolucao":
        if s["status_manual"] != "devolucao":
            raise HTTPException(409, "Não há devolução em andamento.")
        sets = {"status_manual": None, "motivo": None, "cliente": MATRIZ, "contrato_id": None, "aditivo_id": None, "vinculo": "manual"}
        txt = f"Recebido na matriz. Saiu do cliente {s['cliente']} e foi desvinculado do contrato."
    elif p.acao == "cancelar_devolucao":
        if s["status_manual"] != "devolucao":
            raise HTTPException(409, "Não há devolução em andamento.")
        sets, txt = {"status_manual": None, "motivo": None}, "Devolução cancelada. Volta ao status calculado."
    elif p.acao == "vincular":
        if s["cliente"] == MATRIZ:
            raise HTTPException(409, "Serial na matriz não tem contrato. Expeça para um cliente.")
        if p.contrato_id:
            m = await _montar()
            cont = m["contratos"].get(p.contrato_id)
            if not cont:
                raise HTTPException(422, {"message": "Contrato não encontrado.", "field": "contrato_id"})
            if p.aditivo_id and p.aditivo_id not in {a["id"] for a in cont["aditivos"]}:
                raise HTTPException(422, {"message": "Este aditivo não é do contrato escolhido.", "field": "aditivo_id"})
            sets = {"contrato_id": p.contrato_id, "aditivo_id": p.aditivo_id, "vinculo": "manual"}
            txt = f"Vinculado ao contrato {cont['numero']}" + (f", aditivo {next(a['numero'] for a in cont['aditivos'] if a['id'] == p.aditivo_id)}" if p.aditivo_id else "") + "."
        else:
            sets, txt = {"contrato_id": None, "aditivo_id": None, "vinculo": None}, "Vínculo manual removido; volta ao contrato do grupo do cliente."
    else:
        raise HTTPException(422, "Ação inválida.")
    with _trava, _con() as c:
        cols = ", ".join(f"{k} = ?" for k in sets) + ", atualizado_em = ?"
        c.execute(f"UPDATE serial SET {cols} WHERE id = ?", (*sets.values(), _agora(), sid))  # noqa: S608 (colunas fixas acima)
        c.execute("INSERT INTO movimento (serial_id, tipo, texto, autor, em) VALUES (?,?,?,?,?)", (sid, p.acao, txt, getattr(user, "user_id", None), _agora()))
    return {"ok": True, "texto": txt}


@router.get("/{sid}/historico")
async def historico(sid: int, user=Depends(require_permission("reports", "read"))):
    _so_ss(user)
    with _trava, _con() as c:
        return {"data": [dict(r) for r in c.execute("SELECT * FROM movimento WHERE serial_id = ? ORDER BY id DESC", (sid,))]}


class LigarCliente(BaseModel):
    cliente: str
    group_id: Optional[int] = None


@router.post("/cliente-grupo")
async def ligar_cliente(p: LigarCliente, user=Depends(require_permission("reports", "read"))):
    """Corrige à mão qual grupo (e contrato) é o do cliente da planilha."""
    _so_ss(user)
    with _trava, _con() as c:
        if p.group_id is None:
            c.execute("DELETE FROM cliente_grupo WHERE cliente = ?", (p.cliente,))
        else:
            c.execute("INSERT OR REPLACE INTO cliente_grupo (cliente, group_id, origem, em) VALUES (?,?,?,?)", (p.cliente, p.group_id, "manual", _agora()))
    return {"ok": True}
