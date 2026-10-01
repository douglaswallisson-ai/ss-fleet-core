"""
Vehicle endpoints.
CRUD operations for vehicle management.
"""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.core.cursor_pagination import CompositeCursor, apply_keyset_pagination, build_next_cursor
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.vehicle import Vehicle
from app.models.tracked_unit_device import TrackedUnitDevice
from app.models.vcms_unit_device import VcmsUnitDevice
from app.schemas.vehicle import VehicleCreate, VehicleUpdate, VehicleResponse, VehicleCursorResponse

router = APIRouter()

# DS-1385 (Épico DS-1342): saneamento preventivo de data fora do intervalo.
#
# Mesmo risco já corrigido em drivers.py (achado #9 do Épico 3): valores de
# data/hora fora do intervalo que o Python consegue representar (ano 1-9999)
# fazem o asyncpg estourar (OverflowError/ValueError) ao decodificar o valor
# BINÁRIO de volta para datetime - a exceção acontece na camada do driver do
# banco, ANTES do Pydantic/response model, então nenhuma validação em nível
# de schema alcança isso.
#
# Diferença para drivers.py: lá as colunas de risco são do tipo DATE (typed
# por humano - cnh_validate, admission, etc.); aqui são TIMESTAMP
# (date_add/date_modif/date_removed), preenchidas automaticamente pelo
# sistema (datetime.now()), não digitadas por humano. Não há evidência de
# problema real até agora - aplicado de forma preventiva, com o mesmo custo
# relativamente baixo já pago em drivers.py.
#
# Mesma solução: converter essas colunas pra TEXTO já na consulta (o cast
# pra texto nunca quebra, independente do tamanho do ano) e interpretar a
# data/hora manualmente em Python, com try/except.
#
# Escopo desta correção: apenas list_vehicles (endpoint de listagem), assim
# como o fix original em drivers.py cobriu apenas list_drivers - get/create/
# update/delete continuam decodificando as colunas do jeito antigo (mesmo
# estado de exposição dos endpoints equivalentes de driver). Ver ressalva
# na documentação da tarefa (DS-1385) sobre esse gap remanescente.
_RISKY_DATETIME_COLUMNS = ('date_add', 'date_modif', 'date_removed')


def _safe_datetime_column(column):
    """SELECT dessa coluna TIMESTAMP já convertida pra texto (ISO), pra
    nunca quebrar o decodificador binário do asyncpg."""
    return func.to_char(column, 'YYYY-MM-DD HH24:MI:SS').label(column.key)


def _parse_safe_datetime(value):
    """Convertido de volta pra datetime em Python, com fallback pra None se
    o valor estiver fora do intervalo suportado (ano > 9999 ou <= 0)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError):
        return None


@router.get("/", response_model=VehicleCursorResponse)
async def list_vehicles(
    cursor: Optional[str] = Query(None, description="Cursor da página anterior (base64, retornado em next_cursor). Omitir na primeira requisição."),
    limit: int = Query(100, ge=1, le=5000),
    group_id: int = Query(None, description="Filter by group ID"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """
    List all vehicles accessible by current user based on group and subgroup permissions.

    Access logic:
    - User sees vehicles where group_id matches AND (subgroup_id matches OR subgroup_id IS NULL)

    Paginação real por cursor (DS-1379 - substitui o antigo skip/limit por
    OFFSET, mesmo padrão dos endpoints de relatório):
    - Primeira página: não envie `cursor`.
    - Próximas páginas: envie o `next_cursor` retornado na resposta anterior.
    - `has_more=false` indica que não há mais páginas.
    - Custo não cresce com a profundidade da página - importante pra
      grupos com muitos veículos (o limite de 5000 era paliativo: se um
      grupo passar disso um dia, o problema original volta).

    Optional group_id filter (mirrors drivers.py) - sem isso, listas de
    contas com acesso a muitos grupos ficavam limitadas aos veículos mais
    recentes GLOBALMENTE, deixando de fora veículos mais antigos de um
    grupo específico (aparecem como "ID X" nos gráficos por falta de
    correspondência).

    Saneamento preventivo (DS-1385): as colunas date_add/date_modif/
    date_removed são lidas como texto e parseadas manualmente em Python,
    pelo mesmo motivo do fix em drivers.py (achado #9, Épico 3) - evita que
    um valor de data fora do intervalo suportado (ano > 9999 ou <= 0) quebre
    a decodificação binária do asyncpg antes mesmo de chegar no Pydantic.

    BREAKING CHANGE: o parâmetro `skip` foi removido e a resposta deixou
    de ser uma lista simples (`[...]`) para ser um envelope
    (`{data, next_cursor, has_more, total_returned}`). Ver tarefa de
    frontend "Consumir paginação real de /drivers e /vehicles".
    """
    # Filter vehicles by user's accessible groups and subgroups
    if not current_user.group_access:
        # User has no group access - return empty
        return VehicleCursorResponse(data=[], next_cursor=None, has_more=False, total_returned=0)

    # Decodifica o cursor da página anterior, se enviado
    decoded_cursor = None
    if cursor:
        try:
            decoded_cursor = CompositeCursor.from_string(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format. Expected base64-encoded composite cursor."
            )

    # Build filter considering group_id and subgroup_id with NULL logic
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Seleciona todas as colunas do Vehicle, mas com as colunas de
    # data/hora de risco convertidas pra texto na consulta (ver
    # _safe_datetime_column acima) - a paginação por cursor abaixo continua
    # comparando contra a coluna original (sem conversão), já que a
    # comparação acontece dentro do Postgres e nunca precisa decodificar o
    # valor num objeto Python (mesmo raciocínio já usado no filtro de
    # cnh_expired em drivers.py).
    select_columns = [
        _safe_datetime_column(col) if col.key in _RISKY_DATETIME_COLUMNS else col
        for col in Vehicle.__table__.columns
    ]

    query = select(*select_columns).where(access_filter)

    # Status 3 é unidade removida (exclusão lógica). O plataforma_web filtra
    # `status <> 3` em toda listagem (vault: Unidades, seção 3); sem isso a
    # FERTRAN aparecia com 531 veículos, 318 deles removidos, e todo
    # indicador sobre a frota (disponibilidade, médias) saía diluído.
    query = query.where(Vehicle.status != 3)

    if group_id is not None:
        query = query.where(Vehicle.group_id == group_id)

    # Paginação por cursor: ORDER BY (date_add DESC NULLS LAST, id DESC) +
    # WHERE de keyset a partir do cursor da página anterior.
    query = apply_keyset_pagination(query, Vehicle.date_add, Vehicle.id, decoded_cursor)
    query = query.limit(limit + 1)  # +1 para saber se há próxima página

    result = await db.execute(query)
    rows = result.mappings().all()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Estado atual, numa consulta só para a página inteira.
    #
    # `dev_status` guarda a última leitura de cada equipamento — é ali que está
    # o odômetro de hoje. `tracked_unit.initial_odometer` é o valor de quando o
    # equipamento foi instalado: está preenchido em poucos veículos e não
    # avança, então calcular manutenção sobre ele usa quilometragem de meses
    # atrás e nada vence nunca.
    #
    # Uma consulta por página e não uma por veículo: com 1.000 por página,
    # buscar individualmente seriam mil idas ao banco para montar uma lista.
    estados: dict[int, dict] = {}
    if rows:
        ids = [r["id"] for r in rows]
        estados = {
            e["unit_id"]: dict(e)
            for e in (
                await db.execute(
                    text(
                        """
                        SELECT unit_id, odom, odom_total, odom_quality_flag,
                               hourmeter_total, can_avg_fuel_economy_kmpl,
                               can_total_odometer, can_engine_hourmeter,
                               local_time, speed, ignition
                        FROM mova.dev_status
                        WHERE unit_id = ANY(:ids)
                        """
                    ),
                    {"ids": ids},
                )
            ).mappings()
        }

    def _build_response(row):
        data = dict(row)
        for col_key in _RISKY_DATETIME_COLUMNS:
            data[col_key] = _parse_safe_datetime(data[col_key])
        # Nulo quando o veículo nunca transmitiu — diferente de odômetro zero,
        # que seria uma leitura afirmando que o veículo não rodou.
        data["estado_atual"] = estados.get(data["id"])
        return data

    data = [_build_response(row) for row in rows]

    next_cursor = None
    if data and has_more:
        last_row = data[-1]
        next_cursor = build_next_cursor(last_row['date_add'], last_row['id'])

    return VehicleCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/{vehicle_id}", response_model=VehicleResponse)
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """Get vehicle by ID (only if user has access to vehicle's group/subgroup)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build filter considering group_id and subgroup_id with NULL logic
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found"
        )

    return vehicle


@router.post("/", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    vehicle_data: VehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "create"))
):
    """Create a new vehicle in a group/subgroup the user has access to."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Validate user has access to target group/subgroup
    target_group = (vehicle_data.group_id, vehicle_data.subgroup_id)
    if target_group not in current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to target group/subgroup"
        )

    # Check if label (plate) already exists
    result = await db.execute(
        select(Vehicle).where(Vehicle.label == vehicle_data.label)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vehicle with this label already exists"
        )

    # Create vehicle
    vehicle = Vehicle(
        **vehicle_data.model_dump(),
        account_id=539,  # Fixed account_id
        status=1,  # Active by default
        user_add=current_user.user_id
    )

    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)

    return vehicle


@router.put("/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: int,
    vehicle_data: VehicleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "update"))
):
    """Update vehicle by ID (only if user has access to vehicle's group/subgroup)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build filter considering group_id and subgroup_id with NULL logic
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get vehicle (with group/subgroup access check)
    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found"
        )

    # Update fields
    update_data = vehicle_data.model_dump(exclude_unset=True)

    # If changing group/subgroup, validate user has access to new group/subgroup
    if 'group_id' in update_data or 'subgroup_id' in update_data:
        new_group_id = update_data.get('group_id', vehicle.group_id)
        new_subgroup_id = update_data.get('subgroup_id', vehicle.subgroup_id)
        target_group = (new_group_id, new_subgroup_id)

        if target_group not in current_user.group_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to target group/subgroup"
            )

    for field, value in update_data.items():
        setattr(vehicle, field, value)

    # Update audit fields
    vehicle.user_modif = current_user.user_id
    vehicle.date_modif = datetime.now()

    await db.commit()
    await db.refresh(vehicle)

    return vehicle


@router.delete("/{vehicle_id}", response_model=VehicleResponse)
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    """
    Soft delete vehicle by ID (only if user has access to vehicle's group/subgroup).
    Sets status to -1 and updates removal audit fields.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build filter considering group_id and subgroup_id with NULL logic
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found"
        )

    # Cascade soft delete: Release any active device associations
    now = datetime.now()

    # Update any active TrackedUnitDevice associations (status=1) for this vehicle
    associations_result = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.tracked_unit_id == vehicle_id,
            TrackedUnitDevice.status == 1
        )
    )
    active_associations = associations_result.scalars().all()

    for association in active_associations:
        association.status = -1
        association.device_primary = 0
        if association.release_date is None:
            association.release_date = now

    # Update any active VcmsUnitDevice associations (video devices)
    vcms_associations_result = await db.execute(
        select(VcmsUnitDevice).where(
            VcmsUnitDevice.unit_id == vehicle_id,
            VcmsUnitDevice.status == 1
        )
    )
    active_vcms_associations = vcms_associations_result.scalars().all()

    for association in active_vcms_associations:
        association.status = -1
        if association.release_date is None:
            association.release_date = now

    # Soft delete: update status and audit fields
    vehicle.status = -1
    vehicle.user_modif = current_user.user_id
    vehicle.date_modif = now
    vehicle.user_removed = current_user.user_id
    vehicle.date_removed = now

    await db.commit()
    await db.refresh(vehicle)

    return vehicle