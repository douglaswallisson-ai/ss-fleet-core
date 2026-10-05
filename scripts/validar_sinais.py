"""
Validador independente dos sinais do motor (Manutenção + Sinais do motor).

Uso: .venv\Scripts\python.exe scripts\validar_sinais.py [URL da API]
Rodar ANTES de afirmar ao PM ou ao cliente que um sinal está certo. Saída: tabela por
cliente, DIVERGÊNCIAS (API × banco) e PARA REVISAR (alertas com quebra no histórico).

NÃO reaproveita o código da plataforma: lê o banco com SQL escrito aqui, refaz
cada regra a partir da ficha (_docs/manutencao.md) e compara com o que a API
devolve. Para cada alerta e sinal suspeito, olha 10 dias de histórico atrás de
quebra (mudança brusca), que foi o que deixou passar o ARLA 5% do TDP-2E24.
"""
import asyncio
import json
import statistics
import sys
from datetime import date, datetime, timedelta

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text  # noqa: E402
from app.core.database import AsyncSessionLocalReplica  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001/api/v1"
GRUPOS = {3076: "CECOTI", 13956: "FERTRAN", 15686: "VTR", 14330: "Fênix", 14828: "JTP", 14201: "Quataí", 15092: "RCA"}
MIN = 10
falhas: list[str] = []
revisar: list[str] = []
linhas: list[str] = []


def med(xs):
    return statistics.median(xs) if xs else None


async def ler(sql, p):
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '180s'"))
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


async def leituras_24h(g):
    rows = await ler("""
        SELECT d.unit_id, d.local_time, d.can_rpm, d.voltage, d.can_engine_oil_pressure AS oleo, d.can_engine_coolant_temp AS temp,
               d.can_def_level_percent AS arla
        FROM mova.dev_status_30 d
        WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
          AND d.local_time >= now() - interval '24 hours'""", {"g": g})
    por = {}
    for r in rows:
        por.setdefault(r["unit_id"], []).append(r)
    tetos = await ler("""SELECT unit_id, max(can_engine_oil_pressure) AS m FROM mova.dev_status_30
        WHERE unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
          AND local_time >= now() - interval '7 days' AND can_rpm >= 600 GROUP BY 1""", {"g": g})
    return por, {r["unit_id"]: float(r["m"] or 0) for r in tetos}


def esperado(regs, ultimo_sinal_recente, teto7=0.0):
    """Regras da ficha, refeitas do zero."""
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    rpm = lambda r: f(r["can_rpm"]) or 0  # noqa: E731
    oleo_lig = [f(r["oleo"]) for r in regs if rpm(r) >= 600 and (f(r["oleo"]) or 0) > 0]
    oleo_lenta = [f(r["oleo"]) for r in regs if 600 <= rpm(r) < 900 and (f(r["temp"]) or 0) >= 75 and (f(r["oleo"]) or 0) > 0]
    oleo_alto = [f(r["oleo"]) for r in regs if rpm(r) >= 1600 and (f(r["oleo"]) or 0) > 0]
    v_lig = [f(r["voltage"]) for r in regs if rpm(r) >= 500 and (f(r["voltage"]) or 0) >= 5]
    v_desl = [f(r["voltage"]) for r in regs if rpm(r) == 0 and (f(r["voltage"]) or 0) >= 5]
    arla_validas = [f(r["arla"]) for r in regs if r["arla"] is not None and 1 <= f(r["arla"]) <= 100]
    arla_pos = [f(r["arla"]) for r in regs if r["arla"] is not None and f(r["arla"]) > 0]
    arla_cod = [x for x in arla_pos if x > 100]
    arla_baixo = [f(r["arla"]) for r in regs if r["arla"] is not None and f(r["arla"]) <= 5]
    arla_cheio = [x for x in arla_validas if x >= 95]
    ultimo_v = max(regs, key=lambda r: r["local_time"])["voltage"] if regs else None

    sus, al = set(), {}
    if not ultimo_sinal_recente:
        return al, sus, {}
    if len(oleo_lig) >= MIN and len(set(oleo_lig)) == 1:
        sus.add("oleo")
    elif teto7 >= 240 and len(oleo_lenta) >= MIN and med(oleo_lenta) < 69:
        sus.add("oleo_escala")
    elif oleo_lig and max(oleo_lig) >= 240 and len(oleo_alto) >= MIN and oleo_lenta and med(oleo_alto) < med(oleo_lenta) / 2:
        sus.add("oleo_escala")
    if len(arla_cod) >= MIN and len(arla_cod) >= len(arla_pos) / 2:
        sus.add("arla")
    elif arla_baixo and arla_cheio:
        sus.add("arla")
    if ultimo_v is not None and 0 < float(ultimo_v) < 5:
        sus.add("bateria")

    if "oleo" not in sus and "oleo_escala" not in sus and len(oleo_lenta) >= MIN and med(oleo_lenta) < 69:
        al["oleo"] = med(oleo_lenta)
    ref = med(v_lig) or med(v_desl)
    if "bateria" not in sus and ref:
        aten, carga = (24.0, 25.5) if ref > 18 else (12.0, 12.8)  # limites de alerta com a folga de medição
        if len(v_lig) >= MIN and med(v_lig) < carga:
            al["bateria"] = med(v_lig)
        elif len(v_desl) >= MIN and med(v_desl) < aten:
            al["bateria"] = med(v_desl)
    if "arla" not in sus and len(arla_validas) >= MIN and len(arla_validas) >= len(arla_pos) / 2 and med(arla_validas) < 10:
        al["arla"] = med(arla_validas)
    ev = {"oleo_lenta_n": len(oleo_lenta), "oleo_valores": len(set(oleo_lig)), "v_lig": med(v_lig), "v_desl": med(v_desl),
          "arla_validas": len(arla_validas), "arla_cod": len(arla_cod)}
    return al, sus, ev


async def historico_10d(unit_id, campo):
    col = {"oleo": "d.can_engine_oil_pressure", "arla": "d.can_def_level_percent", "bateria": "d.voltage", "temperatura": "d.can_engine_coolant_temp"}[campo]
    return await ler(f"""
        SELECT d.local_time::date AS dia, count(*) AS n,
               count(*) FILTER (WHERE {col} > 100 AND '{campo}' = 'arla') AS codigos,
               count(DISTINCT {col}) AS valores,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY {col}) FILTER (WHERE {col} > 0 AND ({col} <= 100 OR '{campo}' <> 'arla')) AS mediana,
               min({col}) FILTER (WHERE {col} > 0) AS minimo, max({col}) AS maximo
        FROM mova.dev_status_30 d WHERE d.unit_id = :u AND d.local_time >= now() - interval '10 days'
          AND (coalesce(d.can_rpm, 0) > 0 OR '{campo}' IN ('arla', 'bateria'))
        GROUP BY 1 ORDER BY 1""", {"u": unit_id})


def quebra(hist, campo):
    """Mudança brusca de um dia para o outro: o padrão do erro de 30/09."""
    avisos = []
    for a, b in zip(hist, hist[1:]):
        if campo == "arla":
            pa, pb = (a["codigos"] or 0) / max(a["n"], 1), (b["codigos"] or 0) / max(b["n"], 1)
            if pb - pa > 0.3:
                avisos.append(f"{b['dia']}: leituras com código 102 saltaram de {pa:.0%} para {pb:.0%}")
            if a["mediana"] and b["mediana"] and float(b["mediana"]) < float(a["mediana"]) * 0.5:
                avisos.append(f"{b['dia']}: nível caiu de {float(a['mediana']):.0f}% para {float(b['mediana']):.0f}% de um dia para o outro")
        else:
            if a["mediana"] and b["mediana"] and abs(float(b["mediana"]) - float(a["mediana"])) > 0.4 * float(a["mediana"]):
                avisos.append(f"{b['dia']}: mediana mudou de {float(a['mediana']):.1f} para {float(b['mediana']):.1f}")
            if (a["valores"] or 0) > 3 and (b["valores"] or 0) <= 1:
                avisos.append(f"{b['dia']}: passou a mandar um valor só")
    return avisos


async def checar_sinais_tela(cli, g, unit_id, placa):
    """Refaz o que a tela Sinais do motor mostra e compara com o banco."""
    hoje = date.today().isoformat()
    r = await cli.get(f"{API}/reports/history/detailed/cursor", params={
        "start_date": f"{hoje} 00:00:00", "end_date": f"{hoje} 23:59:59", "vehicle_ids": str(unit_id), "limit": 5000, "group_ids": str(g)})
    if r.status_code != 200:
        falhas.append(f"[{GRUPOS[g]}] {placa}: API de sinais respondeu {r.status_code}")
        return
    itens = r.json().get("data") or r.json().get("items") or []

    def achatar(x):
        out = {}
        for k, v in x.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    out.setdefault(k2, v2)
            else:
                out[k] = v
        return out
    regs = sorted((achatar(i) for i in itens), key=lambda x: str(x.get("local_time")))
    ligados = [x for x in regs if (x.get("can_rpm") or 0) > 0 or (x.get("speed") or 0) > 0 or (x.get("can_speed") or 0) > 0]
    base = ligados or regs

    def tela(campo, valido):
        vals = [x.get(campo) for x in base if isinstance(x.get(campo), (int, float))]
        if not vals:
            return None
        if all(v == 0 for v in vals):
            return "sem sensor"
        bons = [v for v in vals if valido(v)]
        if len(bons) < len(vals) / 2:
            return "sem leitura válida"
        return bons[-1]
    mostrado = {
        "arla": tela("can_def_level_percent", lambda v: 0 <= v <= 100),
        "temp": tela("can_engine_coolant_temp", lambda v: -40 < v < 150),
        "tensao": tela("can_control_module_voltage", lambda v: 5 <= v < 40),
    }
    # Banco, direto: última leitura válida com o motor ligado hoje.
    db = (await ler("""
        SELECT
          (SELECT can_def_level_percent FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :d AND (can_rpm > 0 OR speed > 0)
             AND can_def_level_percent BETWEEN 0 AND 100 ORDER BY local_time DESC LIMIT 1) AS arla,
          (SELECT count(*) FILTER (WHERE can_def_level_percent > 100) * 1.0 / nullif(count(*) FILTER (WHERE can_def_level_percent IS NOT NULL), 0)
             FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :d AND (can_rpm > 0 OR speed > 0)) AS arla_cod,
          (SELECT can_engine_coolant_temp FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :d AND (can_rpm > 0 OR speed > 0)
             ORDER BY local_time DESC LIMIT 1) AS temp,
          (SELECT can_control_module_voltage FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :d AND (can_rpm > 0 OR speed > 0)
             AND can_control_module_voltage >= 5 ORDER BY local_time DESC LIMIT 1) AS tensao,
          (SELECT count(*) FROM mova.dev_status_30 WHERE unit_id = :u AND local_time >= :d) AS n""",
        {"u": unit_id, "d": datetime.combine(date.today(), datetime.min.time())}))[0]
    n_api = len(regs)
    if n_api != db["n"] and abs(n_api - db["n"]) > 5:
        revisar.append(f"[{GRUPOS[g]}] {placa}: tela recebeu {n_api} leituras, banco tem {db['n']} hoje")
    if mostrado["arla"] not in (None, "sem sensor", "sem leitura válida"):
        if db["arla_cod"] and float(db["arla_cod"]) >= 0.5:
            falhas.append(f"[{GRUPOS[g]}] {placa}: tela mostraria ARLA {mostrado['arla']}% mas {float(db['arla_cod']):.0%} das leituras são código 102")
        elif db["arla"] is not None and float(db["arla"]) != float(mostrado["arla"]):
            falhas.append(f"[{GRUPOS[g]}] {placa}: ARLA tela {mostrado['arla']} × banco {db['arla']}")
    for k in ("temp", "tensao"):
        if isinstance(mostrado[k], (int, float)) and db[k] is not None and abs(float(db[k]) - float(mostrado[k])) > 0.01:
            falhas.append(f"[{GRUPOS[g]}] {placa}: {k} tela {mostrado[k]} × banco {db[k]} (última com motor ligado)")
    return mostrado


async def main():
    async with httpx.AsyncClient(timeout=300) as cli:
        for g, nome in GRUPOS.items():
            p = (await cli.get(f"{API}/manutencao/painel", params={"group_id": g})).json()
            regs, tetos = await leituras_24h(g)
            n_al = n_sus = 0
            conferidos = 0
            for v in p["veiculos"]:
                u = v["unit_id"]
                recente = v.get("ultimo_sinal") and datetime.fromisoformat(v["ultimo_sinal"]) > datetime.now() - timedelta(hours=6)
                al_e, sus_e, ev = esperado(regs.get(u, []), recente, tetos.get(u, 0.0))
                al_api = {a["chave"]: a for a in v["alertas"] if a["chave"] != "temperatura"}
                sus_api = {s["sinal"] for s in v.get("sinais_suspeitos", [])}
                n_al += len(v["alertas"])
                n_sus += len(sus_api)
                conferidos += 1
                if set(al_e) != set(al_api):
                    falhas.append(f"[{nome}] {v['placa']}: alertas API {sorted(al_api)} × esperado {sorted(al_e)} | {ev}")
                if sus_e != sus_api:
                    falhas.append(f"[{nome}] {v['placa']}: suspeitos API {sorted(sus_api)} × esperado {sorted(sus_e)} | {ev}")
                for k, a in al_api.items():
                    if k in al_e:
                        num = float(str(a["valor"]).split()[0].replace("%", ""))
                        if abs(num - round(al_e[k], 1)) > 0.6:
                            falhas.append(f"[{nome}] {v['placa']}: {k} API {a['valor']} × banco {al_e[k]:.1f}")
                    # Toda quebra de histórico vira item de revisão (o erro do ARLA 5%).
                    hist = await historico_10d(u, k)
                    for q in quebra(hist, k):
                        revisar.append(f"[{nome}] {v['placa']} — alerta '{a['titulo']} {a['valor']}' com quebra no histórico: {q}")
                if g == 3076 and (al_api or sus_api):
                    await checar_sinais_tela(cli, g, u, v["placa"])
            linhas.append(f"| {nome} | {conferidos} | {n_al} | {sum(1 for v in p['veiculos'] for a in v['alertas'] if a['nivel']=='critico')} | {n_sus} |")
            # Início: os cartões leem o painel; conferir a conta de "Em corretiva".
            ordens = (await cli.get(f"{API}/manutencao/ordens", params={"group_id": g})).json()
            corr = {o["unit_id"] for o in ordens if o["tipo"] == "corretiva" and o["status"] not in ("concluida", "cancelada")}
            em_corr = sum(1 for v in p["veiculos"] if v["alertas"] or v["unit_id"] in corr)
            linhas[-1] += f" {em_corr} |"
    print("| Cliente | Veículos conferidos | Alertas | Críticos | Suspeitos | Em corretiva (Início = quadro) |")
    print("|---|---|---|---|---|---|")
    print("\n".join(linhas))
    print(f"\nDIVERGÊNCIAS ({len(falhas)}):")
    print("\n".join(falhas) or "nenhuma")
    print(f"\nPARA REVISAR ({len(revisar)}):")
    print("\n".join(revisar) or "nenhum")

asyncio.run(main())
