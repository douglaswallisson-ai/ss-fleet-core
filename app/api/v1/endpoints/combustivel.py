"""
Controle de Combustível — o mesmo módulo do time de TI ("Controle de Combustível
V2", ss-bi-start /fuel + ss-fleet-core fuel_*.py), lido em 04/10/2026 e trazido
para a plataforma nova a pedido do PM: "é exatamente daquele jeito que eu
gostaria que fosse nosso módulo".

Leitura (banco, só leitura):
- `mova.fuel_supply`: abastecimentos (CTA Smart, RDP Online, manual, planilha,
  migrados), com o tipo, o fornecedor, o posto e o motorista;
- `mova.fuel_supply_telemetry`: conferência do km informado com o rastreador,
  feita pelo serviço ss-fueling-integration (OK, DIVERGENTE, SEM_TELEMETRIA,
  SEM_KM_INFORMADO);
- `mova.fuel_unit_profile`: tanque e faixa de km/L esperada por veículo;
- `mova.fuel_station`: postos; `mova.fuel_supply_staging`: transações dos
  cartões que não viraram abastecimento (pendências).

Cálculo: igual ao da view `mova.v_fuel_supply_calc` (km/L por ciclo de tanque
cheio a tanque cheio) e às regras de `app/core/fuel_calc.py` (alertas, faixa
de consumo, duplicados, média do período sem ciclos com erro). Refeito em
Python para que o que o usuário lança ou corrige aqui entre na mesma conta.

⚠️ ARMAZENAMENTO PROVISÓRIO: o banco de produção é só leitura (regra do PM).
Lançamentos, correções, exclusões, verificações de alerta, perfis, postos e
pendências resolvidas ficam em `data/combustivel.sqlite` e aparecem junto com
o dado real. Não chegam ao sistema do time até a gravação ser liberada.
"""

import json
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import fuel_calc as F
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "combustivel.sqlite"
_trava = threading.Lock()
_CACHE: dict[int, tuple[float, dict]] = {}
CACHE_S = 60
ID_LOCAL = 900_000_000  # ids dos lançamentos feitos aqui (não colidem com o banco)
ID_POSTO_LOCAL = 900_000

# Campos que o usuário pode corrigir, por origem (mesma regra do time):
# integração (CTA/RDP) só km, tanque cheio, motorista e observação.
EDITAVEIS_TODOS = ["event_datetime", "fuel_type_id", "liters", "price_per_liter", "km_informado_atual",
                   "full_tank", "fuel_station_id", "local_informado", "driver_id",
                   "total_value_informed", "invoice_number", "notes"]
EDITAVEIS_INTEGRACAO = ["km_informado_atual", "full_tank", "driver_id", "notes"]
AFETAM_TELEMETRIA = {"event_datetime", "km_informado_atual", "fuel_type_id"}


def editaveis(codigo_origem: Optional[str]) -> list[str]:
    return list(EDITAVEIS_TODOS) if codigo_origem in ("MANUAL", "PLANILHA", None) else list(EDITAVEIS_INTEGRACAO)


# --------------------------------------------------------------- armazenamento

def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS lancamento (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL, dados TEXT NOT NULL, autor INTEGER, criado_em TEXT);
        CREATE TABLE IF NOT EXISTS edicao (supply_id INTEGER PRIMARY KEY, dados TEXT NOT NULL, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS exclusao (supply_id INTEGER PRIMARY KEY, motivo TEXT NOT NULL, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS revisao (supply_id INTEGER PRIMARY KEY, motivo TEXT NOT NULL, autor INTEGER, autor_nome TEXT, em TEXT);
        CREATE TABLE IF NOT EXISTS perfil (unit_id INTEGER PRIMARY KEY, dados TEXT NOT NULL, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS pendencia (staging_id INTEGER PRIMARY KEY, status TEXT NOT NULL, unit_id INTEGER,
            supply_id INTEGER, nota TEXT, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS posto (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
            origem_id INTEGER, dados TEXT NOT NULL, ativo INTEGER DEFAULT 1, autor INTEGER, em TEXT);
        CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY AUTOINCREMENT, supply_id INTEGER, acao TEXT,
            dados TEXT, motivo TEXT, autor INTEGER, em TEXT);
        INSERT OR IGNORE INTO sqlite_sequence(name, seq) SELECT 'lancamento', {ID_LOCAL}
            WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'lancamento');
        INSERT OR IGNORE INTO sqlite_sequence(name, seq) SELECT 'posto', {ID_POSTO_LOCAL}
            WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'posto');
        """
    )
    return c


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _uid(user) -> Optional[int]:
    return getattr(user, "user_id", None)


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


def _historico(c, supply_id, acao, dados, motivo, user):
    c.execute("INSERT INTO historico (supply_id, acao, dados, motivo, autor, em) VALUES (?,?,?,?,?,?)",
              (supply_id, acao, json.dumps(dados, default=str), motivo, _uid(user), _agora()))


def _invalidar(group_id: int):
    _CACHE.pop(group_id, None)


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


def _f(v):
    return float(v) if v is not None else None


def _dt(v) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).replace("T", " ").strip()
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%d/%m/%Y %H:%M", 16), ("%Y-%m-%d", 10), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------- leitura do banco

SQL_ABAST = """
SELECT s.id, s.unit_id, s.group_id, s.subgroup_id, s.driver_id, d.name AS driver_name,
       s.fuel_type_id, ft.code AS ft_code, ft.name AS ft_name, s.fuel_station_id, st.name AS station_name,
       st.cnpj AS station_cnpj, s.local_informado, s.fuel_supplier_id, sp.code AS sup_code, sp.name AS sup_name,
       s.external_ref, s.event_datetime, s.km_informado_atual, s.liters, s.price_per_liter, s.total_value,
       s.total_value_informed, s.full_tank, s.invoice_number, s.notes, s.reviewed_at, s.review_reason,
       s.reviewed_by, s.telemetry_stale_at, s.date_add,
       t.classification, t.odom_telemetry_before, t.local_time_telemetry_before, t.km_telemetry,
       t.divergence_km, t.divergence_pct, t.processed_at
FROM mova.fuel_supply s
JOIN mova.fuel_type ft ON ft.id = s.fuel_type_id
JOIN mova.fuel_supplier sp ON sp.id = s.fuel_supplier_id
LEFT JOIN mova.fuel_station st ON st.id = s.fuel_station_id
LEFT JOIN mova.driver d ON d.id = s.driver_id
LEFT JOIN mova.fuel_supply_telemetry t ON t.fuel_supply_id = s.id
WHERE s.status = 1 AND s.group_id = :g
"""

SQL_VEICULOS = """
SELECT tu.id, tu.label, tu.label2, tu.subgroup_id, sg.name AS subgroup_name, tu.date_add, tu.status
FROM mova.tracked_unit tu LEFT JOIN mova.subgroup sg ON sg.id = tu.subgroup_id
WHERE tu.group_id = :g AND tu.status = 1
"""


async def _carregar_banco(g: int) -> dict:
    abast = await _ler(SQL_ABAST, {"g": g})
    veiculos = await _ler(SQL_VEICULOS, {"g": g})
    ids = [v["id"] for v in veiculos]
    perfis = await _ler(
        "SELECT unit_id, default_fuel_type, tank_capacity_l, expected_kml_min, expected_kml_max, meter "
        "FROM mova.fuel_unit_profile WHERE unit_id = ANY(CAST(:ids AS bigint[]))", {"ids": ids}) if ids else []
    tipos = await _ler("SELECT id, code, name FROM mova.fuel_type WHERE status = 1 ORDER BY id", {})
    fornecedores = await _ler("SELECT id, code, name, integration_type FROM mova.fuel_supplier WHERE status = 1 ORDER BY id", {})
    postos = await _ler(
        "SELECT id, name, pump_cod, company, branch, fuel_type_id, price_per_liter, cnpj, latitude, longitude, radius_m, subgroup_id "
        "FROM mova.fuel_station WHERE status = 1 AND group_id = :g", {"g": g})
    nomes = await _ler("SELECT name FROM mova.\"group\" WHERE id = :g", {"g": g})
    return {"abast": [dict(r) for r in abast], "veiculos": {v["id"]: dict(v) for v in veiculos},
            "perfis": {p["unit_id"]: dict(p) for p in perfis}, "tipos": [dict(t) for t in tipos],
            "fornecedores": [dict(f) for f in fornecedores], "postos": [dict(p) for p in postos],
            "grupo_nome": nomes[0]["name"] if nomes else None}


async def _telemetria(unit_id: int, quando: datetime, km: Optional[float]) -> dict:
    """Mesma conferência do serviço do time: leitura do rastreador mais próxima
    ANTES do abastecimento (até 3 dias), odômetro em metros ÷ 1000; DIVERGENTE
    se a diferença passar de 5 km."""
    antes = await _ler(
        "SELECT local_time, odom FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :i AND local_time <= :q "
        "AND odom IS NOT NULL AND odom > 0 ORDER BY local_time DESC LIMIT 1",
        {"u": unit_id, "i": quando - timedelta(days=3), "q": quando})
    depois = await _ler(
        "SELECT local_time, odom FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :q AND local_time <= :f "
        "AND odom IS NOT NULL AND odom > 0 ORDER BY local_time ASC LIMIT 1",
        {"u": unit_id, "q": quando, "f": quando + timedelta(days=3)})
    a = float(antes[0]["odom"]) / 1000 if antes else None
    d = float(depois[0]["odom"]) / 1000 if depois else None
    if a is None:
        return {"classification": "SEM_TELEMETRIA"}
    if km is None:
        return {"classification": "SEM_KM_INFORMADO", "odometer": a, "read_at": str(antes[0]["local_time"])}
    div = km - a
    return {"classification": "OK" if abs(div) <= F.TELEMETRY_CLASSIFICATION_TOLERANCE_KM else "DIVERGENTE",
            "odometer": round(a, 1), "read_at": str(antes[0]["local_time"]),
            "km_telemetry": round(d - a, 1) if d is not None else None,
            "divergence_km": round(div, 1), "divergence_pct": round(div / a * 100, 2) if a else None}


# --------------------------------------------------------------- montagem

def _aplicar_overlay(g: int, banco: dict) -> dict:
    with _trava, _con() as c:
        edic = {r["supply_id"]: json.loads(r["dados"]) for r in c.execute("SELECT * FROM edicao")}
        excl = {r["supply_id"]: dict(r) for r in c.execute("SELECT * FROM exclusao")}
        rev = {r["supply_id"]: dict(r) for r in c.execute("SELECT * FROM revisao")}
        perf = {r["unit_id"]: json.loads(r["dados"]) for r in c.execute("SELECT * FROM perfil")}
        lanc = [dict(r) for r in c.execute("SELECT * FROM lancamento WHERE group_id = ?", (g,))]
        postos_loc = [dict(r) for r in c.execute("SELECT * FROM posto WHERE group_id = ?", (g,))]
    tipos = {t["id"]: t for t in banco["tipos"]}
    forn = {f["code"]: f for f in banco["fornecedores"]}
    postos = {p["id"]: dict(p) for p in banco["postos"]}
    for p in postos_loc:
        d = json.loads(p["dados"])
        alvo = p["origem_id"] or p["id"]
        if not p["ativo"]:
            postos.pop(alvo, None)
            continue
        postos[alvo] = {**postos.get(alvo, {}), **d, "id": alvo, "provisorio": True}

    linhas = []
    for r in banco["abast"]:
        if r["id"] in excl:
            continue
        x = dict(r)
        x["provisorio"] = False
        if r["id"] in edic:
            e = edic[r["id"]]
            for k, v in e.items():
                if k == "telemetria":
                    continue
                x[k] = v
            if "telemetria" in e:
                x["tel_local"] = e["telemetria"]
            x["editado"] = True
        linhas.append(x)
    for l in lanc:
        d = json.loads(l["dados"])
        if l["id"] in excl:
            continue
        sup = forn.get(d.get("origem", "MANUAL"), {})
        x = {**d, "id": l["id"], "unit_id": l["unit_id"], "group_id": g, "provisorio": True,
             "fuel_supplier_id": sup.get("id"), "sup_code": sup.get("code", d.get("origem", "MANUAL")),
             "sup_name": sup.get("name", "Lançamento manual"), "tel_local": d.get("telemetria"),
             "date_add": l["criado_em"]}
        if l["id"] in edic:
            x.update({k: v for k, v in edic[l["id"]].items() if k != "telemetria"})
            if "telemetria" in edic[l["id"]]:
                x["tel_local"] = edic[l["id"]]["telemetria"]
        linhas.append(x)

    for x in linhas:
        x["event_datetime"] = _dt(x.get("event_datetime"))
        for k in ("km_informado_atual", "liters", "price_per_liter", "total_value", "total_value_informed"):
            x[k] = _f(x.get(k))
        t = tipos.get(x.get("fuel_type_id"))
        x["ft_code"] = t["code"] if t else x.get("ft_code")
        x["ft_name"] = t["name"] if t else x.get("ft_name")
        x["family"] = F.classify_fuel_family(x["ft_code"])
        if x.get("liters") is not None and x.get("price_per_liter") is not None and (x.get("provisorio") or x.get("editado")):
            x["total_value"] = round(x["liters"] * x["price_per_liter"], 2)
        st = postos.get(x.get("fuel_station_id"))
        if st:
            x["station_name"], x["station_cnpj"] = st.get("name"), st.get("cnpj")
        rv = rev.get(x["id"])
        if rv:
            x["reviewed_at"], x["review_reason"], x["reviewed_by_name"] = rv["em"], rv["motivo"], rv["autor_nome"]

    perfis = {}
    for uid, p in banco["perfis"].items():
        perfis[uid] = {"default_fuel_type_id": p["default_fuel_type"], "tank_capacity_l": _f(p["tank_capacity_l"]),
                       "expected_kml_min": _f(p["expected_kml_min"]), "expected_kml_max": _f(p["expected_kml_max"]),
                       "meter": p["meter"], "provisorio": False}
    for uid, p in perf.items():
        if uid in banco["veiculos"]:
            perfis[uid] = {**p, "provisorio": True}
    return {"linhas": linhas, "perfis": perfis, "postos": postos}


def _ciclos(linhas: list[dict]) -> None:
    """Igual à view mova.v_fuel_supply_calc: por veículo e família (fora ARLA),
    em ordem de data, km/L = km do ciclo ÷ litros do ciclo, onde o ciclo vai de
    um tanque cheio (com km) ao próximo."""
    grupos: dict[tuple, list] = defaultdict(list)
    for x in linhas:
        for k in ("km_inicial", "km_rodados", "ciclo_km", "ciclo_litros", "km_por_litro", "custo_por_km"):
            x[k] = None
        if x["family"] != "arla" and x["event_datetime"] is not None:
            grupos[(x["unit_id"], x["family"])].append(x)
    for lista in grupos.values():
        lista.sort(key=lambda x: (x["event_datetime"], x["id"]))
        cheios = 0
        ultimo_km = None
        totais: dict[int, dict] = defaultdict(lambda: {"litros": 0.0, "valor": 0.0, "fim": None})
        for x in lista:
            x["_ciclo"] = cheios
            if x["km_informado_atual"] is not None:
                x["km_inicial"] = ultimo_km
                ultimo_km = x["km_informado_atual"]
                if x["km_inicial"] is not None:
                    x["km_rodados"] = round(x["km_informado_atual"] - x["km_inicial"], 1)
            t = totais[cheios]
            t["litros"] += x["liters"] or 0
            t["valor"] += x["total_value"] or 0
            if x.get("full_tank") and x["km_informado_atual"] is not None:
                t["fim"] = max(t["fim"] or x["km_informado_atual"], x["km_informado_atual"])
                cheios += 1
        for x in lista:
            if not x.get("full_tank"):
                continue
            t, ant = totais[x["_ciclo"]], totais.get(x["_ciclo"] - 1)
            x["ciclo_litros"] = round(t["litros"], 3)
            if ant and ant["fim"] is not None and x["km_informado_atual"] is not None and x["km_informado_atual"] > ant["fim"]:
                km = x["km_informado_atual"] - ant["fim"]
                x["ciclo_km"] = round(km, 1)
                x["km_por_litro"] = round(km / t["litros"], 2) if t["litros"] else None
                x["custo_por_km"] = round(t["valor"] / km, 2) if km else None


def _duplicados(linhas: list[dict]) -> set:
    jan = timedelta(minutes=F.DEFAULT_THRESHOLDS["duplicate_window_min"])
    tol = F.DEFAULT_THRESHOLDS["tank_tolerance_pct"] / 100
    por: dict[tuple, list] = defaultdict(list)
    for x in linhas:
        if x["event_datetime"] is not None and x.get("liters"):
            por[(x["unit_id"], x["family"])].append(x)
    dup = set()
    for lista in por.values():
        lista.sort(key=lambda x: x["event_datetime"])
        for i, a in enumerate(lista):
            for b in lista[i + 1:]:
                if b["event_datetime"] - a["event_datetime"] > jan:
                    break
                if abs(b["liters"] - a["liters"]) / a["liters"] <= tol:
                    dup.update((a["id"], b["id"]))
    return dup


def _classificacao_tel(x: dict) -> dict:
    if x.get("tel_local"):
        return x["tel_local"]
    if x.get("classification") is None:
        return {"classification": "PENDENTE"}
    stale, proc = x.get("telemetry_stale_at"), x.get("processed_at")
    if stale is not None and (proc is None or stale > proc):
        return {"classification": "PENDENTE"}
    return {"classification": x["classification"], "odometer": _f(x.get("odom_telemetry_before")),
            "read_at": str(x["local_time_telemetry_before"]) if x.get("local_time_telemetry_before") else None,
            "km_telemetry": _f(x.get("km_telemetry")), "divergence_km": _f(x.get("divergence_km")),
            "divergence_pct": _f(x.get("divergence_pct"))}


def _alertas(x: dict, perfil: Optional[dict], dup: set) -> list[dict]:
    p = perfil or {}
    tel = _classificacao_tel(x)
    x["_tel"] = tel
    revisado = x.get("reviewed_at") is not None
    al = F.compute_alerts(
        family=x["family"], km_rodados=x["km_rodados"], liters=x["liters"], km_informado_atual=x["km_informado_atual"],
        tank_capacity_l=p.get("tank_capacity_l"), expected_kml_max=p.get("expected_kml_max"),
        km_por_litro=x["km_por_litro"], expected_kml_min=p.get("expected_kml_min"),
        telemetry_classification=tel["classification"], total_value=x.get("total_value"),
        total_value_informed=x.get("total_value_informed"), is_duplicate_candidate=x["id"] in dup, reviewed=revisado)
    return [{"code": a.code, "severity": a.severity, "label": F.ALERT_LABELS.get(a.code, a.code), "reviewed": a.reviewed} for a in al]


async def _dados(g: int) -> dict:
    agora = time.time()
    hit = _CACHE.get(g)
    if hit and agora - hit[0] < CACHE_S:
        return hit[1]
    banco = await _carregar_banco(g)
    ov = _aplicar_overlay(g, banco)
    linhas = ov["linhas"]
    _ciclos(linhas)
    dup = _duplicados(linhas)
    for x in linhas:
        x["alerts"] = _alertas(x, ov["perfis"].get(x["unit_id"]), dup)
    d = {**banco, **ov}
    _CACHE[g] = (agora, d)
    return d


def _veiculo_ref(d: dict, unit_id: int) -> dict:
    v = d["veiculos"].get(unit_id) or {}
    return {"id": unit_id, "plate": v.get("label") or f"#{unit_id}", "fleetNumber": (v.get("label2") or "").strip() or None,
            "groupName": d.get("grupo_nome"), "subgroupId": v.get("subgroup_id"), "subgroupName": v.get("subgroup_name")}


def _saida(x: dict, d: dict) -> dict:
    tel = x.get("_tel") or {}
    return {
        "id": x["id"], "unitId": x["unit_id"], "vehicle": _veiculo_ref(d, x["unit_id"]),
        "eventDatetime": x["event_datetime"].isoformat(timespec="minutes") if x["event_datetime"] else None,
        "fuelTypeId": x.get("fuel_type_id"), "fuelTypeName": x.get("ft_name"), "fuelFamily": x["family"],
        "supplier": {"code": x.get("sup_code"), "name": x.get("sup_name")}, "externalRef": x.get("external_ref"),
        "station": {"id": x["fuel_station_id"], "name": x.get("station_name"), "cnpj": x.get("station_cnpj")} if x.get("fuel_station_id") else None,
        "localInformado": x.get("local_informado"),
        "driver": {"id": x.get("driver_id"), "name": x.get("driver_name")} if x.get("driver_id") else None,
        "kmInformed": x["km_informado_atual"], "liters": x["liters"], "pricePerLiter": x.get("price_per_liter"),
        "totalValue": x.get("total_value"), "totalValueInformed": x.get("total_value_informed"),
        "fullTank": bool(x.get("full_tank")), "invoiceNumber": x.get("invoice_number"), "notes": x.get("notes"),
        "computed": {"kmInitial": x["km_inicial"], "kmDriven": x["km_rodados"], "kmPerLiter": x["km_por_litro"],
                     "costPerKm": x["custo_por_km"], "cycleKm": x["ciclo_km"], "cycleLiters": x["ciclo_litros"]},
        "telemetry": {"classification": tel.get("classification"), "odometer": tel.get("odometer"), "readAt": tel.get("read_at"),
                      "kmTelemetry": tel.get("km_telemetry"), "divergenceKm": tel.get("divergence_km"),
                      "divergencePct": tel.get("divergence_pct")},
        "alerts": x["alerts"],
        "review": {"reason": x.get("review_reason"), "user": x.get("reviewed_by_name") or (f"usuário {x['reviewed_by']}" if x.get("reviewed_by") else None),
                   "date": str(x["reviewed_at"])} if x.get("reviewed_at") else None,
        "editableFields": editaveis(x.get("sup_code")), "provisorio": bool(x.get("provisorio") or x.get("editado")),
    }


def _periodo(inicio: date, fim: date) -> tuple[datetime, datetime]:
    if fim < inicio:
        raise HTTPException(400, "A data final deve ser maior que a inicial.")
    if (fim - inicio).days > 365:
        raise HTTPException(400, "O período máximo é de 365 dias.")
    return datetime.combine(inicio, datetime.min.time()), datetime.combine(fim + timedelta(days=1), datetime.min.time())


def _agregar(linhas: list[dict]) -> dict:
    """Totais de UM veículo no período (aggregate_supply_rows do time): km/L e
    custo/km somam os ciclos completos, sem os ciclos com erro não verificado."""
    litros = arla = gasto = ciclo_km = ciclo_l = 0.0
    abertos = {"error": 0, "warn": 0, "info": 0}
    ultimo = None
    ultimo_tipo = None
    for x in linhas:
        lt = x["liters"] or 0
        if x["family"] == "arla":
            arla += lt
        else:
            litros += lt
            ultimo_tipo = x.get("fuel_type_id")
        gasto += x.get("total_value") or 0
        rev = x.get("reviewed_at") is not None
        for a in x["alerts"]:
            if not a["reviewed"]:
                abertos[a["severity"]] += 1
        if (x["family"] != "arla" and x.get("full_tank") and x["ciclo_km"] is not None
                and not F.cycle_excluded_from_average([a["code"] for a in x["alerts"]], rev)):
            ciclo_km += x["ciclo_km"]
            ciclo_l += x["ciclo_litros"] or 0
        if ultimo is None or x["event_datetime"] > ultimo["event_datetime"]:
            ultimo = x
    return {"supplies": len(linhas), "liters": round(litros, 1), "litersArla": round(arla, 1), "spent": round(gasto, 2),
            "kmDriven": round(ciclo_km, 1) if ciclo_km else None,
            "kmPerLiter": round(ciclo_km / ciclo_l, 2) if ciclo_l else None,
            "costPerKm": round(gasto / ciclo_km, 2) if ciclo_km else None, "openAlerts": abertos,
            "_ciclo_l": ciclo_l, "lastFuelTypeId": ultimo_tipo,
            "lastSupply": {"at": ultimo["event_datetime"].isoformat(timespec="minutes"), "km": ultimo["km_informado_atual"]} if ultimo else None}


def _no_periodo(d: dict, ini: datetime, fim: datetime, subgroup_id=None) -> list[dict]:
    out = []
    for x in d["linhas"]:
        if x["event_datetime"] is None or not (ini <= x["event_datetime"] < fim):
            continue
        if subgroup_id and (d["veiculos"].get(x["unit_id"]) or {}).get("subgroup_id") != subgroup_id:
            continue
        out.append(x)
    return out


def _bate_busca(d: dict, unit_id: int, busca: Optional[str]) -> bool:
    if not busca:
        return True
    v = d["veiculos"].get(unit_id) or {}
    b = busca.replace("-", "").lower()
    return b in (v.get("label") or "").replace("-", "").lower() or b in (v.get("label2") or "").lower() \
        or b in (v.get("subgroup_name") or "").lower()


# ------------------------------------------------------------------ endpoints

@router.get("/catalogo")
async def catalogo(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    return {
        "fuelTypes": [{"id": t["id"], "code": t["code"], "name": t["name"], "family": F.classify_fuel_family(t["code"])} for t in d["tipos"]],
        "suppliers": [{"id": f["id"], "code": f["code"], "name": f["name"], "integrationType": f["integration_type"]} for f in d["fornecedores"]],
        "alertTypes": [{"code": c, "severity": s, "label": l, "description": ds} for c, s, l, ds in F.ALERT_TYPES],
        "pendingErrorCodes": [{"code": c, "label": l} for c, l in F.PENDING_ERROR_CODES],
        "thresholds": F.DEFAULT_THRESHOLDS,
        "stations": [{"id": p["id"], "name": p.get("name"), "fuelTypeId": p.get("fuel_type_id"), "pricePerLiter": _f(p.get("price_per_liter"))}
                     for p in d["postos"].values()],
        "drivers": [],
        "groupName": d.get("grupo_nome"),
    }


@router.get("/painel")
async def painel(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                 subgroup_id: Optional[int] = Query(None), busca: Optional[str] = Query(None),
                 filtro: str = Query("all", pattern="^(all|alerts|range)$"), ordem: str = Query("alerts"),
                 direcao: str = Query("desc", pattern="^(asc|desc)$"), pagina: int = Query(0, ge=0),
                 limite: int = Query(50, ge=1, le=200), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    ini, fi = _periodo(inicio, fim)
    por: dict[int, list] = defaultdict(list)
    for x in _no_periodo(d, ini, fi, subgroup_id):
        if _bate_busca(d, x["unit_id"], busca):
            por[x["unit_id"]].append(x)
    resumos = []
    ciclo_l_total = 0.0
    for uid, ls in por.items():
        a = _agregar(ls)
        p = d["perfis"].get(uid)
        st = F.consumption_status(a["kmPerLiter"], (p or {}).get("expected_kml_min"), (p or {}).get("expected_kml_max"))
        ciclo_l_total += a.pop("_ciclo_l")
        resumos.append({"unitId": uid, "vehicle": _veiculo_ref(d, uid), "plate": _veiculo_ref(d, uid)["plate"],
                        "fleetNumber": _veiculo_ref(d, uid)["fleetNumber"], "subgroupName": _veiculo_ref(d, uid)["subgroupName"],
                        "fuelTypeId": a.pop("lastFuelTypeId"), "tankCapacityL": (p or {}).get("tank_capacity_l"),
                        "expectedKmL": {"min": p["expected_kml_min"], "max": p["expected_kml_max"]} if p and p.get("expected_kml_min") is not None else None,
                        "consumptionStatus": st, **a})
    km_total = sum(r["kmDriven"] or 0 for r in resumos)
    gasto = sum(r["spent"] for r in resumos)
    totais = {
        "spent": round(gasto, 2), "liters": round(sum(r["liters"] for r in resumos), 1),
        "litersArla": round(sum(r["litersArla"] for r in resumos), 1), "supplies": sum(r["supplies"] for r in resumos),
        "kmDriven": round(km_total, 1), "vehicles": len(resumos),
        "kmPerLiter": round(km_total / ciclo_l_total, 2) if ciclo_l_total else None,
        "costPerKm": round(gasto / km_total, 2) if km_total else None,
        "openAlerts": {k: sum(r["openAlerts"][k] for r in resumos) for k in ("error", "warn", "info")},
        "vehiclesOutOfRange": sum(1 for r in resumos if r["consumptionStatus"] in ("below", "above")),
    }
    com_alerta = lambda r: r["openAlerts"]["error"] + r["openAlerts"]["warn"] + r["openAlerts"]["info"] > 0
    facetas = {"all": len(resumos), "withAlerts": sum(1 for r in resumos if com_alerta(r)), "outOfRange": totais["vehiclesOutOfRange"]}
    lista = [r for r in resumos if filtro == "all" or (filtro == "alerts" and com_alerta(r))
             or (filtro == "range" and r["consumptionStatus"] in ("below", "above"))]
    chave = {
        "plate": lambda r: r["plate"], "supplies": lambda r: r["supplies"], "liters": lambda r: r["liters"],
        "spent": lambda r: r["spent"], "kmDriven": lambda r: r["kmDriven"] or 0, "kmPerLiter": lambda r: r["kmPerLiter"] or 0,
        "costPerKm": lambda r: r["costPerKm"] or 0, "lastSupply": lambda r: r["lastSupply"]["at"] if r["lastSupply"] else "",
        "alerts": lambda r: (r["openAlerts"]["error"], r["openAlerts"]["warn"] + r["openAlerts"]["info"]),
    }.get(ordem, lambda r: r["openAlerts"]["error"])
    lista.sort(key=chave, reverse=direcao == "desc")
    return {"totals": totais, "facets": facetas, "total": len(lista),
            "data": lista[pagina * limite: pagina * limite + limite]}


@router.get("/sem-abastecimento")
async def sem_abastecimento(group_id: int = Query(...), busca: Optional[str] = Query(None),
                            user=Depends(require_permission("reports", "read"))):
    """Veículos ativos que nunca tiveram abastecimento registrado."""
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    com = {x["unit_id"] for x in d["linhas"]}
    out = [{"unitId": uid, **_veiculo_ref(d, uid), "dateAdd": str(v.get("date_add") or "")[:10]}
           for uid, v in d["veiculos"].items() if uid not in com and _bate_busca(d, uid, busca)]
    out.sort(key=lambda r: r["plate"])
    return {"data": out, "total": len(out)}


@router.get("/veiculo/{unit_id}")
async def veiculo(unit_id: int, group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                  user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    if unit_id not in d["veiculos"]:
        raise HTTPException(404, "Veículo não encontrado neste cliente.")
    ini, fi = _periodo(inicio, fim)
    ls = sorted([x for x in d["linhas"] if x["unit_id"] == unit_id and x["event_datetime"] and ini <= x["event_datetime"] < fi],
                key=lambda x: (x["event_datetime"], x["id"]))
    a = _agregar(ls)
    a.pop("_ciclo_l")
    p = d["perfis"].get(unit_id)
    a["consumptionStatus"] = F.consumption_status(a["kmPerLiter"], (p or {}).get("expected_kml_min"), (p or {}).get("expected_kml_max"))
    ref = _veiculo_ref(d, unit_id)
    return {
        "vehicle": {**ref, "fuelTypeId": (p or {}).get("default_fuel_type_id") or a.get("lastFuelTypeId"),
                    "profile": {"tankCapacityL": p.get("tank_capacity_l"), "expectedMin": p.get("expected_kml_min"),
                                "expectedMax": p.get("expected_kml_max"), "defaultFuelTypeId": p.get("default_fuel_type_id"),
                                "meter": p.get("meter"), "provisorio": p.get("provisorio")} if p else None},
        "totals": a, "supplies": [_saida(x, d) for x in ls[-2000:]], "truncated": len(ls) > 2000,
    }


@router.get("/alertas")
async def alertas(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                  subgroup_id: Optional[int] = Query(None), busca: Optional[str] = Query(None),
                  tipos: Optional[str] = Query(None), gravidade: Optional[str] = Query(None),
                  revisado: str = Query("open", pattern="^(open|only|all)$"),
                  pagina: int = Query(0, ge=0), limite: int = Query(50, ge=1, le=200),
                  user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    ini, fi = _periodo(inicio, fim)
    pedidos = set(tipos.split(",")) if tipos else None
    por_tipo = {c: 0 for c, *_ in F.ALERT_TYPES}
    achados = []
    for x in _no_periodo(d, ini, fi, subgroup_id):
        al = x["alerts"]
        if not al or not _bate_busca(d, x["unit_id"], busca):
            continue
        ok_grav = gravidade is None or any(a["severity"] == gravidade for a in al)
        ok_rev = (revisado == "all" or (revisado == "open" and any(not a["reviewed"] for a in al))
                  or (revisado == "only" and all(a["reviewed"] for a in al)))
        if ok_grav and ok_rev:
            for a in al:
                por_tipo[a["code"]] = por_tipo.get(a["code"], 0) + 1
        if pedidos and not any(a["code"] in pedidos for a in al):
            continue
        if not (ok_grav and ok_rev):
            continue
        achados.append(x)
    achados.sort(key=lambda x: (not any(a["severity"] == "error" for a in x["alerts"]), -x["event_datetime"].timestamp()))
    return {"facets": {"byType": por_tipo}, "total": len(achados),
            "data": [_saida(x, d) for x in achados[pagina * limite: pagina * limite + limite]]}


# ------------------------------------------------------------- lançamentos

class Abastecimento(BaseModel):
    group_id: int
    unit_id: int
    event_datetime: str
    fuel_type_id: int
    liters: float = Field(gt=0)
    price_per_liter: float = Field(ge=0)
    km_informado_atual: Optional[float] = None
    full_tank: bool = True
    fuel_station_id: Optional[int] = None
    local_informado: Optional[str] = None
    driver_id: Optional[int] = None
    total_value_informed: Optional[float] = None
    invoice_number: Optional[str] = None
    notes: Optional[str] = None


def _validar(d: dict, b: dict, excluir_id: Optional[int] = None) -> tuple[dict, list[dict]]:
    """Prévia e revalidação (validate_supply_draft do time): mesmas regras no
    rascunho e ao salvar."""
    issues = []
    tipo = next((t for t in d["tipos"] if t["id"] == b["fuel_type_id"]), None)
    if not tipo:
        return {}, [{"field": "fuel_type_id", "code": "invalid", "severity": "error", "message": "Combustível inválido."}]
    fam = F.classify_fuel_family(tipo["code"])
    p = d["perfis"].get(b["unit_id"]) or {}
    quando = _dt(b["event_datetime"])
    if quando is None:
        issues.append({"field": "event_datetime", "code": "invalid", "severity": "error", "message": "Data e hora inválidas."})
        return {}, issues
    if quando > datetime.now() + timedelta(minutes=10):
        issues.append({"field": "event_datetime", "code": "future", "severity": "error", "message": "A data está no futuro."})
    if p.get("default_fuel_type_id") and p["default_fuel_type_id"] != b["fuel_type_id"]:
        padrao = next((t for t in d["tipos"] if t["id"] == p["default_fuel_type_id"]), None)
        if padrao and F.classify_fuel_family(padrao["code"]) != fam:
            issues.append({"field": "fuel_type_id", "code": "fuel_mismatch", "severity": "warn",
                           "message": "Combustível diferente do padrão cadastrado para este veículo."})
    tanque = p.get("tank_capacity_l")
    if fam != "arla" and tanque and b["liters"] > tanque * (1 + F.DEFAULT_THRESHOLDS["tank_tolerance_pct"] / 100):
        issues.append({"field": "liters", "code": "ACIMA_DO_TANQUE", "severity": "error",
                       "message": f"Litros acima da capacidade do tanque ({tanque:g} L)."})
    km = b.get("km_informado_atual")
    if km is None and fam != "arla":
        issues.append({"field": "km_informado_atual", "code": "SEM_KM", "severity": "warn",
                       "message": "Sem quilometragem informada: fica fora do cálculo de consumo."})
    if not b.get("fuel_station_id") and not (b.get("local_informado") or "").strip():
        issues.append({"field": "fuel_station_id", "code": "station_or_local_required", "severity": "error",
                       "message": "Informe o posto cadastrado ou o local do abastecimento."})
    total = round(b["liters"] * b["price_per_liter"], 2)
    km_ini = km_rod = kml = cpk = None
    if fam != "arla":
        anteriores = sorted([x for x in d["linhas"] if x["unit_id"] == b["unit_id"] and x["id"] != excluir_id
                             and x["km_informado_atual"] is not None and x["event_datetime"] and x["event_datetime"] < quando],
                            key=lambda x: x["event_datetime"])
        if anteriores:
            km_ini = anteriores[-1]["km_informado_atual"]
        if km_ini is not None and km is not None:
            km_rod = km - km_ini
            if km_rod < 0:
                issues.append({"field": "km_informado_atual", "code": "KM_REGRESSIVO", "severity": "error",
                               "message": f"Menor que o último abastecimento ({km_ini:,.0f} km).".replace(",", ".")})
            elif tanque and p.get("expected_kml_max") and km_rod > tanque * p["expected_kml_max"] * 1.5:
                issues.append({"field": "km_informado_atual", "code": "KM_SALTO", "severity": "warn",
                               "message": "Km rodados maior do que um tanque cheio permitiria. Confira os dígitos."})
            if b.get("full_tank") and km_rod and km_rod > 0:
                kml = round(km_rod / b["liters"], 2)
                cpk = round(total / km_rod, 2)
                if F.consumption_status(kml, p.get("expected_kml_min"), p.get("expected_kml_max")) in ("below", "above"):
                    issues.append({"field": "liters", "code": "CONSUMO_FORA_DA_FAIXA", "severity": "warn",
                                   "message": f"Consumo de {kml:.2f} km/L, fora da faixa esperada.".replace(".", ",")})
        jan = timedelta(minutes=F.DEFAULT_THRESHOLDS["duplicate_window_min"])
        for x in d["linhas"]:
            if (x["id"] != excluir_id and x["unit_id"] == b["unit_id"] and x["family"] == fam and x["event_datetime"]
                    and abs(x["event_datetime"] - quando) <= jan and x.get("liters")
                    and abs(x["liters"] - b["liters"]) / b["liters"] <= F.DEFAULT_THRESHOLDS["tank_tolerance_pct"] / 100):
                issues.append({"field": None, "code": "DUPLICADO", "severity": "error",
                               "message": "Outro abastecimento próximo (até 30 min) com litros parecidos já existe."})
                break
    if b.get("total_value_informed") is not None and abs(b["total_value_informed"] - total) > F.DEFAULT_THRESHOLDS["invoice_tolerance_brl"]:
        issues.append({"field": "total_value_informed", "code": "VALOR_DIVERGENTE", "severity": "warn",
                       "message": "O valor da nota diverge do cálculo (litros × preço por litro)."})
    return {"totalValue": total, "kmInitial": km_ini, "kmDriven": km_rod, "kmPerLiter": kml, "costPerKm": cpk}, issues


@router.post("/abastecimentos/validar")
async def validar(a: Abastecimento, excluir_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, a.group_id)
    d = await _dados(a.group_id)
    calc, issues = _validar(d, a.model_dump(), excluir_id)
    return {"computed": calc, "issues": issues}


@router.get("/veiculo/{unit_id}/km-sugerido")
async def km_sugerido(unit_id: int, group_id: int = Query(...), quando: str = Query(...),
                      user=Depends(require_permission("reports", "read"))):
    """Último km informado antes da data e a leitura do rastreador naquele momento."""
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    q = _dt(quando) or datetime.now()
    ant = sorted([x for x in d["linhas"] if x["unit_id"] == unit_id and x["km_informado_atual"] is not None
                  and x["event_datetime"] and x["event_datetime"] < q], key=lambda x: x["event_datetime"])
    tel = await _telemetria(unit_id, q, None)
    return {"lastSupply": {"at": ant[-1]["event_datetime"].isoformat(timespec="minutes"), "km": ant[-1]["km_informado_atual"]} if ant else None,
            "trackerKm": tel.get("odometer"), "trackerReadAt": tel.get("read_at")}


@router.post("/abastecimentos")
async def criar(a: Abastecimento, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, a.group_id)
    d = await _dados(a.group_id)
    if a.unit_id not in d["veiculos"]:
        raise HTTPException(404, "Veículo não encontrado neste cliente.")
    calc, issues = _validar(d, a.model_dump())
    erros = [i for i in issues if i["severity"] == "error"]
    if erros:
        raise HTTPException(422, {"message": erros[0]["message"], "issues": issues})
    dados = {k: v for k, v in a.model_dump().items() if k not in ("group_id", "unit_id")}
    dados["origem"] = "MANUAL"
    dados["telemetria"] = await _telemetria(a.unit_id, _dt(a.event_datetime), a.km_informado_atual)
    with _trava, _con() as c:
        cur = c.execute("INSERT INTO lancamento (group_id, unit_id, dados, autor, criado_em) VALUES (?,?,?,?,?)",
                        (a.group_id, a.unit_id, json.dumps(dados), _uid(user), _agora()))
        _historico(c, cur.lastrowid, "create", dados, None, user)
    _invalidar(a.group_id)
    return {"id": cur.lastrowid, "issues": issues}


class Correcao(BaseModel):
    group_id: int
    campos: dict
    motivo: Optional[str] = None


@router.put("/abastecimentos/{supply_id}")
async def corrigir(supply_id: int, c_: Correcao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, c_.group_id)
    d = await _dados(c_.group_id)
    x = next((l for l in d["linhas"] if l["id"] == supply_id), None)
    if not x:
        raise HTTPException(404, "Abastecimento não encontrado.")
    livres = set(editaveis(x.get("sup_code")))
    proibidos = [k for k in c_.campos if k not in livres]
    if proibidos:
        raise HTTPException(400, f"Abastecimento de integração: só dá para corrigir {', '.join(sorted(livres))}.")
    novo = {**{k: x.get(k) for k in EDITAVEIS_TODOS}, **c_.campos}
    novo["event_datetime"] = str(novo["event_datetime"]) if novo.get("event_datetime") else None
    _, issues = _validar(d, {**novo, "unit_id": x["unit_id"], "liters": novo.get("liters") or x["liters"] or 1,
                             "price_per_liter": novo.get("price_per_liter") or 0}, supply_id)
    erros = [i for i in issues if i["severity"] == "error" and i["code"] != "DUPLICADO"]
    if erros:
        raise HTTPException(422, {"message": erros[0]["message"], "issues": issues})
    tel = None
    if AFETAM_TELEMETRIA & set(c_.campos):
        tel = await _telemetria(x["unit_id"], _dt(novo["event_datetime"]), _f(novo.get("km_informado_atual")))
    with _trava, _con() as c:
        ant = c.execute("SELECT dados FROM edicao WHERE supply_id = ?", (supply_id,)).fetchone()
        atual = json.loads(ant["dados"]) if ant else {}
        atual.update(c_.campos)
        if tel:
            atual["telemetria"] = tel
        c.execute("INSERT OR REPLACE INTO edicao (supply_id, dados, autor, em) VALUES (?,?,?,?)",
                  (supply_id, json.dumps(atual, default=str), _uid(user), _agora()))
        _historico(c, supply_id, "update", c_.campos, c_.motivo, user)
    _invalidar(c_.group_id)
    return {"ok": True, "issues": issues}


class Motivo(BaseModel):
    group_id: int
    motivo: str = Field(min_length=3, max_length=500)
    autor_nome: Optional[str] = None


@router.post("/abastecimentos/{supply_id}/excluir")
async def excluir(supply_id: int, m: Motivo, user=Depends(require_permission("reports", "read"))):
    """Sai dos cálculos, mas continua no histórico com o motivo."""
    _grupo_ok(user, m.group_id)
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO exclusao (supply_id, motivo, autor, em) VALUES (?,?,?,?)",
                  (supply_id, m.motivo, _uid(user), _agora()))
        _historico(c, supply_id, "delete", {}, m.motivo, user)
    _invalidar(m.group_id)
    return {"ok": True}


@router.post("/abastecimentos/{supply_id}/verificar")
async def verificar(supply_id: int, m: Motivo, user=Depends(require_permission("reports", "read"))):
    """Marca os alertas do abastecimento como verificados (a classificação não muda)."""
    _grupo_ok(user, m.group_id)
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO revisao (supply_id, motivo, autor, autor_nome, em) VALUES (?,?,?,?,?)",
                  (supply_id, m.motivo, _uid(user), m.autor_nome, _agora()))
        _historico(c, supply_id, "review", {}, m.motivo, user)
    _invalidar(m.group_id)
    return {"ok": True}


@router.delete("/abastecimentos/{supply_id}/verificar")
async def desfazer_verificacao(supply_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _trava, _con() as c:
        c.execute("DELETE FROM revisao WHERE supply_id = ?", (supply_id,))
        _historico(c, supply_id, "unreview", {}, None, user)
    _invalidar(group_id)
    return {"ok": True}


@router.get("/abastecimentos/{supply_id}/historico")
async def historico(supply_id: int, user=Depends(require_permission("reports", "read"))):
    with _trava, _con() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM historico WHERE supply_id = ? ORDER BY id DESC", (supply_id,))]
    for r in rows:
        r["dados"] = json.loads(r["dados"] or "{}")
    return {"data": rows}


# ------------------------------------------------------------------- perfil

class Perfil(BaseModel):
    group_id: int
    default_fuel_type_id: Optional[int] = None
    tank_capacity_l: float = Field(gt=0, le=5000)
    expected_kml_min: float = Field(gt=0, le=50)
    expected_kml_max: float = Field(gt=0, le=50)
    meter: str = "km"


@router.put("/veiculo/{unit_id}/perfil")
async def salvar_perfil(unit_id: int, p: Perfil, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    if p.expected_kml_max < p.expected_kml_min:
        raise HTTPException(422, "O km/L máximo deve ser maior ou igual ao mínimo.")
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO perfil (unit_id, dados, autor, em) VALUES (?,?,?,?)",
                  (unit_id, json.dumps(p.model_dump(exclude={"group_id"})), _uid(user), _agora()))
    _invalidar(p.group_id)
    return {"ok": True}


# ------------------------------------------------------------------- postos

@router.get("/postos")
async def postos(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                 user=Depends(require_permission("reports", "read"))):
    """Postos cadastrados com o movimento do período, e os locais informados
    pelos cartões que ainda não são posto cadastrado."""
    _grupo_ok(user, group_id)
    d = await _dados(group_id)
    ini, fi = _periodo(inicio, fim)
    mov: dict = defaultdict(lambda: {"supplies": 0, "liters": 0.0, "spent": 0.0, "vehicles": set(), "last": None})
    soltos: dict = defaultdict(lambda: {"supplies": 0, "liters": 0.0, "spent": 0.0, "vehicles": set(), "last": None})
    for x in _no_periodo(d, ini, fi):
        alvo = mov[x["fuel_station_id"]] if x.get("fuel_station_id") in d["postos"] else soltos[(x.get("local_informado") or "Não informado").strip()]
        alvo["supplies"] += 1
        alvo["liters"] += x["liters"] or 0
        alvo["spent"] += x.get("total_value") or 0
        alvo["vehicles"].add(x["unit_id"])
        alvo["last"] = max(alvo["last"] or x["event_datetime"], x["event_datetime"])

    def _fmt(m):
        return {"supplies": m["supplies"], "liters": round(m["liters"], 1), "spent": round(m["spent"], 2),
                "avgPrice": round(m["spent"] / m["liters"], 3) if m["liters"] else None, "vehicles": len(m["vehicles"]),
                "lastSupply": m["last"].isoformat(timespec="minutes") if m["last"] else None}

    cadastrados = [{"id": p["id"], "name": p.get("name"), "company": p.get("company"), "branch": p.get("branch"),
                    "cnpj": p.get("cnpj"), "pumpCode": p.get("pump_cod"), "fuelTypeId": p.get("fuel_type_id"),
                    "pricePerLiter": _f(p.get("price_per_liter")), "latitude": _f(p.get("latitude")),
                    "longitude": _f(p.get("longitude")), "radiusM": p.get("radius_m"), "provisorio": bool(p.get("provisorio")),
                    **_fmt(mov[p["id"]])} for p in d["postos"].values()]
    cadastrados.sort(key=lambda r: -r["spent"])
    nao = [{"local": k, **_fmt(v)} for k, v in soltos.items()]
    nao.sort(key=lambda r: -r["spent"])
    return {"stations": cadastrados, "unregistered": nao}


class Posto(BaseModel):
    group_id: int
    name: str = Field(min_length=2, max_length=120)
    company: Optional[str] = None
    branch: Optional[str] = None
    cnpj: Optional[str] = None
    pump_cod: Optional[str] = None
    fuel_type_id: Optional[int] = None
    price_per_liter: Optional[float] = Field(None, ge=0)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    radius_m: int = Field(250, ge=10, le=5000)


@router.post("/postos")
async def criar_posto(p: Posto, posto_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    dados = p.model_dump(exclude={"group_id"})
    if p.cnpj:
        digitos = "".join(ch for ch in p.cnpj if ch.isdigit())
        if len(digitos) != 14:
            raise HTTPException(422, "CNPJ deve ter 14 dígitos.")
    with _trava, _con() as c:
        if posto_id:
            ex = c.execute("SELECT id FROM posto WHERE id = ? OR origem_id = ?", (posto_id, posto_id)).fetchone()
            if ex:
                c.execute("UPDATE posto SET dados = ?, autor = ?, em = ?, ativo = 1 WHERE id = ?", (json.dumps(dados), _uid(user), _agora(), ex["id"]))
            else:
                c.execute("INSERT INTO posto (group_id, origem_id, dados, autor, em) VALUES (?,?,?,?,?)",
                          (p.group_id, posto_id, json.dumps(dados), _uid(user), _agora()))
            novo_id = posto_id
        else:
            novo_id = c.execute("INSERT INTO posto (group_id, dados, autor, em) VALUES (?,?,?,?)",
                                (p.group_id, json.dumps(dados), _uid(user), _agora())).lastrowid
    _invalidar(p.group_id)
    return {"id": novo_id}


@router.delete("/postos/{posto_id}")
async def remover_posto(posto_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _trava, _con() as c:
        ex = c.execute("SELECT id FROM posto WHERE id = ? OR origem_id = ?", (posto_id, posto_id)).fetchone()
        if ex:
            c.execute("UPDATE posto SET ativo = 0, autor = ?, em = ? WHERE id = ?", (_uid(user), _agora(), ex["id"]))
        else:
            c.execute("INSERT INTO posto (group_id, origem_id, dados, ativo, autor, em) VALUES (?,?,?,0,?,?)",
                      (group_id, posto_id, "{}", _uid(user), _agora()))
    _invalidar(group_id)
    return {"ok": True}


# ---------------------------------------------------------------- pendências

_CHAVES = {
    "plate": ["placa", "PLACA", "plate"],
    "date": ["data_hora", "DATA", "data", "event_datetime", "emissao"],
    "product": ["PRODUTO", "produto", "product"],
    "liters": ["LITROS", "litros", "quantidade", "liters"],
    "price": ["VL_UNIT", "vl_unit", "preco_litro", "preco_unit", "price_per_liter"],
    "km": ["HODOMETRO", "hodometro", "km", "km_fim"],
    "station": ["POSTO", "posto", "station", "unidade"],
    "driver": ["motorista", "MOTORISTA"],
    "value": ["valor", "VALOR", "valor_total"],
}


def _primeiro(p: dict, campo: str):
    for k in _CHAVES[campo]:
        if k in p and p[k] not in (None, ""):
            return p[k]
    return None


def _num_br(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _data_payload(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    if s.isdigit() and len(s) == 14:  # RDP: AAAAMMDDhhmmss
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return s


def _resumo(raw) -> dict:
    p = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return {"plate": _primeiro(p, "plate"), "eventDatetime": _data_payload(_primeiro(p, "date")),
            "product": _primeiro(p, "product"), "liters": _num_br(_primeiro(p, "liters")),
            "pricePerLiter": _num_br(_primeiro(p, "price")), "km": _num_br(_primeiro(p, "km")),
            "station": _primeiro(p, "station"), "driver": _primeiro(p, "driver"), "value": _num_br(_primeiro(p, "value"))}


async def _pendencias_banco(g: int):
    return await _ler(
        """SELECT st.id, st.fuel_supplier_id, sp.code AS sup_code, sp.name AS sup_name, st.external_ref, st.raw_payload,
                  st.processing_status, st.error_code, st.error_message, st.fuel_supply_id, st.group_id, st.date_add,
                  st.resolved_at, st.resolution_note
           FROM mova.fuel_supply_staging st JOIN mova.fuel_supplier sp ON sp.id = st.fuel_supplier_id
           WHERE st.group_id = :g
              OR (st.group_id IS NULL AND st.fuel_supplier_id IN (SELECT DISTINCT fuel_supplier_id FROM mova.fuel_supply WHERE group_id = :g))
           ORDER BY st.date_add DESC""", {"g": g})


@router.get("/pendencias")
async def pendencias(group_id: int = Query(...), situacao: str = Query("open", pattern="^(open|RESOLVED|DISCARDED)$"),
                     codigo: Optional[str] = Query(None), busca: Optional[str] = Query(None),
                     user=Depends(require_permission("reports", "read"))):
    """Transações dos cartões que não viraram abastecimento (não dependem de período)."""
    _grupo_ok(user, group_id)
    rows = await _pendencias_banco(group_id)
    with _trava, _con() as c:
        loc = {r["staging_id"]: dict(r) for r in c.execute("SELECT * FROM pendencia")}
    por_sit = {"open": 0, "RESOLVED": 0, "DISCARDED": 0}
    por_cod: dict[str, int] = {}
    out = []
    for r in rows:
        st = loc[r["id"]]["status"] if r["id"] in loc else r["processing_status"]
        aberto = st in ("PENDING", "ERROR")
        if aberto:
            por_sit["open"] += 1
            if r["error_code"]:
                por_cod[r["error_code"]] = por_cod.get(r["error_code"], 0) + 1
        elif st in por_sit:
            por_sit[st] += 1
        if not ((situacao == "open" and aberto) or situacao == st):
            continue
        if codigo and r["error_code"] != codigo:
            continue
        resumo = _resumo(r["raw_payload"])
        if busca and busca.lower() not in json.dumps(r["raw_payload"], default=str).lower() and busca.lower() not in (r["external_ref"] or "").lower():
            continue
        l = loc.get(r["id"])
        out.append({"id": r["id"], "supplier": {"code": r["sup_code"], "name": r["sup_name"]}, "externalRef": r["external_ref"],
                    "status": st, "errorCode": r["error_code"], "errorMessage": r["error_message"], "summary": resumo,
                    "dateAdd": str(r["date_add"])[:16] if r["date_add"] else None,
                    "resolution": {"note": l["nota"], "at": l["em"], "supplyId": l["supply_id"]} if l else
                                  ({"note": r["resolution_note"], "at": str(r["resolved_at"])[:16]} if r["resolved_at"] else None),
                    "provisorio": bool(l)})
    return {"facets": {"byStatus": por_sit, "byErrorCode": por_cod}, "data": out, "total": len(out)}


@router.get("/pendencias/{pend_id}")
async def pendencia(pend_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    rows = [r for r in await _pendencias_banco(group_id) if r["id"] == pend_id]
    if not rows:
        raise HTTPException(404, "Pendência não encontrada.")
    r = rows[0]
    resumo = _resumo(r["raw_payload"])
    d = await _dados(group_id)
    sugestoes = []
    if resumo["plate"]:
        alvo = F.normalize_plate(str(resumo["plate"]))
        for uid, v in d["veiculos"].items():
            dist = F.levenshtein_distance(alvo, F.normalize_plate(v.get("label") or ""))
            if dist <= 2:
                sugestoes.append({"unitId": uid, "plate": v.get("label"), "subgroupName": v.get("subgroup_name"), "distance": dist,
                                  "reason": "Placa idêntica." if dist == 0 else "Mesma placa, com pequenas diferenças (troca de letra/número ou pontuação)."})
        sugestoes.sort(key=lambda s: s["distance"])
    tipo_sugerido = _tipo_por_texto(d, resumo["product"] or "")
    return {"id": r["id"], "supplier": {"code": r["sup_code"], "name": r["sup_name"]}, "externalRef": r["external_ref"],
            "errorCode": r["error_code"], "errorMessage": r["error_message"], "summary": resumo,
            "rawPayload": r["raw_payload"], "suggestions": sugestoes[:10], "suggestedFuelTypeId": tipo_sugerido,
            "needs": {"fuelType": r["error_code"] == "PRODUTO_DESCONHECIDO" or tipo_sugerido is None, "liters": resumo["liters"] is None}}


class Resolver(BaseModel):
    group_id: int
    unit_id: int
    fuel_type_id: int
    liters: Optional[float] = Field(None, gt=0)
    note: Optional[str] = None


@router.post("/pendencias/{pend_id}/resolver")
async def resolver(pend_id: int, r_: Resolver, user=Depends(require_permission("reports", "read"))):
    """Vira abastecimento do veículo escolhido (no armazenamento provisório)."""
    _grupo_ok(user, r_.group_id)
    rows = [r for r in await _pendencias_banco(r_.group_id) if r["id"] == pend_id]
    if not rows:
        raise HTTPException(404, "Pendência não encontrada.")
    r = rows[0]
    with _trava, _con() as c:
        if c.execute("SELECT 1 FROM pendencia WHERE staging_id = ?", (pend_id,)).fetchone() or r["processing_status"] in ("RESOLVED", "DISCARDED"):
            raise HTTPException(409, "Pendência já foi resolvida ou descartada.")
    d = await _dados(r_.group_id)
    if r_.unit_id not in d["veiculos"]:
        raise HTTPException(404, "Veículo não encontrado neste cliente.")
    s = _resumo(r["raw_payload"])
    litros = r_.liters or s["liters"]
    if not litros:
        raise HTTPException(400, "Informe os litros.")
    quando = _dt(s["eventDatetime"]) or datetime.now()
    dados = {"event_datetime": quando.isoformat(sep=" ", timespec="minutes"), "fuel_type_id": r_.fuel_type_id, "liters": litros,
             "price_per_liter": s["pricePerLiter"] or (round(s["value"] / litros, 3) if s["value"] else 0),
             "km_informado_atual": s["km"], "full_tank": True, "local_informado": s["station"],
             "external_ref": r["external_ref"], "notes": r_.note, "origem": r["sup_code"],
             "telemetria": await _telemetria(r_.unit_id, quando, s["km"])}
    with _trava, _con() as c:
        sid = c.execute("INSERT INTO lancamento (group_id, unit_id, dados, autor, criado_em) VALUES (?,?,?,?,?)",
                        (r_.group_id, r_.unit_id, json.dumps(dados), _uid(user), _agora())).lastrowid
        c.execute("INSERT INTO pendencia (staging_id, status, unit_id, supply_id, nota, autor, em) VALUES (?,?,?,?,?,?,?)",
                  (pend_id, "RESOLVED", r_.unit_id, sid, r_.note, _uid(user), _agora()))
        _historico(c, sid, "resolve", {"pendencia": pend_id}, r_.note, user)
    _invalidar(r_.group_id)
    return {"supplyId": sid}


@router.post("/pendencias/{pend_id}/descartar")
async def descartar(pend_id: int, m: Motivo, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, m.group_id)
    with _trava, _con() as c:
        if c.execute("SELECT 1 FROM pendencia WHERE staging_id = ?", (pend_id,)).fetchone():
            raise HTTPException(409, "Pendência já foi resolvida ou descartada.")
        c.execute("INSERT INTO pendencia (staging_id, status, nota, autor, em) VALUES (?,?,?,?,?)",
                  (pend_id, "DISCARDED", m.motivo, _uid(user), _agora()))
    return {"ok": True}


# ---------------------------------------------------------------- planilha

class LinhaPlanilha(BaseModel):
    placa: str
    data: str
    combustivel: str
    litros: float
    preco_litro: float
    km: Optional[float] = None
    posto: Optional[str] = None
    tanque_cheio: bool = True
    nota_fiscal: Optional[str] = None
    valor_nota: Optional[float] = None
    motorista: Optional[str] = None


class Planilha(BaseModel):
    group_id: int
    linhas: list[LinhaPlanilha] = Field(max_length=50000)
    confirmar: bool = False


def _tipo_por_texto(d: dict, t: str) -> Optional[int]:
    s = (t or "").upper().replace(" ", "")
    for tp in d["tipos"]:
        if s in (tp["code"].replace("_", ""), tp["name"].upper().replace(" ", "")):
            return tp["id"]
    regras = [("S10", "DIESEL_S10"), ("S500", "DIESEL_S500"), ("COMUM", "DIESEL_S500"), ("ARLA", "ARLA32"),
              ("GASOL", "GASOLINA"), ("ETANOL", "ETANOL"), ("ALCOOL", "ETANOL"), ("ÁLCOOL", "ETANOL")]
    for chave, cod in regras:
        if chave in s:
            return next((tp["id"] for tp in d["tipos"] if tp["code"] == cod), None)
    return None


@router.post("/planilha")
async def importar_planilha(p: Planilha, user=Depends(require_permission("reports", "read"))):
    """Confere cada linha (placa → veículo, combustível, data e as mesmas regras do
    lançamento manual). Com `confirmar`, grava as linhas sem erro."""
    _grupo_ok(user, p.group_id)
    d = await _dados(p.group_id)
    por_placa = {F.normalize_plate(v.get("label") or ""): uid for uid, v in d["veiculos"].items()}
    resultado = []
    gravadas = 0
    for i, l in enumerate(p.linhas, start=1):
        issues = []
        uid = por_placa.get(F.normalize_plate(l.placa))
        if not uid:
            issues.append({"code": "PLACA_SEM_VEICULO", "severity": "error", "message": f"Placa {l.placa} não encontrada neste cliente."})
        tipo = _tipo_por_texto(d, l.combustivel)
        if not tipo:
            issues.append({"code": "PRODUTO_DESCONHECIDO", "severity": "error", "message": f"Combustível '{l.combustivel}' não reconhecido."})
        quando = _dt(_data_payload(l.data))
        if not quando:
            issues.append({"code": "DATA_INVALIDA", "severity": "error", "message": f"Data '{l.data}' inválida."})
        corpo = None
        if not issues:
            corpo = {"unit_id": uid, "event_datetime": quando.isoformat(sep=" ", timespec="minutes"), "fuel_type_id": tipo,
                     "liters": l.litros, "price_per_liter": l.preco_litro, "km_informado_atual": l.km, "full_tank": l.tanque_cheio,
                     "fuel_station_id": None, "local_informado": l.posto or "Planilha", "total_value_informed": l.valor_nota,
                     "invoice_number": l.nota_fiscal, "notes": f"Motorista: {l.motorista}" if l.motorista else None}
            _, iss = _validar(d, corpo)
            issues += iss
        ok = not any(x["severity"] == "error" for x in issues)
        if ok and p.confirmar and corpo:
            dados = {k: v for k, v in corpo.items() if k != "unit_id"}
            dados["origem"] = "PLANILHA"
            dados["telemetria"] = await _telemetria(uid, quando, l.km)
            with _trava, _con() as c:
                sid = c.execute("INSERT INTO lancamento (group_id, unit_id, dados, autor, criado_em) VALUES (?,?,?,?,?)",
                                (p.group_id, uid, json.dumps(dados), _uid(user), _agora())).lastrowid
                _historico(c, sid, "import", {"linha": i}, None, user)
            gravadas += 1
            d["linhas"].append({**corpo, "id": sid, "family": F.classify_fuel_family(next(t["code"] for t in d["tipos"] if t["id"] == tipo)),
                                "event_datetime": quando, "km_informado_atual": l.km})
        resultado.append({"linha": i, "placa": l.placa, "ok": ok, "issues": issues})
    if p.confirmar:
        _invalidar(p.group_id)
    return {"linhas": resultado, "validas": sum(1 for r in resultado if r["ok"]), "comErro": sum(1 for r in resultado if not r["ok"]),
            "gravadas": gravadas}
