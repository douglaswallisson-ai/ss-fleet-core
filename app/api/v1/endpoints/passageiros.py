"""
Contagem de passageiros (embarques por cartão RFID).

Fonte e regras do sistema atual (plataforma_web, lidas em 04/10/2026):
- Embarque e Desembarque (reportpassengerboarding): eventos 273 (embarque) e
  274 (desembarque) do equipamento; passageiro = cartão (`passenger.cod`) do
  mesmo grupo; "linha permitida" = o passageiro está na lista da viagem
  (`buss_line_shift_passenger`, status 1).
- Taxa de Frequência (reporttripoccupancy): por passageiro × viagem programada,
  embarques ÷ viagens realizadas no período. O sistema atual calcula numa API
  externa; aqui o cálculo é refeito com as mesmas peças.
  SUPOSIÇÃO: "viagens" = viagens fechadas em `con_status_buss_line` com a mesma
  tabela (buss_line_shift_id) no período. Confirmar comparando um passageiro com
  o relatório antigo.

O embarque gravado em `passenger_board` é o 273 já ligado à viagem; o total por
viagem bate com `con_status_buss_line.passenger_qtd` (conferido na VTR: 8.179 em
7 dias nos dois). Desembarque (274) quase não chega (246 em 3 dias na VTR), então
aparece só como contagem, sem fluxo "a bordo".
"""

import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

MAX_DIAS = 31
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_S = 300


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


async def _ler(db, sql: str, p: dict):
    return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


def _f(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


def _limpo(rows):
    return [{k: _f(v) for k, v in r.items()} for r in rows]


@router.get("/contagem")
async def contagem(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                   user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    if fim < inicio:
        raise HTTPException(422, "O fim do período deve ser depois do início.")
    if (fim - inicio).days + 1 > MAX_DIAS:
        raise HTTPException(422, f"Período de no máximo {MAX_DIAS} dias.")
    k = (group_id, inicio, fim)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]

    p = {"g": group_id, "ini": datetime.combine(inicio, datetime.min.time()),
         "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time())}
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '60s'"))
        # Embarques do período, com o passageiro e se ele está na lista da viagem.
        emb_cte = """WITH un AS (SELECT id, label FROM mova.tracked_unit WHERE group_id = :g),
            e AS (SELECT pb.id, pb.local_time, pb.unit_id, pb.passenger_id, pb.line_shift_id, pb.poi_id, pb.rfid,
                         EXISTS (SELECT 1 FROM mova.buss_line_shift_passenger b WHERE b.status = 1
                                 AND b.passenger_id = pb.passenger_id AND b.buss_line_shift_id = pb.line_shift_id) AS na_lista
                  FROM mova.passenger_board pb JOIN un ON un.id = pb.unit_id
                  WHERE pb.local_time >= :ini AND pb.local_time < :fim)"""

        resumo = (await _ler(db, emb_cte + """
            SELECT count(*) AS embarques, count(DISTINCT passenger_id) AS passageiros,
                   count(*) FILTER (WHERE passenger_id IS NULL) AS sem_cadastro,
                   count(*) FILTER (WHERE passenger_id IS NOT NULL AND NOT na_lista) AS fora_da_lista,
                   count(DISTINCT local_time::date) AS dias
            FROM e""", p))[0]

        por_dia = await _ler(db, emb_cte + """
            SELECT local_time::date AS dia, count(*) AS embarques, count(DISTINCT passenger_id) AS passageiros
            FROM e GROUP BY 1 ORDER BY 1""", p)

        por_hora = await _ler(db, emb_cte + """
            SELECT extract(hour FROM local_time)::int AS hora, count(*) AS embarques FROM e GROUP BY 1 ORDER BY 1""", p)

        por_ponto = await _ler(db, emb_cte + """
            SELECT e.poi_id, coalesce(po.name, 'Fora de ponto cadastrado') AS ponto, count(*) AS embarques,
                   count(DISTINCT e.passenger_id) AS passageiros
            FROM e LEFT JOIN mova.poi po ON po.id = e.poi_id
            GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30""", p)

        desembarques = (await _ler(db, """
            SELECT count(*) AS n FROM mova.dev_status_30 d
            WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g)
              AND d.tracker_event_id = 274 AND d.local_time >= :ini AND d.local_time < :fim""", p))[0]["n"]

        # Viagens realizadas: o total de embarques vem do próprio fechamento da viagem.
        viagens = await _ler(db, """
            SELECT c.id, c.local_time_ini AS inicio, c.local_time_end AS fim, c.unit_label AS veiculo,
                   c.buss_line_name AS linha, c.buss_line_shift_tag AS tabela, c.buss_line_shift_id AS tabela_id,
                   c.buss_line_cost_center_name AS centro_custo, c.driver_name AS motorista,
                   CASE c.buss_line_shift_direction WHEN 1 THEN 'Ida' WHEN 2 THEN 'Volta' ELSE '—' END AS sentido,
                   coalesce(c.passenger_qtd, 0) AS embarques,
                   nullif(greatest(coalesce(c.max_passenger, 0), coalesce(c.unit_qtd_passenger, 0), coalesce(tu.qtd_passenger, 0)), 0) AS capacidade,
                   (SELECT count(*) FROM mova.buss_line_shift_passenger b WHERE b.status = 1 AND b.buss_line_shift_id = c.buss_line_shift_id) AS na_lista
            FROM mova.con_status_buss_line c LEFT JOIN mova.tracked_unit tu ON tu.id = c.unit_id
            WHERE c.group_id = :g AND c.local_time_ini >= :ini AND c.local_time_ini < :fim AND c.buss_line_shift_id IS NOT NULL
            ORDER BY c.local_time_ini DESC""", p)

        # Taxa de frequência: quem está na lista de cada tabela × viagens que ela fez.
        frequencia = await _ler(db, emb_cte + """,
            vt AS (SELECT buss_line_shift_id AS tid, count(*) AS viagens, max(buss_line_name) AS linha, max(buss_line_shift_tag) AS tabela
                   FROM mova.con_status_buss_line
                   WHERE group_id = :g AND local_time_ini >= :ini AND local_time_ini < :fim AND buss_line_shift_id IS NOT NULL
                   GROUP BY 1),
            ep AS (SELECT passenger_id, line_shift_id AS tid, count(*) AS embarques FROM e WHERE passenger_id IS NOT NULL GROUP BY 1, 2)
            SELECT ps.id AS passageiro_id, ps.name AS passageiro, ps.matricula, cc.name AS centro_custo,
                   vt.linha, vt.tabela, vt.viagens, coalesce(ep.embarques, 0) AS embarques
            FROM vt JOIN mova.buss_line_shift_passenger b ON b.buss_line_shift_id = vt.tid AND b.status = 1
            JOIN mova.passenger ps ON ps.id = b.passenger_id AND ps.status = 1
            LEFT JOIN mova.cost_center cc ON cc.id = ps.cost_center_id
            LEFT JOIN ep ON ep.passenger_id = ps.id AND ep.tid = vt.tid""", p)

        cartoes = await _ler(db, emb_cte + """
            SELECT e.rfid AS cartao, count(*) AS leituras, max(e.local_time) AS ultima, max(un.label) AS veiculo
            FROM e JOIN un ON un.id = e.unit_id WHERE e.passenger_id IS NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 50""", p)

    viagens = _limpo(viagens)
    for v in viagens:
        v["ocupacao"] = round(100 * v["embarques"] / v["capacidade"]) if v.get("capacidade") else None
    frequencia = _limpo(frequencia)
    for f in frequencia:
        f["taxa"] = round(100 * min(f["embarques"], f["viagens"]) / f["viagens"]) if f["viagens"] else None
    frequencia.sort(key=lambda f: (f["taxa"] if f["taxa"] is not None else 999, f["passageiro"] or ""))
    ausentes = sum(1 for f in frequencia if f["embarques"] == 0)

    r = {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "resumo": {**_limpo([resumo])[0], "desembarques": desembarques, "viagens": len(viagens),
                   "media_por_viagem": round(sum(v["embarques"] for v in viagens) / len(viagens), 1) if viagens else None,
                   "na_lista_sem_embarque": ausentes},
        "por_dia": _limpo(por_dia), "por_hora": _limpo(por_hora), "por_ponto": _limpo(por_ponto),
        "viagens": viagens[:500], "viagens_total": len(viagens),
        "frequencia": frequencia[:10000], "frequencia_total": len(frequencia),
        "cartoes_sem_cadastro": _limpo(cartoes),
    }
    _CACHE[k] = (time.time(), r)
    return r
