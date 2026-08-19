"""
Schemas do módulo de linhas.

Espelham as colunas de `mova.buss_line` e derivadas. Os nomes seguem o banco em
vez de serem traduzidos: renomear no contrato criaria um dicionário a mais para
manter, e a equipe que opera o banco não reconheceria os campos.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ShiftStopResponse(BaseModel):
    """Parada de um turno, na ordem do percurso."""

    id: int
    buss_line_shift_id: Optional[int] = None
    poi_id: Optional[int] = None
    poi_name: Optional[str] = None
    #: Tipo do ponto. Identifica ponto de controle entre as paradas comuns.
    poi_type: Optional[int] = None
    ordem: Optional[int] = None
    #: Horário programado de passagem.
    schedule_time: Optional[time] = None

    model_config = {"from_attributes": True}


class ShiftResponse(BaseModel):
    """
    Turno da linha — o que o front chama de itinerário.

    `cerca_id` e `cerca_id_end` são as cercas eletrônicas de início e fim: é
    delas que sai o alarme de abertura de viagem fora do ponto de controle.
    """

    id: int
    tag: Optional[str] = None
    buss_line_id: int
    #: 0 = ida, 1 = volta.
    direction: Optional[int] = None
    status: Optional[int] = None
    hour: Optional[time] = None
    hour_end: Optional[time] = None
    cerca_id: Optional[int] = None
    cerca_id_end: Optional[int] = None
    #: Dias da semana em que o turno opera.
    weekday: Optional[list[int]] = None
    route_id: Optional[int] = None
    driver_id: Optional[int] = None
    unit_id: Optional[int] = None
    circular: Optional[bool] = None
    turn_trip: Optional[bool] = None
    temporary: Optional[bool] = None
    hour_work_initial: Optional[time] = None
    hour_work_final: Optional[time] = None
    stops: list[ShiftStopResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class BusLineResponse(BaseModel):
    """Linha, no formato da listagem."""

    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    buss_line_client_id: Optional[int] = None
    status: Optional[int] = None
    circular: Optional[bool] = None
    type_id: Optional[int] = None
    bls_category_id: Optional[int] = None
    km: Optional[Decimal] = None
    duration: Optional[timedelta] = None
    cost_center_id: Optional[int] = None
    #: Turnos ativos. Linha sem turno não é operável.
    shift_count: int = 0

    model_config = {"from_attributes": True}


class BusLineListResponse(BaseModel):
    items: list[BusLineResponse]
    total: int
    limit: int
    offset: int


class BusLineDetailResponse(BusLineResponse):
    shifts: list[ShiftResponse] = Field(default_factory=list)


class TripComplianceSummary(BaseModel):
    """Contadores do dia. `efficiency_pct` é realizadas sobre programadas."""

    scheduled: int
    executed: int
    ok: int
    late: int
    early: int
    not_executed: int
    in_progress: int
    waiting: int
    efficiency_pct: float


class TripComplianceItem(BaseModel):
    """
    Uma viagem, no par programado × realizado.

    `situation` já vem classificada pelo servidor para que a mesma regra de
    tolerância valha em qualquer cliente — front web, app ou integração.
    """

    id: int
    bus_line_shift_id: Optional[int] = None
    shift_tag: Optional[str] = None
    direction: Optional[int] = None
    scheduled_unit_id: Optional[int] = None
    executed_unit_id: Optional[int] = None
    scheduled_driver_id: Optional[int] = None
    executed_driver_id: Optional[int] = None
    scheduled_start: Optional[datetime] = None
    executed_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    executed_end: Optional[datetime] = None
    #: Diferença em minutos entre saída real e programada. Negativo = adiantada.
    start_deviation_min: Optional[float] = None
    approve_schedule: Optional[bool] = None
    trip_closure_status: Optional[bool] = None
    situation: str

    model_config = {"from_attributes": True}


class TripComplianceResponse(BaseModel):
    line_id: int
    operation_date: date
    summary: TripComplianceSummary
    trips: list[TripComplianceItem]
    #: Tolerâncias aplicadas, em minutos. Vão na resposta porque a
    #: classificação depende delas: sem saber o limite, o gestor não tem como
    #: contestar uma viagem marcada como atrasada.
    tolerance_early_min: int
    tolerance_late_min: int
    #: De onde vieram — cadastro da unidade ou padrão do setor. Declarado para
    #: o gestor saber se aquele número corresponde ao contrato dele.
    tolerance_source: str
