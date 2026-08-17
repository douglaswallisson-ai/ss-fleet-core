"""
Testes da DS-1380 (épico DS-1342): incompatibilidade poolclass=QueuePool
com create_async_engine.

Causa raiz confirmada nesta investigação: `create_async_engine(...,
poolclass=QueuePool)` é aceito silenciosamente pelo SQLAlchemy 1.4.x (o
engine resultante usa QueuePool - a implementação SÍNCRONA, com
threading.Event/queue.Queue bloqueantes - por baixo de um engine
assíncrono, o que é inseguro mas não falha imediatamente). A partir do
SQLAlchemy 2.0, isso passou a ser validado e levanta
`sqlalchemy.exc.ArgumentError: Pool class QueuePool cannot be used with
asyncio engine` já na CRIAÇÃO do engine - ou seja, na importação de
app/core/database.py. Por isso o bug "não reproduz em produção" (que
provavelmente resolve SQLAlchemy 1.4.x) mas travava a suíte de pytest no
sandbox (que resolveu SQLAlchemy 2.0.x).

Correção: usar `AsyncAdaptedQueuePool` (equivalente async-safe, mesma
API de tuning: pool_size, max_overflow) nos dois engines assíncronos
(`async_engine`, `async_engine_replica`). O `sync_engine` continua
corretamente usando `QueuePool`.

Executar com:
    pytest tests/test_database_pool.py -v
"""

import importlib

import pytest
from sqlalchemy.pool import QueuePool, AsyncAdaptedQueuePool


@pytest.fixture()
def database_module():
    """Importa (ou reimporta) app.core.database, garantindo um estado
    limpo mesmo se outro teste já tiver importado o módulo antes."""
    import app.core.database as db
    importlib.reload(db)
    yield db


def test_sync_engine_uses_queuepool(database_module):
    """O engine síncrono (usado por Alembic/scripts síncronos) deve
    continuar usando QueuePool normalmente - isso nunca foi o problema."""
    assert isinstance(database_module.sync_engine.pool, QueuePool)
    # QueuePool "puro" não deve ser confundido com a subclasse async
    assert not isinstance(database_module.sync_engine.pool, AsyncAdaptedQueuePool)


def test_async_engine_uses_async_adapted_queuepool(database_module):
    """O engine assíncrono master (INSERT/UPDATE/DELETE) precisa usar a
    variante async-safe do pool."""
    assert isinstance(database_module.async_engine.pool, AsyncAdaptedQueuePool)


def test_async_engine_replica_uses_async_adapted_queuepool(database_module):
    """O engine assíncrono de réplica (SELECT) precisa usar a mesma
    variante async-safe do pool."""
    assert isinstance(database_module.async_engine_replica.pool, AsyncAdaptedQueuePool)


def test_async_engines_preserve_pool_tuning_settings(database_module):
    """AsyncAdaptedQueuePool deve continuar respeitando pool_size e
    max_overflow configurados via settings - a troca de classe não pode
    silenciosamente desligar o tuning de conexões."""
    settings = database_module.settings

    assert database_module.async_engine.pool.size() == settings.DATABASE_POOL_SIZE
    assert database_module.async_engine.pool._max_overflow == settings.DATABASE_MAX_OVERFLOW

    assert database_module.async_engine_replica.pool.size() == settings.DATABASE_REPLICA_POOL_SIZE
    assert (
        database_module.async_engine_replica.pool._max_overflow
        == settings.DATABASE_REPLICA_MAX_OVERFLOW
    )


def test_database_module_imports_without_error_on_sqlalchemy_2x():
    """
    Trip-wire de ponta a ponta: o simples IMPORT do módulo já era
    suficiente para reproduzir o bug original (o ArgumentError acontece
    na construção do engine, que roda no nível do módulo, não dentro de
    uma função). Este teste passando é, por si só, a evidência de que a
    suíte completa de pytest não é mais bloqueada por este problema.
    """
    import app.core.database  # nowhere to catch - se falhar, o teste falha na coleta


def test_source_never_pairs_create_async_engine_with_sync_queuepool():
    """
    Varredura estática (trip-wire): nenhuma chamada a create_async_engine
    no arquivo pode ser seguida por `poolclass=QueuePool` (sem o prefixo
    AsyncAdapted) antes da próxima ocorrência de create_engine/
    create_async_engine - previne reintrodução futura do bug.
    """
    import pathlib
    import re

    db_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"
    source = db_path.read_text(encoding="utf-8")

    # Encontra cada bloco "create_async_engine(...)" (até o fechamento do
    # parêntese correspondente, de forma simplificada por linhas) e
    # confirma que não contém "poolclass=QueuePool" (só o prefixo exato,
    # sem o "AsyncAdapted" na frente).
    blocks = re.split(r"(?=create_async_engine\()", source)
    offending = [
        block for block in blocks
        if block.startswith("create_async_engine(")
        and re.search(r"poolclass\s*=\s*QueuePool\b(?!.*AsyncAdapted)", block.split(")\n", 1)[0])
    ]

    assert not offending, (
        "create_async_engine(...) usando poolclass=QueuePool (sem AsyncAdapted) encontrado - "
        "isso quebra em SQLAlchemy 2.0+ e é inseguro em 1.4.x."
    )