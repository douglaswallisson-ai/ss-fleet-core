"""
Database configuration and session management.
Provides SQLAlchemy engine, session factory, and database utilities.

Architecture:
- Master (DATABASE_URL): Used for INSERT, UPDATE, DELETE operations
- Read Replica (DATABASE_REPLICA_URL): Used for SELECT operations

Usage:
- get_db(): For write operations (master)
- get_db_read(): For read-only operations (replica)
"""

from typing import AsyncGenerator, Generator
from contextlib import contextmanager
import time

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool, AsyncAdaptedQueuePool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Base class for SQLAlchemy models
Base = declarative_base()

# ========================================
# Master Database (Read/Write)
# ========================================

# Synchronous engine (for Alembic migrations and sync operations)
#
# Criado sob demanda, não na importação.
#
# Ele existe para as migrations e usa `psycopg2`, que carrega uma biblioteca
# nativa. A aplicação em si é assíncrona e fala com o banco por `asyncpg` —
# nunca precisa deste motor.
#
# Instanciar na importação obriga a carregar `psycopg2` para **subir a API**, e
# num ambiente com controle de aplicativo a biblioteca nativa é bloqueada: o
# processo nem inicia, por uma dependência que não seria usada.
_sync_engine = None


def get_sync_engine():
    """Motor síncrono, instanciado na primeira chamada."""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            echo=settings.DEBUG,
            poolclass=QueuePool,
        )
        # Monitoramento de consulta lenta, ligado junto com o motor.
        event.listen(_sync_engine, "before_cursor_execute", before_cursor_execute)
        event.listen(_sync_engine, "after_cursor_execute", after_cursor_execute)
    return _sync_engine


class _SyncEngineProxy:
    """
    Mantém ``sync_engine`` utilizável como antes.

    Quem já importava o objeto continua funcionando; a diferença é que a
    conexão só é criada quando algum atributo é acessado de fato.
    """

    def __getattr__(self, nome):
        return getattr(get_sync_engine(), nome)


sync_engine = _SyncEngineProxy()

def _connect_args() -> dict:
    """
    Argumentos de conexão do driver assíncrono.

    Alguns modelos declaram ``{'schema': 'mova'}`` e outros não — os de
    permissão (``api_permissions``, ``api_user_permissions``) usam o nome
    simples. Em produção isso funciona porque o usuário do banco tem
    ``search_path`` apontando para ``mova``; um usuário sem essa configuração
    recebe ``relation "api_permissions" does not exist`` logo após autenticar.

    Definir o caminho na conexão resolve sem depender de como cada usuário foi
    criado, e sem alterar os modelos — mexer neles mudaria o comportamento de
    quem já funciona.

    A ordem segue a do usuário ``suporte``: ``public, audit, mova``.
    """
    caminho = getattr(settings, "DATABASE_SEARCH_PATH", None)
    if not caminho:
        return {}
    return {"server_settings": {"search_path": caminho}}


# Asynchronous engine for Master (INSERT, UPDATE, DELETE)
async_engine = create_async_engine(
    settings.database_url_async,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    echo=settings.DEBUG,
    # DS-1380: QueuePool é a implementação SÍNCRONA (usa threading.Event/
    # queue.Queue bloqueantes) e não é compatível com engines assíncronos.
    # AsyncAdaptedQueuePool é o equivalente async-safe, com a mesma API de
    # tuning (pool_size, max_overflow) - é o que create_async_engine já
    # escolhe por padrão quando poolclass não é especificado.
    poolclass=AsyncAdaptedQueuePool,
    connect_args=_connect_args(),
)

# ========================================
# Read Replica Database (Read-Only)
# ========================================

# Asynchronous engine for Read Replica (SELECT only)
async_engine_replica = create_async_engine(
    settings.database_replica_url_async,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_REPLICA_POOL_SIZE,
    max_overflow=settings.DATABASE_REPLICA_MAX_OVERFLOW,
    echo=settings.DEBUG,
    # DS-1380: mesmo motivo do async_engine acima.
    poolclass=AsyncAdaptedQueuePool,
    connect_args=_connect_args(),
)

# Log which databases are configured
if settings.DATABASE_REPLICA_URL:
    logger.info(
        "database_replica_configured",
        master_host=str(settings.DATABASE_URL).split("@")[1].split("/")[0] if "@" in str(settings.DATABASE_URL) else "unknown",
        replica_host=str(settings.DATABASE_REPLICA_URL).split("@")[1].split("/")[0] if "@" in str(settings.DATABASE_REPLICA_URL) else "unknown",
    )
else:
    logger.warning(
        "database_replica_not_configured",
        message="DATABASE_REPLICA_URL not set, read operations will use master"
    )

# ========================================
# Session Factories
# ========================================

# O sessionmaker aceita o proxy: ele só resolve o motor quando uma sessão é
# de fato aberta, o que nas migrations acontece e na API não.
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=sync_engine,
    class_=Session,
)

# Master session factory (for write operations)
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Replica session factory (for read operations)
AsyncSessionLocalReplica = async_sessionmaker(
    async_engine_replica,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# Database query monitoring
#
# Registrados dentro de `get_sync_engine`, não no nível do módulo: decorar com
# `event.listens_for(sync_engine, ...)` aqui resolveria o proxy na importação e
# criaria o motor — exatamente o que se evita, já que ele carrega `psycopg2` e
# a API não precisa dele para subir.
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Record query start time for monitoring."""
    conn.info.setdefault("query_start_time", []).append(time.time())


def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Log slow queries for performance monitoring."""
    total_time = time.time() - conn.info["query_start_time"].pop()

    # Log slow queries (>1 second)
    if total_time > 1.0:
        query_type = statement.strip().split()[0].upper()
        logger.warning(
            "slow_query_detected",
            duration_ms=round(total_time * 1000, 2),
            query_type=query_type,
            statement=statement[:200],  # Log first 200 characters
        )


# ========================================
# FastAPI Dependencies
# ========================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for WRITE operations (Master database).

    Use this for:
    - INSERT operations
    - UPDATE operations
    - DELETE operations
    - Transactions that modify data

    Usage:
        @app.post("/items")
        async def create_item(db: AsyncSession = Depends(get_db)):
            db.add(item)
            await db.commit()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("database_write_error", error=str(e), exc_info=True)
            raise
        finally:
            await session.close()


async def get_db_read() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for READ operations (Read Replica database).

    Use this for:
    - SELECT operations
    - List endpoints
    - Report queries
    - Any read-only operation

    IMPORTANT:
    - Never use for write operations (will fail on replica)
    - Be aware of replication lag (typically < 1 second)
    - For read-after-write scenarios, use get_db() instead

    Usage:
        @app.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db_read)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocalReplica() as session:
        try:
            yield session
        except Exception as e:
            logger.error("database_read_error", error=str(e), exc_info=True)
            raise
        finally:
            await session.close()


# Synchronous session context manager (for migrations and sync operations)
@contextmanager
def get_db_sync() -> Generator[Session, None, None]:
    """
    Context manager to get synchronous database session.

    Usage:
        with get_db_sync() as db:
            item = db.query(Item).first()
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("database_error", error=str(e), exc_info=True)
        raise
    finally:
        session.close()


async def init_db() -> None:
    """
    Initialize database tables.
    Only use in development - in production use Alembic migrations.
    """
    async with async_engine.begin() as conn:
        # Import all models to register them with Base
        from app.models import user, vehicle, device, position, event, api_key

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
        logger.info("database_tables_created")


async def close_db() -> None:
    """Close all database connections on application shutdown."""
    await async_engine.dispose()
    await async_engine_replica.dispose()
    sync_engine.dispose()
    logger.info("database_connections_closed", master=True, replica=True)


# Database health check
async def check_db_health() -> dict:
    """
    Check database connections health.
    Returns dict with master and replica status.
    """
    result = {"master": False, "replica": False}

    # Check master
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        result["master"] = True
    except Exception as e:
        logger.error("database_master_health_check_failed", error=str(e))

    # Check replica
    try:
        async with AsyncSessionLocalReplica() as session:
            await session.execute(text("SELECT 1"))
        result["replica"] = True
    except Exception as e:
        logger.error("database_replica_health_check_failed", error=str(e))

    return result


async def check_db_health_simple() -> bool:
    """
    Simple health check - returns True if master is accessible.
    For backward compatibility.
    """
    health = await check_db_health()
    return health["master"]