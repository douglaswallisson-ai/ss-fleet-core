"""
Driver endpoints - Driver/conductor management.

CRUD operations for driver management with business rules:
1. Soft delete pattern (status=1 active, status=-1 deleted)
2. Access control through group_id and subgroup_id
3. Unique fields per group: login, cpf, email, matricula
4. Unique fields GLOBALLY: cnh (national document - cannot repeat)
5. Automatic SHA1 hashing for password_apps
6. CNH validation: format (11 digits), expiration tracking, category validation
"""

from typing import Optional
from datetime import datetime, date
import hashlib
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.core.cursor_pagination import CompositeCursor, apply_keyset_pagination, build_next_cursor
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.driver import Driver
from app.models.driver_function import DriverFunction
from app.schemas.driver import DriverCreate, DriverUpdate, DriverResponse, DriverWithFunction, DriverCursorResponse

router = APIRouter()

# Datas fora do intervalo que o Python consegue representar (ano 1-9999,
# ex.: um "20234" digitado por engano em vez de "2023-04") fazem o
# asyncpg quebrar ao decodificar o valor binário de volta pra um objeto
# date - a exceção acontece na camada do driver do banco, ANTES até do
# Pydantic/response model, então nenhuma validação em nível de schema
# alcança isso. Tentamos "grampear" com LEAST/GREATEST direto em SQL,
# mas o protocolo BINÁRIO do asyncpg ainda quebra do mesmo jeito (só o
# modo texto do psql calcula certo). A saída robusta: converter essas
# colunas pra TEXTO já na consulta (o cast pra texto nunca quebra,
# independente do tamanho do ano) e interpretar a data manualmente em
# Python, com um try/except.
_RISKY_DATE_COLUMNS = ('cnh_validate', 'admission', 'rac_validate', 'aso_validate')


def _safe_date_column(column):
    """SELECT dessa coluna DATE já convertida pra texto (ISO), pra nunca
    quebrar o decodificador binário do asyncpg."""
    return func.to_char(column, 'YYYY-MM-DD').label(column.key)


def _parse_safe_date(value):
    """Convertido de volta pra date em Python, com fallback pra None se
    o valor estiver fora do intervalo suportado (ano > 9999 ou <= 0)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, OverflowError):
        return None


def hash_password_sha1(password: str) -> str:
    """Hash password using SHA1."""
    return hashlib.sha1(password.encode()).hexdigest()


@router.get("/", response_model=DriverCursorResponse)
async def list_drivers(
    cursor: Optional[str] = Query(None, description="Cursor da página anterior (base64, retornado em next_cursor). Omitir na primeira requisição."),
    limit: int = Query(100, ge=1, le=5000),
    group_id: int = Query(None, description="Filter by group ID"),
    subgroup_id: int = Query(None, description="Filter by subgroup ID"),
    function_id: int = Query(None, description="Filter by driver function ID"),
    cnh_expired: bool = Query(None, description="Filter by CNH expiration status"),
    include_deleted: bool = Query(False, description="Include soft-deleted drivers"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("drivers", "read"))
):
    """
    List all drivers accessible by current user, com paginação real por
    cursor (DS-1379 - substitui o antigo skip/limit por OFFSET).

    Access control: User can only see drivers in their accessible groups/subgroups.

    Paginação (keyset, mesmo padrão dos endpoints de relatório):
    - Primeira página: não envie `cursor`.
    - Próximas páginas: envie o `next_cursor` retornado na resposta anterior.
    - `has_more=false` indica que não há mais páginas.
    - Diferente do antigo `skip`, o custo desta paginação não cresce com a
      profundidade da página - importante para grupos com milhares de
      motoristas (ex.: Fertran, ~1210 motoristas e crescendo).

    BREAKING CHANGE: o parâmetro `skip` foi removido e a resposta deixou
    de ser uma lista simples (`[...]`) para ser um envelope
    (`{data, next_cursor, has_more, total_returned}`). Ver tarefa de
    frontend "Consumir paginação real de /drivers e /vehicles".
    """
    if not current_user.group_access:
        return DriverCursorResponse(data=[], next_cursor=None, has_more=False, total_returned=0)

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

    # Build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Driver)

    # Seleciona todas as colunas do Driver, mas com as 4 colunas de data
    # de risco convertidas pra texto na consulta (ver _safe_date_column
    # acima) - as cláusulas WHERE abaixo continuam comparando contra a
    # coluna original (sem conversão), já que a comparação acontece
    # dentro do Postgres e nunca precisa decodificar o valor num objeto
    # Python.
    select_columns = [
        _safe_date_column(col) if col.key in _RISKY_DATE_COLUMNS else col
        for col in Driver.__table__.columns
    ]

    query = select(*select_columns).where(access_filter)

    # Apply filters
    if group_id:
        query = query.where(Driver.group_id == group_id)

    if subgroup_id:
        query = query.where(Driver.subgroup_id == subgroup_id)

    if function_id:
        query = query.where(Driver.driver_function_id == function_id)

    if not include_deleted:
        query = query.where(Driver.status != -1)

    # CNH expiration filter
    if cnh_expired is not None:
        today = date.today()
        if cnh_expired:
            query = query.where(Driver.cnh_validate < today)
        else:
            query = query.where(or_(Driver.cnh_validate >= today, Driver.cnh_validate.is_(None)))

    # Paginação por cursor: ORDER BY (date_add DESC NULLS LAST, id DESC) +
    # WHERE de keyset a partir do cursor da página anterior. date_add não
    # está em _RISKY_DATE_COLUMNS (é TIMESTAMP preenchido pelo sistema,
    # não sujeito ao mesmo risco de digitação humana das colunas de
    # licença), então pode ser comparado diretamente sem o cast pra texto.
    query = apply_keyset_pagination(query, Driver.date_add, Driver.id, decoded_cursor)
    query = query.limit(limit + 1)  # +1 para saber se há próxima página

    result = await db.execute(query)
    rows = result.mappings().all()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Como não selecionamos mais a entidade ORM Driver diretamente (por
    # causa do grampo de data acima), as "virtual properties" do model
    # (is_active, is_deleted, cnh_expired, rac_expired, aso_expired)
    # precisam ser calculadas aqui manualmente, com a mesma lógica do
    # model (app/models/driver.py).
    today = date.today()

    def _build_response(row):
        data = dict(row)
        for col_key in _RISKY_DATE_COLUMNS:
            data[col_key] = _parse_safe_date(data[col_key])
        data['is_active'] = data['status'] == 1
        data['is_deleted'] = data['status'] == -1
        data['cnh_expired'] = (data['cnh_validate'] < today) if data['cnh_validate'] else None
        data['rac_expired'] = (data['rac_validate'] < today) if data['rac_validate'] else None
        data['aso_expired'] = (data['aso_validate'] < today) if data['aso_validate'] else None
        return data

    data = [_build_response(row) for row in rows]

    next_cursor = None
    if data and has_more:
        last_row = data[-1]
        next_cursor = build_next_cursor(last_row['date_add'], last_row['id'])

    return DriverCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/{driver_id}", response_model=DriverWithFunction)
async def get_driver(
    driver_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("drivers", "read"))
):
    """Get driver by ID with function details (only if user has access)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Driver)

    # Get driver with function details
    result = await db.execute(
        select(
            Driver,
            DriverFunction.name.label('function_name')
        )
        .outerjoin(DriverFunction, Driver.driver_function_id == DriverFunction.id)
        .where(
            Driver.id == driver_id,
            access_filter,
            Driver.status != -1  # Exclude soft-deleted
        )
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver not found"
        )

    driver, function_name = row

    # Build response with function details
    response_data = {
        **DriverResponse.model_validate(driver).model_dump(),
        "function_name": function_name
    }

    return DriverWithFunction(**response_data)


@router.post("/", response_model=DriverResponse, status_code=status.HTTP_201_CREATED)
async def create_driver(
    driver_data: DriverCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("drivers", "create"))
):
    """
    Create new driver.

    Business Rules:
    1. User must have write access to driver's group/subgroup
    2. Unique fields per group: login, cpf, email, matricula
    3. Unique fields GLOBALLY: cnh (national document)
    4. CNH format validation: 11 digits
    5. CNH category validation: A, B, C, D, E, AB, AC, AD, AE
    6. Automatic SHA1 hashing for password_apps
    7. Validate driver_function_id exists
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Validate user has access to target group/subgroup
    target_group = (driver_data.group_id, driver_data.subgroup_id)
    if target_group not in current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to target group/subgroup"
        )

    # Validate driver_function_id exists if provided
    if driver_data.driver_function_id:
        function_result = await db.execute(
            select(DriverFunction).where(
                DriverFunction.id == driver_data.driver_function_id,
                DriverFunction.status == 1
            )
        )
        if not function_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Driver function not found or not active"
            )

    # Check uniqueness per group
    if driver_data.login:
        existing = await db.execute(
            select(Driver).where(
                Driver.login == driver_data.login,
                Driver.group_id == driver_data.group_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Login already exists in this group"
            )

    if driver_data.cpf:
        existing = await db.execute(
            select(Driver).where(
                Driver.cpf == driver_data.cpf,
                Driver.group_id == driver_data.group_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CPF already exists in this group"
            )

    if driver_data.email:
        existing = await db.execute(
            select(Driver).where(
                Driver.email == driver_data.email,
                Driver.group_id == driver_data.group_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists in this group"
            )

    if driver_data.matricula:
        existing = await db.execute(
            select(Driver).where(
                Driver.matricula == driver_data.matricula,
                Driver.group_id == driver_data.group_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Matricula already exists in this group"
            )

    # Check CNH uniqueness GLOBALLY (not per group - CNH is a national document)
    if driver_data.cnh:
        existing = await db.execute(
            select(Driver).where(
                Driver.cnh == driver_data.cnh,
                Driver.status != -1  # No group filter - CNH must be unique globally
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CNH already exists in the system"
            )

    # Prepare driver data
    driver_dict = driver_data.model_dump()

    # Hash password_apps if provided
    if driver_dict.get('password_apps'):
        driver_dict['password_apps'] = hash_password_sha1(driver_dict['password_apps'])

    # Create driver
    driver = Driver(
        **driver_dict,
        account_id=539,  # Fixed account_id
        status=1,
        user_add=current_user.user_id
    )

    db.add(driver)
    await db.commit()
    await db.refresh(driver)

    return driver


@router.put("/{driver_id}", response_model=DriverResponse)
async def update_driver(
    driver_id: int,
    driver_data: DriverUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("drivers", "update"))
):
    """
    Update driver by ID.

    Business Rules:
    1. User must have write access to driver's group/subgroup
    2. Validate unique fields per group if changed: login, cpf, email, matricula
    3. Validate unique fields GLOBALLY if changed: cnh
    4. CNH format validation if provided: 11 digits
    5. CNH category validation if provided: A, B, C, D, E, AB, AC, AD, AE
    6. Auto-hash password_apps if provided
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Driver)

    # Get driver
    result = await db.execute(
        select(Driver).where(
            Driver.id == driver_id,
            access_filter,
            Driver.status != -1
        )
    )
    driver = result.scalar_one_or_none()

    if not driver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver not found"
        )

    # Apply updates
    update_data = driver_data.model_dump(exclude_unset=True)

    # Validate driver_function_id if being updated
    if 'driver_function_id' in update_data and update_data['driver_function_id']:
        function_result = await db.execute(
            select(DriverFunction).where(
                DriverFunction.id == update_data['driver_function_id'],
                DriverFunction.status == 1
            )
        )
        if not function_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Driver function not found or not active"
            )

    # If changing group_id/subgroup_id, validate user has access to new group/subgroup
    if 'group_id' in update_data or 'subgroup_id' in update_data:
        new_group_id = update_data.get('group_id', driver.group_id)
        new_subgroup_id = update_data.get('subgroup_id', driver.subgroup_id)
        target_group = (new_group_id, new_subgroup_id)

        if target_group not in current_user.group_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to target group/subgroup"
            )

    # Check uniqueness per group for changed fields
    group_id = update_data.get('group_id', driver.group_id)

    if 'login' in update_data and update_data['login'] != driver.login:
        existing = await db.execute(
            select(Driver).where(
                Driver.login == update_data['login'],
                Driver.group_id == group_id,
                Driver.id != driver_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Login already exists in this group"
            )

    if 'cpf' in update_data and update_data['cpf'] != driver.cpf:
        existing = await db.execute(
            select(Driver).where(
                Driver.cpf == update_data['cpf'],
                Driver.group_id == group_id,
                Driver.id != driver_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CPF already exists in this group"
            )

    if 'email' in update_data and update_data['email'] != driver.email:
        existing = await db.execute(
            select(Driver).where(
                Driver.email == update_data['email'],
                Driver.group_id == group_id,
                Driver.id != driver_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists in this group"
            )

    if 'matricula' in update_data and update_data['matricula'] != driver.matricula:
        existing = await db.execute(
            select(Driver).where(
                Driver.matricula == update_data['matricula'],
                Driver.group_id == group_id,
                Driver.id != driver_id,
                Driver.status != -1
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Matricula already exists in this group"
            )

    # Check CNH uniqueness GLOBALLY (not per group - CNH is a national document)
    if 'cnh' in update_data and update_data['cnh'] != driver.cnh:
        existing = await db.execute(
            select(Driver).where(
                Driver.cnh == update_data['cnh'],
                Driver.id != driver_id,
                Driver.status != -1  # No group filter - CNH must be unique globally
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CNH already exists in the system"
            )

    # Hash password_apps if provided
    if 'password_apps' in update_data and update_data['password_apps']:
        update_data['password_apps'] = hash_password_sha1(update_data['password_apps'])

    # Apply updates
    for key, value in update_data.items():
        setattr(driver, key, value)

    # Update audit fields
    driver.user_modif = current_user.user_id
    driver.date_modif = datetime.now()

    await db.commit()
    await db.refresh(driver)

    return driver


@router.delete("/{driver_id}", response_model=DriverResponse)
async def delete_driver(
    driver_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("drivers", "delete"))
):
    """
    Soft delete driver by ID.

    Sets status=-1 and updates audit fields.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Driver)

    result = await db.execute(
        select(Driver).where(
            Driver.id == driver_id,
            access_filter,
            Driver.status != -1
        )
    )
    driver = result.scalar_one_or_none()

    if not driver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver not found"
        )

    # Soft delete
    now = datetime.now()
    driver.status = -1
    driver.user_modif = current_user.user_id
    driver.date_modif = now

    await db.commit()
    await db.refresh(driver)

    return driver