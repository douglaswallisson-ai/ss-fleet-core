"""
Painel sinótico do transporte urbano (ex.: Consórcio Fênix) com dados reais.
Só leitura.

O urbano não tem itinerário cadastrado. A régua de cada linha e sentido é
montada a partir do TRAJETO REAL de uma viagem completa recente dessa linha
(posições de `dev_status_30` entre o início e o fim da viagem). Os ônibus em
operação são encaixados nessa régua pela posição atual (`dev_status`).

Fontes e regras:
- Viagens: `con_telemetry` com `trip_status = true`, `line_number`,
  `trip_direction` (0 ida, 1 volta) e `trip_number` — a mesma regra do
  relatório "Viagens Produtivas" (ver operacao.py). Cada linha da tabela é um
  trecho; a viagem é (veículo, número da viagem, dia).
- Ônibus na linha agora: o trecho mais recente de cada veículo nos últimos
  40 min é desta linha (se o último trecho for de outra linha, ele trocou).
- Encaixe: ponto mais próximo da régua; a mais de 300 m, "fora da rota".
- Pontos de referência: nomes das cercas onde os trechos da viagem de
  referência começam (terminais, praças, ruas marcadas pelo cliente).
- Intervalo até o da frente: distância na régua ÷ velocidade média da viagem
  de referência.

SUPOSIÇÃO: "colado" = intervalo menor que 40% do intervalo médio da linha;
"buraco" = maior que 160%. Confirmar com a operação do cliente.
Todos os horários são hora local (as três tabelas gravam em hora de Brasília).
"""

import math
import time
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

JANELA_ATIVO_MIN = 40
RAIO_ROTA_M = 300
COLADO = 0.4
BURACO = 1.6
_CACHE: dict[tuple, tuple[float, object]] = {}


def _cache(chave: tuple, ttl: int):
    c = _CACHE.get(chave)
    return c[1] if c and time.time() - c[0] < ttl else None


def _guardar(chave: tuple, valor):
    _CACHE[chave] = (time.time(), valor)
    return valor


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


def _grupo_permitido(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    grupos = {g for g, _ in (getattr(user, "group_access", None) or [])}
    if group_id not in grupos:
        raise HTTPException(403, "Sem acesso a este grupo.")


# ------------------------------- Geometria --------------------------------


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _regua(pontos: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], list[float]]:
    """Limpa o trajeto (pontos a menos de 15 m do anterior) e acumula a distância."""
    limpo: list[tuple[float, float]] = []
    for p in pontos:
        if not limpo or _dist_m(limpo[-1], p) >= 15:
            limpo.append(p)
    acum = [0.0]
    for i in range(1, len(limpo)):
        acum.append(acum[-1] + _dist_m(limpo[i - 1], limpo[i]))
    return limpo, acum


def _projetar(p: tuple[float, float], linha: list[tuple[float, float]], acum: list[float]) -> tuple[Optional[float], float]:
    """(metros ao longo da régua, distância até ela). Projeção plana local, suficiente para trechos curtos."""
    melhor = (None, float("inf"))
    kx = 111_320 * math.cos(math.radians(p[0]))
    ky = 110_540
    for i in range(len(linha) - 1):
        a, b = linha[i], linha[i + 1]
        ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
        bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(cx, cy)
        if d < melhor[1]:
            melhor = (acum[i] + t * (acum[i + 1] - acum[i]), d)
    return melhor


def _simplificar(linha: list[tuple[float, float]], maximo: int = 400) -> list[list[float]]:
    passo = max(1, len(linha) // maximo)
    out = linha[::passo]
    if linha and out[-1] != linha[-1]:
        out.append(linha[-1])
    return [[round(a, 6), round(b, 6)] for a, b in out]


# ------------------------------- Rotas ------------------------------------


@router.get("/linhas")
async def linhas(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Linhas com viagem produtiva nas últimas 3 horas, e quantos carros estão nelas agora."""
    _grupo_permitido(user, group_id)
    if (c := _cache(("linhas", group_id), 120)) is not None:
        return c
    rows = await _ler(
        f"""
        WITH ult AS (
          SELECT DISTINCT ON (ct.unit_id) ct.unit_id, ct.line_number, ct.trip_status, ct.start_time
          FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
          WHERE tu.group_id = :g AND ct.start_time >= now() - interval '3 hours'
          ORDER BY ct.unit_id, ct.start_time DESC)
        SELECT line_number::text AS linha,
               count(*) FILTER (WHERE trip_status AND start_time >= now() - interval '{JANELA_ATIVO_MIN} minutes') AS agora,
               count(*) AS carros_3h
        FROM ult WHERE line_number IS NOT NULL AND line_number > 0
        GROUP BY 1 ORDER BY 2 DESC, 3 DESC
        """,
        {"g": group_id},
    )
    return _guardar(("linhas", group_id), [dict(r) for r in rows])


async def _referencia(group_id: int, linha: str, sentido: int) -> Optional[dict]:
    """Trajeto da viagem completa mais longa (hoje ou ontem) desta linha e sentido. Guardado por 6 h."""
    chave = ("ref", group_id, linha, sentido, date.today())
    if (c := _cache(chave, 6 * 3600)) is not None:
        return c
    viagens = await _ler(
        """
        SELECT ct.unit_id, ct.trip_number, ct.start_time::date AS dia,
               min(ct.start_time) AS ini, max(ct.end_time) AS fim, count(*) AS trechos
        FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
        WHERE tu.group_id = :g AND ct.line_number = :l AND ct.trip_direction = :s AND ct.trip_status = true
          AND ct.start_time >= current_date - 1 AND ct.end_time IS NOT NULL
        GROUP BY 1, 2, 3
        HAVING max(ct.end_time) - min(ct.start_time) BETWEEN interval '10 minutes' AND interval '3 hours'
        ORDER BY count(*) DESC, max(ct.end_time) DESC LIMIT 5
        """,
        {"g": group_id, "l": int(linha), "s": sentido},
    )
    for v in viagens:
        pos = await _ler(
            """SELECT latitude::float AS lat, longitude::float AS lon FROM mova.dev_status_30
               WHERE unit_id = :u AND local_time BETWEEN :i AND :f AND latitude <> 0 AND longitude <> 0
               ORDER BY local_time""",
            {"u": v["unit_id"], "i": v["ini"], "f": v["fim"]},
        )
        linha_pts, acum = _regua([(r["lat"], r["lon"]) for r in pos])
        if len(linha_pts) < 10 or acum[-1] < 2000:
            continue
        marcos = await _ler(
            """SELECT ct.start_area_name AS nome, ct.start_lat::float AS lat, ct.start_lon::float AS lon, min(ct.start_time) AS em
               FROM mova.con_telemetry ct
               WHERE ct.unit_id = :u AND ct.trip_number = :t AND ct.start_time BETWEEN :i AND :f
                 AND ct.start_area_name IS NOT NULL AND ct.start_lat IS NOT NULL
               GROUP BY 1, 2, 3 ORDER BY 4""",
            {"u": v["unit_id"], "t": v["trip_number"], "i": v["ini"], "f": v["fim"]},
        )
        pontos_ref = []
        for m in marcos:
            km, d = _projetar((m["lat"], m["lon"]), linha_pts, acum)
            nome = (m["nome"] or "").strip()
            if km is None or d > RAIO_ROTA_M or not nome:
                continue
            if any(p["nome"] == nome or abs(p["m"] - km) < 300 for p in pontos_ref):
                continue
            pontos_ref.append({"nome": nome, "m": km})
        # Cercas do cliente perto do trajeto (terminais, escolas, praças).
        # Nos polígonos de mova.cerca, X = latitude e Y = longitude (ver mapa.py).
        lats = [q[0] for q in linha_pts]
        lons = [q[1] for q in linha_pts]
        cercas = await _ler(
            """SELECT name AS nome,
                      CASE WHEN polygroup_type = 1 THEN latitude::float ELSE ST_X(ST_Centroid(polygon)) END AS lat,
                      CASE WHEN polygroup_type = 1 THEN longitude::float ELSE ST_Y(ST_Centroid(polygon)) END AS lon
               FROM mova.cerca
               WHERE status = 1 AND group_id = :g AND (
                 (polygroup_type = 1 AND latitude BETWEEN :la AND :lb AND longitude BETWEEN :oa AND :ob)
                 OR (polygroup_type <> 1 AND polygon && ST_MakeEnvelope(:la, :oa, :lb, :ob, 4326)))
               LIMIT 300""",
            {"g": group_id, "la": min(lats) - 0.002, "lb": max(lats) + 0.002, "oa": min(lons) - 0.002, "ob": max(lons) + 0.002},
        )
        for m in cercas:
            if m["lat"] is None or m["lon"] is None:
                continue
            km, d = _projetar((m["lat"], m["lon"]), linha_pts, acum)
            nome = (m["nome"] or "").strip()
            if km is None or d > 200 or not nome:
                continue
            if any(p["nome"] == nome or abs(p["m"] - km) < 300 for p in pontos_ref):
                continue
            pontos_ref.append({"nome": nome, "m": km})
        dur = (v["fim"] - v["ini"]).total_seconds()
        ref = {
            "linha": linha_pts, "acum": acum, "comprimento_m": acum[-1], "duracao_s": dur,
            "vel_media_ms": acum[-1] / dur if dur > 0 else None,
            "pontos": sorted(pontos_ref, key=lambda p: p["m"]),
            "base": {"unit_id": v["unit_id"], "viagem": v["trip_number"], "dia": v["dia"].isoformat(),
                     "inicio": v["ini"].strftime("%H:%M"), "fim": v["fim"].strftime("%H:%M")},
        }
        return _guardar(chave, ref)
    return _guardar(chave, None)


@router.get("")
async def sinotico(
    group_id: int = Query(...),
    linha: str = Query(..., pattern=r"^\d{1,6}$"),
    user=Depends(require_permission("reports", "read")),
):
    _grupo_permitido(user, group_id)
    ultimos = await _ler(
        f"""
        WITH ult AS (
          SELECT DISTINCT ON (ct.unit_id) ct.unit_id, ct.line_number, ct.trip_direction, ct.trip_number,
                 ct.trip_status, ct.start_time
          FROM mova.con_telemetry ct JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
          WHERE tu.group_id = :g AND ct.start_time >= now() - interval '{JANELA_ATIVO_MIN} minutes'
          ORDER BY ct.unit_id, ct.start_time DESC)
        SELECT u.unit_id, u.trip_direction AS sentido, u.trip_number AS viagem, u.start_time,
               tu.label AS placa, tu.label2 AS prefixo,
               ds.latitude::float AS lat, ds.longitude::float AS lon, ds.speed, ds.local_time, ds.driver_name
        FROM ult u
        JOIN mova.tracked_unit tu ON tu.id = u.unit_id
        LEFT JOIN mova.dev_status ds ON ds.unit_id = u.unit_id
        WHERE u.line_number = :l AND u.trip_status = true
        """,
        {"g": group_id, "l": int(linha)},
    )

    sentidos = []
    for s in (0, 1):
        ref = await _referencia(group_id, linha, s)
        carros = [r for r in ultimos if r["sentido"] == s]
        onibus = []
        for r in carros:
            km = d = None
            if ref and r["lat"] and r["lon"]:
                km, d = _projetar((r["lat"], r["lon"]), ref["linha"], ref["acum"])
            fora = km is None or (d or 0) > RAIO_ROTA_M
            onibus.append({
                "unit_id": r["unit_id"], "prefixo": r["prefixo"] or r["placa"], "placa": r["placa"],
                "viagem": r["viagem"], "velocidade": float(r["speed"]) if r["speed"] is not None else None,
                "ultimo": r["local_time"], "motorista": (r["driver_name"] or "").strip() or None,
                "m": None if fora else round(km), "pct": None if fora or not ref else round(100 * km / ref["comprimento_m"], 1),
                "fora_rota": fora, "distancia_rota_m": None if d is None else round(d),
                "lat": r["lat"], "lon": r["lon"],
            })
        # Intervalo até o carro da frente (mais adiantado na régua).
        na_rota = sorted((o for o in onibus if not o["fora_rota"]), key=lambda o: o["m"])
        for i, o in enumerate(na_rota):
            frente = na_rota[i + 1] if i + 1 < len(na_rota) else None
            o["frente_m"] = frente["m"] - o["m"] if frente else None
            vel = ref["vel_media_ms"] if ref else None
            o["frente_min"] = round(o["frente_m"] / vel / 60, 1) if frente and vel else None
        gaps = [o["frente_min"] for o in na_rota if o.get("frente_min") is not None]
        media = sum(gaps) / len(gaps) if gaps else None
        for o in na_rota:
            g = o.get("frente_min")
            o["espacamento"] = (
                None if g is None or not media else "colado" if g < COLADO * media else "buraco" if g > BURACO * media else "regular"
            )
        sentidos.append({
            "sentido": s,
            "nome": "Ida" if s == 0 else "Volta",
            "regua": None if not ref else {
                "comprimento_m": round(ref["comprimento_m"]), "duracao_min": round(ref["duracao_s"] / 60),
                "pontos": [{"nome": p["nome"], "m": round(p["m"])} for p in ref["pontos"]],
                "trajeto": _simplificar(ref["linha"]), "base": ref["base"],
            },
            "onibus": sorted(onibus, key=lambda o: (o["m"] is None, o["m"] or 0)),
            "intervalo_medio_min": round(media, 1) if media else None,
            "maior_buraco_min": max(gaps) if gaps else None,
            "colados": sum(1 for o in na_rota if o.get("espacamento") == "colado"),
        })
    return {"linha": linha, "group_id": group_id, "atualizado": date.today().isoformat(), "sentidos": sentidos}
