"""
Testes da DS-1379 (épico DS-1342): paginação real por cursor em
GET /drivers e GET /vehicles.

Roda contra um banco SQLite em memória (não é Postgres real, mas exercita
o código de produção de ponta a ponta: geração de SQL, execução, e
montagem da resposta) com dois ajustes de compatibilidade:

1. `to_char(valor, formato)` é registrada como função SQL customizada,
   emulando o suficiente da função nativa do Postgres para os dois
   formatos usados em drivers.py/vehicles.py ('YYYY-MM-DD' e
   'YYYY-MM-DD HH24:MI:SS') - o SQLite já guarda datetime como texto
   ISO-8601, então a emulação é só um corte de string.
2. O schema `mova` (usado via `__table_args__ = {'schema': 'mova'}` nos
   models) é criado com `ATTACH DATABASE ':memory:' AS mova`, que o
   SQLite entende como um "schema" de fato.

Executar com:
    pytest tests/test_pagination_cursor.py -v
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.database import get_db_read as real_get_db_read
from app.middleware.auth import AuthenticatedUser, get_current_user
from app.models.driver import Driver
from app.models.vehicle import Vehicle
from app.api.v1.endpoints.drivers import router as drivers_router
from app.api.v1.endpoints.vehicles import router as vehicles_router


def _sqlite_to_char(value, fmt):
    """Emula o suficiente de to_char(timestamp, formato) do Postgres para
    os dois formatos usados nos endpoints sob teste."""
    if value is None:
        return None
    text_value = str(value)
    if fmt == "YYYY-MM-DD":
        return text_value[:10]
    if fmt == "YYYY-MM-DD HH24:MI:SS":
        return text_value[:19]
    raise ValueError(f"Formato não suportado no stub de teste: {fmt}")


@pytest_asyncio.fixture()
async def engine():
    """Engine SQLite em memória, compartilhada (StaticPool) para persistir
    entre conexões, com to_char registrada e o schema 'mova' anexado."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(eng.sync_engine, "connect")
    def _on_connect(dbapi_connection, connection_record):
        dbapi_connection.create_function("to_char", 2, _sqlite_to_char)

    async with eng.begin() as conn:
        await conn.execute(text("ATTACH DATABASE ':memory:' AS mova"))
        await conn.execute(text("ATTACH DATABASE ':memory:' AS vcms"))
        # IMPORTANTE: `Base` é o registro declarativo compartilhado por
        # TODA a aplicação - no momento em que a suíte completa de testes
        # roda, dezenas de outros models (Group, Subgroup, User, etc.)
        # já estão registrados nele, alguns com FKs pra tabelas sem model
        # no projeto (ex.: Group.account_id -> mova.account.id, que não
        # existe como model SQLAlchemy). Rodar `Base.metadata.create_all`
        # sem filtro faz o SQLAlchemy tentar criar TODAS as tabelas
        # conhecidas e quebra em FKs não resolvíveis. Este teste só
        # precisa de Driver e Vehicle, então criamos apenas essas duas.
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[Driver.__table__, Vehicle.__table__],
        )

    yield eng

    await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
def app(session_factory):
    """
    App FastAPI mínimo com os routers reais de drivers e vehicles, banco
    SQLite via override, e usuário fixo com acesso ao group_id=1.

    Por que sobrescrever `get_current_user` e não `require_permission`:
    `require_permission(resource, action)` (em app/middleware/auth.py)
    cria uma closure NOVA a cada chamada - `require_permission("drivers",
    "read")` e `require_permission("vehicles", "read")` retornam objetos
    diferentes, mesmo com os mesmos argumentos. `app.dependency_overrides`
    faz override por identidade do objeto, então não dá pra sobrescrever
    essas closures diretamente sem acoplar o teste aos objetos internos
    de drivers.py/vehicles.py.

    A solução correta: `require_permission(...)` sempre depende de
    `get_current_user` internamente (`Depends(get_current_user)`) - essa
    função É estável e importável. Sobrescrevendo só ela, cobrimos
    qualquer endpoint que dependa de `require_permission`, para qualquer
    (resource, action), com um único override.
    """
    test_app = FastAPI()
    test_app.include_router(drivers_router, prefix="/api/v1/drivers")
    test_app.include_router(vehicles_router, prefix="/api/v1/vehicles")

    async def _override_get_db_read():
        async with session_factory() as session:
            yield session

    async def _override_get_current_user():
        # group_access=(1, 1) - não (1, None) - de propósito: na lógica
        # real de app/core/access_control.py, um acesso com subgroup_id
        # None só enxerga recursos com subgroup_id IS NULL (recursos
        # compartilhados - essa é justamente a correção de segurança do
        # SEC-2025-001/DS-1381: acesso NULL não pode "ver tudo"). Como os
        # veículos de teste são semeados com subgroup_id=1 (fixo, para
        # bater com VehicleResponse.subgroup_id: int, não-opcional), o
        # usuário de teste precisa de acesso explícito a (1, 1). Isso
        # também cobre os motoristas de teste (subgroup_id NULL, sem
        # valor explícito no seed): a mesma condição de acesso (1, 1)
        # inclui "OR subgroup_id IS NULL" na lógica real, então os dois
        # cenários de seed continuam funcionando com uma única entrada de
        # acesso.
        return AuthenticatedUser(
            user_id=1,
            group_access=[(1, 1)],
            permissions={"drivers.read", "vehicles.read"},
        )

    test_app.dependency_overrides[real_get_db_read] = _override_get_db_read
    test_app.dependency_overrides[get_current_user] = _override_get_current_user

    return test_app


@pytest.fixture()
def client(app):
    return TestClient(app)


async def _seed_drivers(session_factory, count: int, group_id: int = 1, id_offset: int = 0):
    """Cria `count` motoristas com date_add decrescente (o mais recente
    primeiro), simulando inserções ao longo do tempo."""
    base_time = datetime(2025, 1, 1, 12, 0, 0)
    async with session_factory() as session:
        for i in range(count):
            session.add(
                Driver(
                    id=id_offset + i + 1,
                    name=f"Motorista {id_offset + i + 1}",
                    auth=1,
                    group_id=group_id,
                    status=1,
                    date_add=base_time + timedelta(minutes=i),
                )
            )
        await session.commit()


async def _seed_vehicles(session_factory, count: int, group_id: int = 1, id_offset: int = 0):
    base_time = datetime(2025, 1, 1, 12, 0, 0)
    async with session_factory() as session:
        for i in range(count):
            session.add(
                Vehicle(
                    id=id_offset + i + 1,
                    label=f"VEIC-{id_offset + i + 1:04d}",
                    group_id=group_id,
                    subgroup_id=1,
                    account_id=539,
                    unit_category_id=1,
                    status=1,
                    timezone=-3,
                    date_add=base_time + timedelta(minutes=i),
                )
            )
        await session.commit()


async def _seed_driver_with_null_date_add(session_factory, driver_id: int, group_id: int = 1):
    """
    Insere um motorista com date_add explicitamente NULL via SQL bruto.

    Necessário porque Driver.date_add tem `default=func.now()` no model -
    um `Driver(date_add=None, ...)` via ORM não persiste o NULL: o
    SQLAlchemy aplica o default do servidor mesmo com None atribuído
    explicitamente. Só um INSERT literal garante o NULL de verdade,
    reproduzindo o cenário real de um registro legado sem date_add.
    """
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO mova.driver (id, name, auth, group_id, status, date_add) "
                "VALUES (:id, :name, 1, :group_id, 1, NULL)"
            ),
            {"id": driver_id, "name": f"Motorista {driver_id} (sem data)", "group_id": group_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# /drivers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drivers_first_page_has_more_and_cursor(session_factory, client):
    await _seed_drivers(session_factory, count=25)

    response = client.get("/api/v1/drivers/", params={"limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is True
    assert body["total_returned"] == 10
    assert body["next_cursor"] is not None
    # Mais recente primeiro (date_add mais alto = id mais alto, no seed)
    assert body["data"][0]["id"] == 25
    assert body["data"][-1]["id"] == 16


@pytest.mark.asyncio
async def test_drivers_pagination_covers_all_rows_without_gaps_or_duplicates(session_factory, client):
    """
    O teste mais importante desta tarefa: percorre todas as páginas via
    next_cursor e confirma que cada motorista aparece exatamente uma vez -
    nem pulado, nem duplicado (o risco central de uma keyset pagination
    mal implementada).
    """
    total = 37
    await _seed_drivers(session_factory, count=total)

    seen_ids = []
    cursor = None
    for _ in range(total + 1):  # margem de segurança contra loop infinito
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/drivers/", params=params)
        assert response.status_code == 200
        body = response.json()
        seen_ids.extend(row["id"] for row in body["data"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]

    assert len(seen_ids) == total
    assert len(set(seen_ids)) == total  # nenhuma duplicata
    assert set(seen_ids) == set(range(1, total + 1))  # nenhum id faltando


@pytest.mark.asyncio
async def test_drivers_invalid_cursor_returns_400(session_factory, client):
    await _seed_drivers(session_factory, count=1)

    response = client.get("/api/v1/drivers/", params={"cursor": "isso-nao-e-um-cursor-valido"})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_drivers_respects_group_access(session_factory, client):
    """Motoristas de um group_id fora do acesso do usuário não devem
    aparecer, mesmo que existam no banco."""
    await _seed_drivers(session_factory, count=5, group_id=1, id_offset=0)
    await _seed_drivers(session_factory, count=5, group_id=999, id_offset=100)  # fora do acesso

    response = client.get("/api/v1/drivers/", params={"limit": 100})

    body = response.json()
    assert body["total_returned"] == 5
    assert all(row["group_id"] == 1 for row in body["data"])


@pytest.mark.asyncio
async def test_drivers_null_date_add_is_paginated_last_and_not_lost(session_factory, client):
    """
    Caso de borda introduzido pela paginação por cursor: linhas com
    date_add NULL (não cobertas pela paginação OFFSET antiga, que não se
    importava com a posição relativa de NULLs) precisam continuar
    aparecendo, e de forma determinística - aqui, sempre por último.
    """
    await _seed_drivers(session_factory, count=1, group_id=1, id_offset=0)  # id=1, com data
    await _seed_driver_with_null_date_add(session_factory, driver_id=2, group_id=1)  # sem data

    seen_ids = []
    cursor = None
    for _ in range(5):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/drivers/", params=params)
        body = response.json()
        seen_ids.extend(row["id"] for row in body["data"])
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]

    assert seen_ids == [1, 2]  # a linha com date_add NULL vem por último


# ---------------------------------------------------------------------------
# /vehicles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vehicles_pagination_covers_all_rows_without_gaps_or_duplicates(session_factory, client):
    total = 23
    await _seed_vehicles(session_factory, count=total)

    seen_ids = []
    cursor = None
    for _ in range(total + 1):
        params = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/vehicles/", params=params)
        assert response.status_code == 200
        body = response.json()
        seen_ids.extend(row["id"] for row in body["data"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]

    assert len(seen_ids) == total
    assert len(set(seen_ids)) == total
    assert set(seen_ids) == set(range(1, total + 1))


@pytest.mark.asyncio
async def test_vehicles_group_id_filter_combines_with_cursor(session_factory, client):
    """O filtro group_id (adicional, fora do access_filter) deve continuar
    funcionando junto com a paginação por cursor."""
    await _seed_vehicles(session_factory, count=10, group_id=1)

    response = client.get("/api/v1/vehicles/", params={"group_id": 1, "limit": 100})

    body = response.json()
    assert body["total_returned"] == 10
    assert all(row["group_id"] == 1 for row in body["data"])


@pytest.mark.asyncio
async def test_vehicles_no_group_access_returns_empty_envelope(client):
    """Usuário sem group_access deve receber o envelope vazio, não uma
    lista simples nem um erro."""
    client.app.dependency_overrides[get_current_user] = (
        lambda: AuthenticatedUser(user_id=1, group_access=[], permissions={"vehicles.read"})
    )

    response = client.get("/api/v1/vehicles/")

    assert response.status_code == 200
    body = response.json()
    assert body == {"data": [], "next_cursor": None, "has_more": False, "total_returned": 0}