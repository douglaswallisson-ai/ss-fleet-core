"""
Manutenção por risco — inspeção priorizada, alertas por tendência e linha do
tempo para causa raiz (pedido do PM, 06/10/2026).

O que dá para afirmar com o dado de hoje (conferido no banco em 06/10/2026):
- NÃO há código de falha (DTC) nem luz de injeção chegando (0 em 10.272
  unidades) e o banco não tem histórico de ordens de serviço. Por isso aqui não
  há "previsão de quebra": há TENDÊNCIA de sinal e RISCO por evidência. A
  correlação com falha real só vem depois que as corretivas forem registradas
  na plataforma (manutencao.py, ordens).
- Sinais passam pelas mesmas regras da manutenção (códigos de "sem dado",
  sensor travado, tensão medida no rastreador) — ver manutencao.py.

Nota de risco (0–100), sempre com o motivo de cada ponto:
- alerta atual crítico 30, de atenção 12 (manutencao._alertas_do_veiculo);
- item do plano vencido 15, vencendo 5;
- tendência ruim (bateria, temperatura, consumo) 15 cada;
- condução agressiva: até 25, pela posição do veículo na frota (eventos de
  freada, aceleração, embreagem e excesso de velocidade a cada 100 km, 30 dias).
SUPOSIÇÃO: os pesos acima são ponto de partida para validar com a oficina;
ajustar quando houver histórico de corretivas para comparar.
"""

import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.api.v1.endpoints import manutencao as man
from app.core import camera as cam
from app.core import combustivel as plaus
from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

_CACHE: dict[tuple, tuple[float, object]] = {}
CACHE_S = 1800
DIAS_TENDENCIA = 14
MIN_DIAS = 7

PESO = {"critico": 30, "atencao": 12, "vencido": 15, "vencendo": 5, "tendencia": 15, "conducao_max": 25}


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '90s'"))
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


def inclinacao(pontos: list[tuple[float, float]]) -> Optional[float]:
    """Inclinação da reta (mínimos quadrados) — variação por dia."""
    if len(pontos) < 2:
        return None
    n = len(pontos)
    mx = sum(x for x, _ in pontos) / n
    my = sum(y for _, y in pontos) / n
    den = sum((x - mx) ** 2 for x, _ in pontos)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in pontos) / den


def _mediana(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def tendencias(dias: list[dict]) -> list[dict]:
    """Alertas por tendência a partir da série diária de um veículo (ordenada por dia).

    Regra conservadora (validada em 06/10/2026 no PZO-7A16 da CECOTI): compara a
    MEDIANA dos 4 primeiros dias com a dos 4 últimos — um dia atípico (leitura
    logo após desligar, trecho curto) não vira tendência.
    """
    out = []
    if not dias:
        return out
    d0 = dias[0]["dia"]

    def serie(campo, ok=lambda v: True):
        return [((d["dia"] - d0).days, float(d[campo])) for d in dias if d.get(campo) is not None and ok(float(d[campo]))]

    # Bateria em repouso (motor desligado): 24 V ou 12 V pelo nível.
    bat = serie("v_repouso", lambda v: 5 < v < 32)
    if len(bat) >= 8:
        ini, fim = _mediana([y for _, y in bat[:4]]), _mediana([y for _, y in bat[-4:]])
        s = inclinacao(bat)
        v24 = fim > 18
        limite, queda_min, teto = (24.0, 0.5, 25.0) if v24 else (12.0, 0.25, 12.5)
        if s is not None and s < 0 and ini - fim >= queda_min and fim < teto:
            dias_ate = int((fim - limite) / -s) if fim > limite else 0
            out.append({"chave": "bateria", "titulo": "Bateria perdendo carga",
                        "detalhe": f"Em repouso: {ini:.1f} V → {fim:.1f} V (mediana do início e do fim de {len(bat)} dias)"
                                   + (f"; no ritmo atual chega a {limite:.0f} V em ~{dias_ate} dias." if dias_ate else f"; já abaixo de {limite:.0f} V."),
                        "por_dia": round(s, 3)})
    # Temperatura do motor (média com o motor trabalhando).
    tmp = serie("temp_media", lambda v: 40 < v < 130)
    if len(tmp) >= 8:
        ini, fim = _mediana([y for _, y in tmp[:4]]), _mediana([y for _, y in tmp[-4:]])
        if fim - ini >= 5 and fim >= 92:
            out.append({"chave": "temperatura", "titulo": "Temperatura do motor subindo",
                        "detalhe": f"Média em trabalho: {ini:.0f} °C → {fim:.0f} °C (mediana do início e do fim de {len(tmp)} dias).",
                        "por_dia": round(inclinacao(tmp) or 0, 2)})
    # Consumo: primeira contra segunda metade, cada uma com km suficiente.
    kml = [d for d in dias if d.get("km_l") and 0.5 <= float(d["km_l"]) <= 25 and d.get("km_comb")]
    # Dia fora do padrão do próprio veículo sai (PZO-7A16: 9,3 km/l num dia, ~4,7 no normal —
    # em veículo leve o combustível chega em degraus de 0,5 L e um dia distorce a média).
    if kml:
        med = _mediana([float(d["km_l"]) for d in kml])
        kml = [d for d in kml if 0.6 * med <= float(d["km_l"]) <= 1.4 * med]
    if len(kml) >= 8:
        meio = len(kml) // 2
        a, b = kml[:meio], kml[meio:]
        km_a, km_b = sum(float(d["km_comb"]) for d in a), sum(float(d["km_comb"]) for d in b)
        if len(a) >= 4 and len(b) >= 4 and km_a >= 800 and km_b >= 800:
            antes = km_a / max(1e-9, sum(float(d["litros"]) for d in a))
            depois = km_b / max(1e-9, sum(float(d["litros"]) for d in b))
            if antes > 0 and (depois - antes) / antes <= -0.12:
                out.append({"chave": "consumo", "titulo": "Consumo piorando",
                            "detalhe": f"{antes:.2f} km/l ({round(km_a)} km) → {depois:.2f} km/l ({round(km_b)} km): {(depois - antes) / antes * 100:.0f}%. Rota ou carga diferente também explicam: conferir antes de mandar para a oficina.",
                            "por_dia": None})
    return out


def nota(v: dict, tend: list[dict], pct_conducao: Optional[float], conducao_suspeita: Optional[float] = None) -> tuple[int, list[dict]]:
    """(nota 0–100, motivos com os pontos). `conducao_suspeita` = eventos por km quando passam de 1."""
    motivos = []
    if conducao_suspeita:
        motivos.append({"pontos": 0, "texto": f"Contagem de eventos suspeita ({conducao_suspeita:.1f} por km): conferir a configuração do equipamento",
                        "tipo": "conducao"})
        pct_conducao = None
    for a in v.get("alertas", []):
        pts = PESO["critico"] if a["nivel"] == "critico" else PESO["atencao"]
        motivos.append({"pontos": pts, "texto": f"{a['titulo']} ({a.get('valor') or 'agora'})", "tipo": "alerta"})
    if v.get("vencidos"):
        motivos.append({"pontos": PESO["vencido"] * v["vencidos"], "texto": f"{v['vencidos']} item(ns) do plano vencido(s)", "tipo": "plano"})
    if v.get("vencendo"):
        motivos.append({"pontos": PESO["vencendo"] * v["vencendo"], "texto": f"{v['vencendo']} item(ns) vencendo", "tipo": "plano"})
    for t in tend:
        motivos.append({"pontos": PESO["tendencia"], "texto": t["titulo"], "tipo": "tendencia"})
    if pct_conducao is not None and pct_conducao >= 0.5:
        pts = round(PESO["conducao_max"] * (pct_conducao - 0.5) / 0.5)
        if pts:
            motivos.append({"pontos": pts, "texto": f"Condução mais agressiva que {round(pct_conducao * 100)}% da frota", "tipo": "conducao"})
    motivos.sort(key=lambda m: -m["pontos"])
    return min(100, sum(m["pontos"] for m in motivos)), motivos


SQL_DIARIO = """
WITH un AS (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1),
s AS (
    SELECT d.unit_id, d.local_time::date AS dia,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY d.voltage) FILTER (WHERE NOT d.ignition AND d.voltage > 5) AS v_repouso,
           avg(d.can_engine_coolant_temp) FILTER (WHERE d.ignition AND coalesce(d.can_rpm, d.rpm) > 600
                                                  AND d.can_engine_coolant_temp BETWEEN 40 AND 130) AS temp_media
    FROM mova.dev_status_30 d
    WHERE d.unit_id IN (SELECT id FROM un) AND d.local_time >= :ini
    GROUP BY 1, 2),
c AS (
    SELECT h.unit_id, h.dt::date AS dia, sum({litros}) / 1000.0 AS litros, sum({km_comb}) / 1000.0 AS km_comb
    FROM mova.con_driver_h_km h WHERE h.group_id = :g AND h.dt >= :ini GROUP BY 1, 2)
SELECT coalesce(s.unit_id, c.unit_id) AS unit_id, coalesce(s.dia, c.dia) AS dia, s.v_repouso, s.temp_media,
       c.litros, c.km_comb, CASE WHEN c.litros > 0 THEN c.km_comb / c.litros END AS km_l
FROM s FULL JOIN c ON c.unit_id = s.unit_id AND c.dia = s.dia
ORDER BY 1, 2
"""

SQL_CONDUCAO = """
SELECT unit_id, sum(distance_traveled_hist) / 1000.0 AS km,
       sum(coalesce(count_break_excess, 0) + coalesce(count_acel_excess, 0) + coalesce(count_clutch_excess, 0)
           + coalesce(count_speed_excess_dry_l1, 0) + coalesce(count_speed_excess_dry_l2, 0) + coalesce(count_speed_excess_dry_l3, 0)
           + coalesce(count_speed_excess_wet_l1, 0) + coalesce(count_speed_excess_wet_l2, 0) + coalesce(count_speed_excess_wet_l3, 0)) AS eventos,
       sum(coalesce(count_break_excess, 0)) AS freadas, sum(coalesce(count_acel_excess, 0)) AS aceleracoes,
       sum(coalesce(count_clutch_excess, 0)) AS embreagem
FROM mova.con_driver_h_km WHERE group_id = :g AND dt >= :ini GROUP BY 1
"""


@router.get("/painel")
async def painel(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Veículos ordenados pela nota de risco, com motivos, tendências e condução."""
    man._grupo_ok(user, group_id)
    k = ("risco", group_id)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    base = await man.painel(group_id=group_id, user=user)
    hoje = date.today()
    ini = datetime.combine(hoje - timedelta(days=DIAS_TENDENCIA), datetime.min.time())
    diario = await _ler(SQL_DIARIO.format(litros=plaus.litros_ml("h"), km_comb=plaus.km_com_combustivel_m("h")), {"g": group_id, "ini": ini})
    cond = await _ler(SQL_CONDUCAO, {"g": group_id, "ini": datetime.combine(hoje - timedelta(days=30), datetime.min.time())})

    por_unidade: dict[int, list[dict]] = {}
    for d in diario:
        por_unidade.setdefault(d["unit_id"], []).append(d)
    taxas = {}
    for c in cond:
        km = float(c["km"] or 0)
        if km >= 300:  # pouco km distorce a taxa
            taxas[c["unit_id"]] = (100 * float(c["eventos"] or 0) / km, c, km)
    # Mais de 1 evento por km é equipamento, não condução (TDV-7E89: 5 por km): fica fora do ranking.
    ordenadas = sorted(t for t, _, _ in taxas.values() if t < 100)

    def percentil(x):
        return sum(1 for t in ordenadas if t <= x) / len(ordenadas) if ordenadas else None

    veiculos = []
    for v in base["veiculos"]:
        tend = tendencias(por_unidade.get(v["unit_id"], []))
        tx = taxas.get(v["unit_id"])
        suspeita = tx[0] / 100 if tx and tx[0] >= 100 else None
        pct = percentil(tx[0]) if tx and not suspeita else None
        pontos, motivos = nota(v, tend, pct, suspeita)
        veiculos.append({
            "unit_id": v["unit_id"], "placa": v["placa"], "prefixo": v["prefixo"], "modelo": v["modelo"],
            "categoria_id": v["categoria_id"], "categoria": v["categoria"],
            "risco": pontos, "classe": "alto" if pontos >= 60 else "medio" if pontos >= 30 else "baixo",
            "motivos": motivos, "tendencias": tend,
            "conducao": None if not tx else {"eventos_100km": round(tx[0], 2), "percentil": round(pct, 2) if pct is not None else None,
                                             "km_30d": round(tx[2]), "freadas": int(tx[1]["freadas"]), "aceleracoes": int(tx[1]["aceleracoes"]),
                                             "embreagem": int(tx[1]["embreagem"])},
            "alertas": len(v["alertas"]), "vencidos": v["vencidos"], "ordens_abertas": v["ordens_abertas"],
        })
    veiculos.sort(key=lambda x: (-x["risco"], x["prefixo"] or x["placa"] or ""))
    r = {"veiculos": veiculos, "resumo": {c: sum(1 for v in veiculos if v["classe"] == c) for c in ("alto", "medio", "baixo")},
         "pesos": PESO, "dias_tendencia": DIAS_TENDENCIA, "gerado_em": datetime.now().isoformat(timespec="seconds")}
    _CACHE[k] = (time.time(), r)
    return r


@router.get("/causa-raiz/{unit_id}")
async def causa_raiz(unit_id: int, ate: Optional[date] = Query(None), dias: int = Query(7, ge=3, le=30),
                     user=Depends(require_permission("reports", "read"))):
    """Linha do tempo dos dias antes de uma falha: tudo que pode explicar a causa, num lugar só."""
    un = await _ler("SELECT id, group_id, label AS placa, label2 AS prefixo FROM mova.tracked_unit WHERE id = :u", {"u": unit_id})
    if not un:
        raise HTTPException(404, "Veículo não encontrado.")
    man._grupo_ok(user, un[0]["group_id"])
    fim = ate or date.today()
    ini = fim - timedelta(days=dias - 1)
    p = {"u": unit_id, "ini": datetime.combine(ini, datetime.min.time()), "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time())}
    sinais = await _ler("""
        SELECT d.local_time::date AS dia, count(*) AS leituras,
               max(d.can_engine_coolant_temp) FILTER (WHERE d.can_engine_coolant_temp BETWEEN 40 AND 130) AS temp_max,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.voltage) FILTER (WHERE NOT d.ignition AND d.voltage > 5) AS v_repouso,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.voltage) FILTER (WHERE d.ignition AND d.voltage > 5) AS v_carga,
               max(coalesce(nullif(d.can_rpm, 0), d.rpm)) AS rpm_max, max(d.speed) AS vel_max,
               min(d.can_def_level_percent) FILTER (WHERE d.can_def_level_percent BETWEEN 1 AND 100) AS arla_min
        FROM mova.dev_status_30 d WHERE d.unit_id = :u AND d.local_time >= :ini AND d.local_time < :fim GROUP BY 1""", p)
    eventos = await _ler("""
        SELECT d.local_time::date AS dia, te.name AS evento, count(*) AS n
        FROM mova.dev_status_30 d JOIN mova.tracker_event te ON te.id = d.tracker_event_id
        WHERE d.unit_id = :u AND d.local_time >= :ini AND d.local_time < :fim
          AND d.tracker_event_id IN (7, 9, 11, 13, 27, 37, 148, 153, 159, 163, 288, 359, 405, 407, 440)
        GROUP BY 1, 2""", p)
    # Nome do tipo pela chave (type, modelo, source): pelo id sozinho ADAS vira DMS (app/core/camera.py).
    camera = await _ler(f"""
        SELECT h.local_time::date AS dia, {cam.NOME_SQL} AS evento, count(*) AS n
        FROM vcms.vcms_history h {cam.JOIN_TIPO}
        WHERE h.unit_id = :u AND h.local_time >= :ini AND h.local_time < :fim GROUP BY 1, 2""", p)
    alarmes = await _ler("""
        SELECT av.initial_time::date AS dia, a.name AS evento, count(*) AS n
        FROM mova.alarm_violation av JOIN mova.alarm a ON a.id = av.alarm_id
        WHERE av.unit_id = :u AND av.initial_time >= :ini AND av.initial_time < :fim GROUP BY 1, 2""", p)
    rodou = await _ler(f"""
        SELECT h.dt::date AS dia, sum(h.distance_traveled_hist) / 1000.0 AS km, sum(h.time_traveled_hist) / 3600.0 AS horas,
               sum({plaus.litros_ml('h')}) / 1000.0 AS litros, sum({plaus.km_com_combustivel_m('h')}) / 1000.0 AS km_comb,
               string_agg(DISTINCT h.driver, ', ') AS motoristas
        FROM mova.con_driver_h_km h WHERE h.unit_id = :u AND h.dt >= :ini AND h.dt < :fim GROUP BY 1""", p)
    with man._con() as c:
        servicos = [dict(r) for r in c.execute("SELECT data, servico, oficina, obs FROM servico WHERE unit_id = ? AND data BETWEEN ? AND ?",
                                                (unit_id, ini.isoformat(), fim.isoformat()))]
        ordens = [dict(r) for r in c.execute("SELECT id, aberta_em, concluida_em, status, tipo, titulo, origem FROM ordem WHERE unit_id = ?", (unit_id,))]

    linha = []
    for i in range(dias):
        d = ini + timedelta(days=i)
        s = next((x for x in sinais if x["dia"] == d), {})
        r = next((x for x in rodou if x["dia"] == d), {})
        km = float(r["km"]) if r.get("km") is not None else None
        litros = float(r["litros"]) if r.get("litros") else None
        linha.append({
            "dia": d.isoformat(),
            "km": round(km, 1) if km is not None and 0 <= km <= 2000 else None,
            "horas": round(float(r["horas"]), 1) if r.get("horas") else None,
            # Dia quase parado não tem km/l que signifique algo (0,1 km com 0,5 L = 0,2 km/l).
            "km_l": round(float(r["km_comb"]) / litros, 2) if litros and r.get("km_comb") and km and km >= 20 else None,
            "motoristas": r.get("motoristas"),
            "temp_max": round(float(s["temp_max"])) if s.get("temp_max") else None,
            "v_repouso": round(float(s["v_repouso"]), 1) if s.get("v_repouso") else None,
            "v_carga": round(float(s["v_carga"]), 1) if s.get("v_carga") else None,
            "rpm_max": int(s["rpm_max"]) if s.get("rpm_max") else None,
            "arla_min": round(float(s["arla_min"])) if s.get("arla_min") else None,
            "eventos": sorted(({"evento": e["evento"].capitalize(), "n": int(e["n"])} for e in eventos if e["dia"] == d), key=lambda e: -e["n"]),
            "camera": sorted(({"evento": e["evento"], "n": int(e["n"])} for e in camera if e["dia"] == d), key=lambda e: -e["n"]),
            "alarmes": sorted(({"evento": e["evento"], "n": int(e["n"])} for e in alarmes if e["dia"] == d), key=lambda e: -e["n"]),
            "servicos": [x for x in servicos if x["data"] == d.isoformat()],
        })
    # Pontos de atenção: o que se destaca no período (evidência, não diagnóstico).
    pontos = []
    temps = [x["temp_max"] for x in linha if x["temp_max"]]
    if temps and max(temps) >= 100:
        dia_t = next(x["dia"] for x in linha if x["temp_max"] == max(temps))
        pontos.append(f"Temperatura chegou a {max(temps)} °C em {dia_t[8:10]}/{dia_t[5:7]}.")
    reps = [x["v_repouso"] for x in linha if x["v_repouso"]]
    if len(reps) >= 3 and reps[-1] < reps[0] - 0.8:
        pontos.append(f"Bateria em repouso caiu de {reps[0]} V para {reps[-1]} V no período.")
    total_ev: dict[str, int] = {}
    for x in linha:
        for e in x["eventos"] + x["camera"]:
            total_ev[e["evento"]] = total_ev.get(e["evento"], 0) + e["n"]
    km_total = sum(x["km"] or 0 for x in linha)
    for nome, n in sorted(total_ev.items(), key=lambda t: -t[1])[:3]:
        if n < 10:
            continue
        # Mais de um evento por km não é condução: é configuração ou sensor do equipamento
        # (TDV-7E89, CECOTI: 2.393 acelerações bruscas em 488 km num dia, 06/10/2026).
        if km_total >= 50 and n / km_total >= 1:
            pontos.append(f"{nome}: {n} ocorrências em {round(km_total)} km ({n / km_total:.1f} por km) — contagem suspeita do equipamento; "
                          "conferir a configuração antes de cobrar o motorista.")
        else:
            pontos.append(f"{nome}: {n} ocorrências no período.")
    kmls = [x["km_l"] for x in linha if x["km_l"]]
    if len(kmls) >= 4 and kmls[-1] < 0.85 * (sum(kmls[:-1]) / len(kmls[:-1])):
        pontos.append(f"Consumo do último dia ({kmls[-1]} km/l) pior que a média dos anteriores.")
    return {"veiculo": un[0], "inicio": ini.isoformat(), "fim": fim.isoformat(), "dias": linha, "pontos_de_atencao": pontos,
            "ordens": ordens, "aviso": "Evidência organizada para a investigação. A causa é decisão de quem investiga: não há código de falha (DTC) chegando do equipamento."}
