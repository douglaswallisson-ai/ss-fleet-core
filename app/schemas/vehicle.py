"""Pydantic schemas for Vehicle model mapped to mova.tracked_unit."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class VehicleBase(BaseModel):
    """Base vehicle schema - uses tracked_unit fields."""
    label: str = Field(..., min_length=1, max_length=255, description="Vehicle plate/name")
    label2: Optional[str] = Field(None, max_length=255, description="Secondary label")
    model: Optional[str] = Field(None, max_length=255, description="Vehicle model")
    unit_category_id: int = Field(..., description="Vehicle category ID")
    unit_type_id: Optional[int] = Field(None, description="Vehicle type ID")
    vehicle_model_id: Optional[int] = Field(None, description="Vehicle model ID")
    timezone: int = Field(default=-3, description="Timezone offset")
    dst: bool = Field(default=True, description="Daylight saving time")
    initial_odometer: int = Field(default=0, ge=0, description="Initial odometer reading (meters)")
    initial_horimeter: Optional[int] = Field(None, ge=0, description="Initial horimeter reading (minutes)")
    max_speed: Optional[int] = Field(None, ge=0, le=300, description="Maximum speed limit (km/h)")
    obs: Optional[str] = Field(None, description="Observations/notes")


class VehicleCreate(VehicleBase):
    """
    Schema for creating vehicle.

    User must provide group_id and subgroup_id, and must have access to them.
    Note: account_id, user_add, date_add are set automatically by the system.
    """
    group_id: int = Field(..., description="Group ID (user must have access)")
    subgroup_id: int = Field(..., description="Subgroup ID (user must have access)")


class VehicleUpdate(BaseModel):
    """Schema for updating vehicle."""
    label: Optional[str] = Field(None, min_length=1, max_length=255)
    label2: Optional[str] = Field(None, max_length=255)
    model: Optional[str] = Field(None, max_length=255)
    unit_category_id: Optional[int] = None
    unit_type_id: Optional[int] = None
    vehicle_model_id: Optional[int] = None
    timezone: Optional[int] = None
    dst: Optional[bool] = None
    initial_odometer: Optional[int] = Field(None, ge=0)
    initial_horimeter: Optional[int] = Field(None, ge=0)
    max_speed: Optional[int] = Field(None, ge=0, le=300)
    obs: Optional[str] = None
    status: Optional[int] = Field(None, ge=0, le=1, description="0=inactive, 1=active")
    group_id: Optional[int] = Field(None, description="Group ID (user must have access)")
    subgroup_id: Optional[int] = Field(None, description="Subgroup ID (user must have access)")


class EstadoAtual(BaseModel):
    """
    Última leitura do equipamento, de ``mova.dev_status``.

    Separado do cadastro de propósito: ``tracked_unit.initial_odometer`` é o
    valor de quando o equipamento foi instalado — está preenchido em poucos
    veículos e não avança. Quem precisa saber a quilometragem de hoje precisa
    desta tabela.

    ``odom_quality_flag`` vem junto e não é detalhe: um terço dos veículos
    ativos tem odômetro corrigido por alguma regra — ``regressive_replaced``
    quando o contador de 32 bits estoura e a leitura anda para trás,
    ``outlier_replaced`` em salto implausível. Entregar o valor sem a marca
    faz o consumidor tratar número tratado como leitura direta, e reimplementar
    a mesma trava por conta própria.
    """

    #: Odômetro em **metros**, como a tabela guarda.
    odom: Optional[int] = None
    odom_total: Optional[int] = None
    #: `ok`, `regressive_replaced`, `outlier_replaced`, `frozen_business_rule`,
    #: `odom_is_zero_replaced_by_last_canonical`, `magic_value_replaced`,
    #: `null_replaced`, ou nulo quando nenhuma regra foi aplicada.
    odom_quality_flag: Optional[str] = None
    #: Horímetro acumulado.
    hourmeter_total: Optional[float] = None
    #: Consumo médio que o próprio veículo informa pelo barramento. Serve de
    #: contraprova ao km/l calculado da telemetria.
    can_avg_fuel_economy_kmpl: Optional[float] = None
    can_total_odometer: Optional[int] = None
    can_engine_hourmeter: Optional[float] = None
    #: Quando esta leitura chegou. Sem ela, não há como saber se o odômetro é
    #: de hoje ou de três meses atrás.
    local_time: Optional[datetime] = None
    #: Velocidade e ignição da última leitura — o que define a situação
    #: (em rota, parado, sem sinal) na lista de veículos.
    speed: Optional[float] = None
    ignition: Optional[bool] = None

    model_config = {"from_attributes": True}


class VehicleResponse(BaseModel):
    """Schema for vehicle response - includes all relevant fields."""
    id: int
    label: str
    label2: Optional[str]
    model: Optional[str]
    group_id: int
    subgroup_id: int
    account_id: int
    unit_category_id: int
    unit_type_id: Optional[int]
    vehicle_model_id: Optional[int]
    status: int
    timezone: int
    dst: bool
    initial_odometer: int
    initial_horimeter: Optional[int]
    max_speed: Optional[int]
    driver_id: Optional[int]
    obs: Optional[str]
    date_add: Optional[datetime]
    date_modif: Optional[datetime]

    # Virtual properties for backward compatibility
    @property
    def plate(self) -> str:
        """Alias for label field."""
        return self.label

    @property
    def is_active(self) -> bool:
        """Convert status to boolean."""
        return self.status == 1

    @property
    def created_at(self) -> Optional[datetime]:
        """Alias for date_add."""
        return self.date_add

    @property
    def updated_at(self) -> Optional[datetime]:
        """Alias for date_modif."""
        return self.date_modif

    @property
    def description(self) -> Optional[str]:
        """Alias for obs."""
        return self.obs

    model_config = {"from_attributes": True}
    #: Custo por quilômetro cadastrado para o veículo. É insumo direto do CPK e
    #: estava no banco sem nenhum endpoint expondo.
    cost_km: Optional[float] = None
    #: Ordem de serviço vinculada, quando o veículo está em manutenção.
    #: Inteiro, como a coluna em `tracked_unit` e o modelo. Declarado como
    #: texto, a lista inteira caía com erro de validação no primeiro veículo
    #: com `os_num = 0` — e grupo nenhum aparecia.
    os_num: Optional[int] = None
    os_id: Optional[int] = None

    # ------------------------------------------------------------------ #
    # Estado atual, de `mova.dev_status`                                   #
    # ------------------------------------------------------------------ #
    #
    # `initial_odometer` acima é o valor de quando o equipamento foi
    # instalado, e está preenchido em poucos veículos. Quem precisa da
    # quilometragem de hoje — manutenção preventiva, custo por km — precisa
    # destes campos.

    #: Odômetro atual, em metros.
    odom: Optional[int] = None

    #: Como a leitura foi tratada antes de virar `odom`.
    #:
    #: Um terço da frota tem o valor corrigido por alguma regra:
    #: `regressive_replaced` é estouro de contador de 32 bits,
    #: `outlier_replaced` é salto implausível, `magic_value_replaced` é
    #: valor sentinela do equipamento. Quem consome `odom` sem olhar esta
    #: flag está lendo valor tratado como se fosse leitura direta.
    odom_quality_flag: Optional[str] = None

    #: Horímetro acumulado, em horas. É o gatilho por horas da preventiva.
    hourmeter_total: Optional[float] = None

    #: Consumo médio informado pelo próprio veículo, quando o barramento
    #: publica. Não substitui o cálculo por viagem — é outra medição, feita
    #: pela central do veículo, e as duas podem divergir.
    can_avg_fuel_economy_kmpl: Optional[float] = None

    #: Instante da última leitura. Sem ele não há como saber se o odômetro
    #: é de hoje ou de três meses atrás.
    dev_status_time: Optional[datetime] = None
    #: Ano de fabricação. Existe no cadastro e não era exposto.
    vehicle_year: Optional[int] = None
    #: Última leitura do equipamento. Nulo quando o veículo nunca transmitiu.
    estado_atual: Optional[EstadoAtual] = None


class VehicleCursorResponse(BaseModel):
    """
    DS-1379: envelope de paginação por cursor para GET /vehicles, no mesmo
    formato já usado pelos endpoints de relatório (data/next_cursor/
    has_more/total_returned).
    """
    data: list[VehicleResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int