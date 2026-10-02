"""
Operação de linhas: Fretamento e Transporte urbano. Só leitura.

Duas fontes, porque os clientes operam de jeitos diferentes:

1. **Monitor de viagens** (fretamento — ex.: VTR): horários do dia
   (`buss_line_shift`) × viagens executadas (`con_status_buss_line`). Regras
   do Monitor de Viagens do sistema antigo (vault: Fretamento/tripmonitor):
   tolerância padrão 7 min depois / 5 min antes; não iniciada passado o
   horário + 10 min = atrasada. Correção deliberada do problema 4 do vault: o
   dia pesquisado é respeitado — viagem de dia passado que não aconteceu é
   "não realizada", não "não iniciada".

2. **Viagens produtivas** (urbano — ex.: Consórcio Fênix): viagens da
   telemetria (`con_telemetry`) com `trip_status = true`, linha
   (`line_number`), sentido (`trip_direction`: 0 ida, 1 volta) e número da
   viagem (`trip_number`). Regra do relatório "Viagens Produtivas"
   (`reporttripproduction` do plataforma_web): linha ligada a `buss_line`
   quando o nome é numérico e igual ao número da linha, no mesmo grupo.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # date.weekday(): 0 = segunda
#: Não iniciada até este tempo depois do horário ainda é "aguardando".
ATRASO_SEM_INICIO_MIN = 10


def _filtro(user, alias: str, group_id: Optional[int], subgroup_id: Optional[int]) -> tuple[str, dict]:
    grupos, subgrupos = escopo_do_usuario(user)
    sql, params = clausula_escopo(grupos, subgrupos, alias=alias)
    if group_id is not None:
        sql += f" AND {alias}.group_id = :f_group"
        params = {**params, "f_group": group_id}
    if subgroup_id is not None:
        sql += f" AND {alias}.subgroup_id = :f_sub"
        params = {**params, "f_sub": subgroup_id}
    return sql, params


def _min(t) -> Optional[int]:
    return None if t is None else t.hour * 60 + t.minute


def _hhmm(dt) -> Optional[str]:
    return None if dt is None else dt.strftime("%H:%M")


@router.get("/monitor")
async def monitor_viagens(
    dia: date = Query(...),
    group_id: Optional[int] = Query(None),
    subgroup_id: Optional[int] = Query(None),
    linha_id: Optional[int] = Query(None),
    tolerancia_antes: int = Query(5, ge=0, le=30),
    tolerancia_depois: int = Query(7, ge=0, le=30),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Programado × realizado do dia (Monitor de Viagens)."""
    esc, par = _filtro(current_user, "bl", group_id, subgroup_id)
    if linha_id is not None:
        esc += " AND bl.id = :linha"
        par["linha"] = linha_id
    nome_dia = DIAS[dia.weekday()]
    util = dia.weekday() < 5
    horarios = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.tag, s.direction, s.hour, s.hour_end, s.unit_id, s.driver_id,
                       bl.id AS linha_id, bl.name AS linha, bl.description AS linha_desc,
                       tu.label AS placa_prog, tu.label2 AS prefixo_prog
                FROM mova.buss_line_shift s
                JOIN mova.buss_line bl ON bl.id = s.buss_line_id
                LEFT JOIN mova.tracked_unit tu ON tu.id = s.unit_id
                WHERE s.status = 1 AND bl.status = 1 {esc}
                  AND (s.weekday IS NULL OR cardinality(s.weekday) = 0
                       OR :dia = ANY(s.weekday) OR (:util AND 'util' = ANY(s.weekday)))
                ORDER BY s.hour, bl.name
                """
            ),
            {**par, "dia": nome_dia, "util": util},
        )
    ).mappings().all()
    linhas_ids = sorted({h["linha_id"] for h in horarios})
    ini = datetime.combine(dia, datetime.min.time())
    executadas = (
        await db.execute(
            text(
                """
                SELECT c.id, c.buss_line_shift_id, c.buss_line_id, c.buss_line_name, c.buss_line_shift_tag,
                       c.buss_line_shift_direction, c.local_time_ini, c.local_time_end, c.status,
                       c.unit_id, c.unit_label, c.unit_label2, c.driver_id, c.driver_name,
                       c.passenger_qtd, c.odom_ini, c.odom_end, c.total_stops, c.bus_line_total_poi, c.max_passenger
                FROM mova.con_status_buss_line c
                WHERE c.local_time_ini >= :ini AND c.local_time_ini < :fim
                  AND c.buss_line_id = ANY(CAST(:linhas AS integer[]))
                ORDER BY c.local_time_ini
                """
            ),
            {"ini": ini, "fim": ini + timedelta(days=1), "linhas": linhas_ids or [-1]},
        )
    ).mappings().all()

    por_horario: dict[int, dict] = {}
    sobra = []
    for e in executadas:
        sid = e["buss_line_shift_id"]
        if sid and sid not in por_horario:
            por_horario[sid] = e
        else:
            sobra.append(e)

    agora = datetime.now()
    hoje = agora.date()
    viagens = []

    def comum(e) -> dict:
        km = None
        if e and e["odom_ini"] is not None and e["odom_end"] is not None:
            km = round(max(0, e["odom_end"] - e["odom_ini"]) / 1000, 1)
        pct = None
        if e and e["bus_line_total_poi"]:
            pct = min(100, int((e["total_stops"] or 0) * 100 / e["bus_line_total_poi"]))
        return {
            "partidaRealizada": _hhmm(e["local_time_ini"]) if e else None,
            "chegadaRealizada": _hhmm(e["local_time_end"]) if e else None,
            "veiculoRealizadoId": str(e["unit_id"]) if e and e["unit_id"] else None,
            "veiculoRealizado": (e["unit_label2"] or e["unit_label"]) if e else None,
            "placaRealizada": e["unit_label"] if e else None,
            "motoristaRealizado": e["driver_name"] if e else None,
            "passageiros": e["passenger_qtd"] if e else None,
            "lotacao": e["max_passenger"] if e else None,
            "kmRodado": km,
            "percursoPct": pct,
        }

    for h in horarios:
        e = por_horario.get(h["id"])
        prog_min = _min(h["hour"])
        if e is None:
            if dia < hoje:
                sit = "nao_realizada"
            elif dia > hoje or prog_min is None:
                sit = "aguardando"
            else:
                agora_min = agora.hour * 60 + agora.minute
                sit = "atrasada" if agora_min > prog_min + ATRASO_SEM_INICIO_MIN else "aguardando"
        else:
            ini_min = _min(e["local_time_ini"])
            desvio = None if prog_min is None else ini_min - prog_min
            if e["local_time_end"] is None and e["status"] == 1:
                sit = "em_andamento"
            elif desvio is None:
                sit = "ok"
            elif desvio > tolerancia_depois:
                sit = "atrasada"
            elif desvio < -tolerancia_antes:
                sit = "adiantada"
            else:
                sit = "ok"
        viagens.append(
            {
                "id": f"h{h['id']}",
                "programadaId": str(h["id"]),
                "linhaId": str(h["linha_id"]),
                "linha": h["linha"],
                "linhaDescricao": h["linha_desc"],
                "tabela": h["tag"],
                "sentido": "volta" if h["direction"] == 1 else "ida",
                "partidaProgramada": h["hour"].strftime("%H:%M") if h["hour"] else None,
                "chegadaProgramada": h["hour_end"].strftime("%H:%M") if h["hour_end"] else None,
                "veiculoProgramadoId": str(h["unit_id"]) if h["unit_id"] else None,
                "veiculoProgramado": h["prefixo_prog"] or h["placa_prog"],
                "situacao": sit,
                **comum(e),
            }
        )
    # Executadas sem horário correspondente: reforço (ou horário fora do dia).
    for e in sobra:
        viagens.append(
            {
                "id": f"e{e['id']}",
                "programadaId": None,
                "linhaId": str(e["buss_line_id"]),
                "linha": e["buss_line_name"],
                "linhaDescricao": None,
                "tabela": e["buss_line_shift_tag"],
                "sentido": "volta" if e["buss_line_shift_direction"] == 1 else "ida",
                "partidaProgramada": None,
                "chegadaProgramada": None,
                "veiculoProgramadoId": None,
                "veiculoProgramado": None,
                "situacao": "reforco",
                **comum(e),
            }
        )
    linhas = sorted({(h["linha_id"], h["linha"], h["linha_desc"]) for h in horarios}, key=lambda x: str(x[1]))
    return {
        "dia": dia.isoformat(),
        "fonte": "programacao",
        "tolerancias": {"antes": tolerancia_antes, "depois": tolerancia_depois, "sem_inicio": ATRASO_SEM_INICIO_MIN},
        "feriado_considerado": False,
        "linhas": [{"id": str(i), "codigo": n, "nome": d} for i, n, d in linhas],
        "viagens": viagens,
    }


@router.get("/produtivas")
async def viagens_produtivas(
    inicio: date = Query(...),
    fim: Optional[date] = Query(None),
    group_id: Optional[int] = Query(None),
    subgroup_id: Optional[int] = Query(None),
    linha: Optional[int] = Query(None, description="Número da linha (line_number)"),
    incluir_nao_produtivas: bool = Query(False),
    limite: int = Query(3000, ge=1, le=20000),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Viagens produtivas por linha e sentido, com o resumo por linha."""
    fim = fim or inicio
    if fim < inicio or (fim - inicio).days > 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Período de 1 a 7 dias")
    esc, par = _filtro(current_user, "tu", group_id, subgroup_id)
    ids = [r[0] for r in (await db.execute(text(f"SELECT tu.id FROM mova.tracked_unit tu WHERE tu.status = 1 {esc}"), par)).all()]
    vazio = {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "fonte": "produtivas", "por_linha": [], "viagens": [], "total": 0}
    if not ids:
        return vazio
    cond = "" if incluir_nao_produtivas else " AND ct.trip_status"
    p = {"ids": ids, "ini": datetime.combine(inicio, datetime.min.time()), "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time())}
    if linha is not None:
        cond += " AND ct.line_number = :linha"
        p["linha"] = linha
    linhas_sql = f"""
        SELECT ct.unit_id, tu.label, tu.label2, tu.group_id, ct.driver_id, ct.driver_name, ct.line_number,
               ct.trip_direction, ct.trip_number, ct.trip_status, ct.start_time, ct.end_time,
               ct.distance_traveled, ct.total_time, ct.fuel_used, ct.efficiency_kml, ct.max_speed, ct.avg_speed,
               ct.count_hard_brake, ct.count_hard_acel, ct.count_over_speed,
               ct.start_poi_name, ct.end_poi_name, ct.start_area_name, ct.end_area_name
        FROM mova.con_telemetry ct
        JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
        WHERE ct.unit_id = ANY(CAST(:ids AS integer[]))
          AND ct.start_time >= :ini AND ct.start_time < :fim
          AND ct.start_time < ct.end_time {cond}
        ORDER BY ct.start_time DESC
    """
    rows = (await db.execute(text(linhas_sql), p)).mappings().all()
    # Descrição da linha: buss_line com nome numérico igual ao número, no grupo.
    desc = {
        (r[0], int(r[1])): r[2]
        for r in (
            await db.execute(
                text(
                    """
                    SELECT bl.group_id, bl.name, bl.description FROM mova.buss_line bl
                    WHERE bl.status = 1 AND bl.name ~ '^[0-9]+$'
                      AND bl.group_id = ANY(CAST(:grupos AS integer[]))
                    """
                ),
                {"grupos": list({r["group_id"] for r in rows}) or [-1]},
            )
        ).all()
    }

    agg: dict = defaultdict(lambda: {"viagens": 0, "ida": 0, "volta": 0, "km": 0.0, "seg": 0.0, "litros": 0.0, "km_comb": 0.0, "veiculos": set(), "motoristas": set(), "freadas": 0, "aceleracoes": 0, "velocidade": 0})
    viagens = []
    for r in rows:
        km = float(r["distance_traveled"] or 0) / 1000
        if r["line_number"] is not None and r["trip_status"]:
            a = agg[(r["group_id"], r["line_number"])]
            a["viagens"] += 1
            a["ida" if r["trip_direction"] == 0 else "volta"] += 1
            a["km"] += km
            a["seg"] += float(r["total_time"] or 0)
            if (r["fuel_used"] or 0) > 0:
                a["litros"] += float(r["fuel_used"]) / 1000
                a["km_comb"] += km
            a["veiculos"].add(r["unit_id"])
            if r["driver_id"]:
                a["motoristas"].add(r["driver_id"])
            a["freadas"] += r["count_hard_brake"] or 0
            a["aceleracoes"] += r["count_hard_acel"] or 0
            a["velocidade"] += r["count_over_speed"] or 0
        if len(viagens) < limite:
            viagens.append(
                {
                    "linha": r["line_number"],
                    "linhaDescricao": desc.get((r["group_id"], r["line_number"])) if r["line_number"] is not None else None,
                    "sentido": None if r["trip_direction"] is None else ("ida" if r["trip_direction"] == 0 else "volta"),
                    "numero": r["trip_number"],
                    "produtiva": bool(r["trip_status"]),
                    "inicio": r["start_time"].isoformat(),
                    "fim": r["end_time"].isoformat() if r["end_time"] else None,
                    "minutos": round(float(r["total_time"] or 0) / 60, 1),
                    "km": round(km, 2),
                    "kml": float(r["efficiency_kml"]) if r["efficiency_kml"] else None,
                    "veiculo": r["label2"] or r["label"],
                    "placa": r["label"],
                    "unit_id": r["unit_id"],
                    "motorista": r["driver_name"],
                    "origem": r["start_poi_name"] or r["start_area_name"],
                    "destino": r["end_poi_name"] or r["end_area_name"],
                    "vel_max": r["max_speed"],
                    "eventos": (r["count_hard_brake"] or 0) + (r["count_hard_acel"] or 0) + (r["count_over_speed"] or 0),
                }
            )
    por_linha = sorted(
        (
            {
                "linha": ln,
                "descricao": desc.get((g, ln)),
                "viagens": a["viagens"],
                "ida": a["ida"],
                "volta": a["volta"],
                "km": round(a["km"], 1),
                "horas": round(a["seg"] / 3600, 1),
                "minutos_medio": round(a["seg"] / 60 / a["viagens"], 1) if a["viagens"] else None,
                "kml": round(a["km_comb"] / a["litros"], 2) if a["litros"] > 0 else None,
                "veiculos": len(a["veiculos"]),
                "motoristas": len(a["motoristas"]),
                "eventos": a["freadas"] + a["aceleracoes"] + a["velocidade"],
            }
            for (g, ln), a in agg.items()
        ),
        key=lambda x: -x["viagens"],
    )
    return {
        **vazio,
        "total": len(rows),
        "produtivas": sum(1 for r in rows if r["trip_status"]),
        # Veículos e motoristas distintos do dia: somar os de cada linha
        # contaria duas vezes quem roda em mais de uma.
        "veiculos": len({r["unit_id"] for r in rows}),
        "motoristas": len({r["driver_id"] for r in rows if r["driver_id"]}),
        "por_linha": por_linha,
        "viagens": viagens,
        "truncado": len(rows) > limite,
    }
