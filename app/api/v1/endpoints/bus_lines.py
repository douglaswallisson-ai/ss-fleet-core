"""
Bus lines endpoints.

Expõe o módulo de linhas que já existe no schema `mova` e nunca teve API:
`buss_line`, `buss_line_shift`, `buss_line_shift_stops` e `busline_schedule`.

São quase 10 milhões de paradas registradas e 1,5 milhão de programações — os
dados estão lá desde sempre, sendo lidos direto pelo front antigo. Estes
endpoints trazem esse acesso para a mesma camada de controle de acesso,
paginação e auditoria dos demais recursos.

A convenção `_exec` percorre todo o módulo: `unit_id` é o programado,
`unit_id_exec` o realizado. É a partir desse par que se mede cumprimento de
programação.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.bus_lines import (
    BusLineDetailResponse,
    BusLineListResponse,
    BusLineResponse,
    ShiftResponse,
    ShiftStopResponse,
    TripComplianceResponse,
    TripComplianceSummary,
)

logger = get_logger(__name__)
router = APIRouter()


async def _accessible_groups(db: AsyncSession, user: User) -> list[int]:
    """
    Grupos que o usuário enxerga.

    Reaproveita o mesmo modelo dos outros recursos: usuário master vê tudo, os
    demais só os grupos concedidos em `user_group_access`. Sem este filtro, um
    operador de uma empresa leria as linhas de outra.
    """
    if user.master:
        return []

    result = await db.execute(
        text("SELECT group_id FROM mova.user_group_access WHERE user_id = :uid"),
        {"uid": user.id},
    )
    return [row[0] for row in result.fetchall()]


def _group_filter(groups: list[int], alias: str = "bl") -> tuple[str, dict]:
    """Cláusula de escopo. Lista vazia significa acesso irrestrito (master)."""
    if not groups:
        return "", {}
    return f" AND {alias}.group_id = ANY(:groups)", {"groups": groups}


@router.get("/", response_model=BusLineListResponse)
async def list_bus_lines(
    search: Optional[str] = Query(None, description="Busca por nome ou descrição"),
    group_id: Optional[int] = Query(None),
    only_active: bool = Query(True, description="Somente linhas com status ativo"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lista as linhas cadastradas.

    Traz a contagem de turnos junto porque linha sem turno não é operável — e
    essa é a primeira coisa que o gestor precisa enxergar ao abrir a lista.
    """
    groups = await _accessible_groups(db, user)
    scope_sql, scope_params = _group_filter(groups)

    params: dict = {"limit": limit, "offset": offset, **scope_params}
    where = ["1=1"]

    if only_active:
        where.append("bl.status = 1")
    if group_id is not None:
        where.append("bl.group_id = :group_id")
        params["group_id"] = group_id
    if search:
        where.append("(bl.name ILIKE :search OR bl.description ILIKE :search)")
        params["search"] = f"%{search}%"

    sql = f"""
        SELECT
            bl.id, bl.name, bl.description, bl.group_id, bl.subgroup_id,
            bl.buss_line_client_id, bl.status, bl.circular, bl.type_id,
            bl.bls_category_id, bl.km, bl.duration, bl.cost_center_id,
            COUNT(DISTINCT s.id) AS shift_count,
            COUNT(*) OVER () AS total_count
        FROM mova.buss_line bl
        LEFT JOIN mova.buss_line_shift s
               ON s.buss_line_id = bl.id AND s.status = 1
        WHERE {' AND '.join(where)}{scope_sql}
        GROUP BY bl.id
        ORDER BY bl.name
        LIMIT :limit OFFSET :offset
    """

    result = await db.execute(text(sql), params)
    rows = result.mappings().all()
    total = rows[0]["total_count"] if rows else 0

    logger.info("bus_lines_listed", user_id=user.id, returned=len(rows), total=total)

    return BusLineListResponse(
        items=[BusLineResponse(**{k: v for k, v in r.items() if k != "total_count"}) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{line_id}", response_model=BusLineDetailResponse)
async def get_bus_line(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Detalhe da linha, com turnos e as paradas de cada turno.

    Devolve tudo numa chamada só porque a tela de cadastro precisa do conjunto
    inteiro para renderizar — pedir turno e depois parada por parada geraria
    dezenas de requisições para montar uma linha.
    """
    groups = await _accessible_groups(db, user)
    scope_sql, scope_params = _group_filter(groups)

    line = (
        await db.execute(
            text(
                f"""
                SELECT bl.id, bl.name, bl.description, bl.group_id, bl.subgroup_id,
                       bl.buss_line_client_id, bl.status, bl.circular, bl.type_id,
                       bl.bls_category_id, bl.km, bl.duration, bl.cost_center_id
                FROM mova.buss_line bl
                WHERE bl.id = :id{scope_sql}
                """
            ),
            {"id": line_id, **scope_params},
        )
    ).mappings().first()

    if not line:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linha não encontrada")

    shifts = (
        await db.execute(
            text(
                """
                SELECT s.id, s.tag, s.buss_line_id, s.direction, s.status,
                       s.hour, s.hour_end, s.cerca_id, s.cerca_id_end,
                       s.weekday, s.route_id, s.driver_id, s.unit_id,
                       s.circular, s.turn_trip, s.temporary,
                       s.hour_work_initial, s.hour_work_final
                FROM mova.buss_line_shift s
                WHERE s.buss_line_id = :id AND s.status = 1
                ORDER BY s.direction, s.hour
                """
            ),
            {"id": line_id},
        )
    ).mappings().all()

    stops = (
        await db.execute(
            text(
                """
                SELECT st.id, st.buss_line_shift_id, st.poi_id, st.poi_name,
                       st.poi_type, st.ordem, st.schedule_time
                FROM mova.buss_line_shift_stops st
                WHERE st.buss_line_id = :id
                  AND st.buss_line_shift_id IS NOT NULL
                GROUP BY st.id, st.buss_line_shift_id, st.poi_id, st.poi_name,
                         st.poi_type, st.ordem, st.schedule_time
                ORDER BY st.buss_line_shift_id, st.ordem
                """
            ),
            {"id": line_id},
        )
    ).mappings().all()

    by_shift: dict[int, list] = {}
    for s in stops:
        by_shift.setdefault(s["buss_line_shift_id"], []).append(ShiftStopResponse(**s))

    return BusLineDetailResponse(
        **line,
        shifts=[
            ShiftResponse(**s, stops=by_shift.get(s["id"], []))
            for s in shifts
        ],
    )


@router.get("/{line_id}/compliance", response_model=TripComplianceResponse)
async def get_trip_compliance(
    line_id: int,
    operation_date: date = Query(..., description="Data de operação (AAAA-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Programado × realizado do dia.

    É o indicador que o poder concedente fiscaliza: quantas viagens foram
    programadas, quantas saíram, e com qual desvio. O par vem da própria
    `busline_schedule`, onde `datetime_ini` é o previsto e `datetime_ini_exec`
    o que aconteceu.

    A classificação segue a tolerância usual de contrato: até 3 minutos de
    adiantamento e 5 de atraso conta como cumprida no horário. Viagem sem
    execução cujo horário já passou é **não realizada** — e é justamente esse
    caso que gera desconto em medição.
    """
    groups = await _accessible_groups(db, user)
    scope_sql, scope_params = _group_filter(groups, alias="sch")

    sql = f"""
        SELECT
            sch.id,
            sch.bus_line_shift_id,
            s.tag                AS shift_tag,
            s.direction,
            sch.unit_id          AS scheduled_unit_id,
            sch.unit_id_exec     AS executed_unit_id,
            sch.driver_id        AS scheduled_driver_id,
            sch.driver_id_exec   AS executed_driver_id,
            sch.datetime_ini     AS scheduled_start,
            sch.datetime_ini_exec AS executed_start,
            sch.datetime_end     AS scheduled_end,
            sch.datetime_end_exec AS executed_end,
            sch.approve_schedule,
            sch.trip_closure_status,
            EXTRACT(EPOCH FROM (sch.datetime_ini_exec - sch.datetime_ini)) / 60
                AS start_deviation_min
        FROM mova.busline_schedule sch
        JOIN mova.buss_line_shift s ON s.id = sch.bus_line_shift_id
        WHERE s.buss_line_id = :line_id
          AND sch.datetime_ini >= :day_start
          AND sch.datetime_ini <  :day_end
          AND sch.status = 1{scope_sql}
        ORDER BY sch.datetime_ini
    """

    day_start = datetime.combine(operation_date, datetime.min.time())
    day_end = datetime.combine(operation_date, datetime.max.time())

    rows = (
        await db.execute(
            text(sql),
            {"line_id": line_id, "day_start": day_start, "day_end": day_end, **scope_params},
        )
    ).mappings().all()

    TOLERANCE_EARLY = -3
    TOLERANCE_LATE = 5
    now = datetime.now()

    trips = []
    for r in rows:
        deviation = r["start_deviation_min"]

        if r["executed_start"] is None:
            situation = "not_executed" if r["scheduled_start"] < now else "waiting"
        elif r["executed_end"] is None:
            situation = "in_progress"
        elif deviation is None:
            situation = "ok"
        elif deviation < TOLERANCE_EARLY:
            situation = "early"
        elif deviation > TOLERANCE_LATE:
            situation = "late"
        else:
            situation = "ok"

        trips.append({**dict(r), "situation": situation})

    scheduled = len(trips)
    executed = sum(1 for t in trips if t["executed_start"] is not None)

    summary = TripComplianceSummary(
        scheduled=scheduled,
        executed=executed,
        ok=sum(1 for t in trips if t["situation"] == "ok"),
        late=sum(1 for t in trips if t["situation"] == "late"),
        early=sum(1 for t in trips if t["situation"] == "early"),
        not_executed=sum(1 for t in trips if t["situation"] == "not_executed"),
        in_progress=sum(1 for t in trips if t["situation"] == "in_progress"),
        waiting=sum(1 for t in trips if t["situation"] == "waiting"),
        efficiency_pct=round(executed / scheduled * 100, 1) if scheduled else 0.0,
    )

    logger.info(
        "trip_compliance_queried",
        user_id=user.id, line_id=line_id, date=str(operation_date),
        scheduled=scheduled, executed=executed,
    )

    return TripComplianceResponse(
        line_id=line_id,
        operation_date=operation_date,
        summary=summary,
        trips=trips,
    )


@router.get("/{line_id}/shifts", response_model=list[ShiftResponse])
async def list_shifts(
    line_id: int,
    weekday: Optional[int] = Query(None, ge=0, le=6, description="0 = domingo"),
    direction: Optional[int] = Query(None, ge=0, le=1, description="0 = ida, 1 = volta"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Turnos da linha — o que a operação chama de escala.

    Filtrar por dia da semana é o uso mais comum: a escala de sábado é outra, e
    trazer a semana inteira obrigaria o front a filtrar um volume que o banco
    descarta com índice.

    `weekday` é um array na tabela, então a comparação usa `ANY` — um turno
    pode valer para vários dias.
    """
    groups = await _accessible_groups(db, user)
    scope_sql, scope_params = _group_filter(groups)

    # Confere o acesso pela linha antes de listar os turnos: sem isto, saber o
    # id de uma linha de outra empresa bastaria para ler a escala dela.
    linha = (
        await db.execute(
            text(f"SELECT id FROM mova.buss_line bl WHERE bl.id = :id{scope_sql}"),
            {"id": line_id, **scope_params},
        )
    ).first()
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linha não encontrada")

    where = ["s.buss_line_id = :id", "s.status = 1"]
    params: dict = {"id": line_id}

    if weekday is not None:
        where.append(":weekday = ANY(s.weekday)")
        params["weekday"] = weekday
    if direction is not None:
        where.append("s.direction = :direction")
        params["direction"] = direction

    rows = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.tag, s.buss_line_id, s.direction, s.status,
                       s.hour, s.hour_end, s.cerca_id, s.cerca_id_end,
                       s.weekday, s.route_id, s.driver_id, s.unit_id,
                       s.circular, s.turn_trip, s.temporary,
                       s.hour_work_initial, s.hour_work_final
                FROM mova.buss_line_shift s
                WHERE {' AND '.join(where)}
                ORDER BY s.direction, s.hour
                """
            ),
            params,
        )
    ).mappings().all()

    return [ShiftResponse(**r, stops=[]) for r in rows]
