"""
Testes unitários da DS-1379 para app/core/cursor_pagination.py.

Cobre a codificação/decodificação do cursor e a montagem do SQL de
keyset pagination isoladamente, sem precisar de banco de dados (o
comportamento fim-a-fim, incluindo o caso de date_add NULL, está coberto
em tests/test_pagination_cursor.py).

Executar com:
    pytest tests/test_cursor_pagination.py -v
"""

from datetime import datetime

import pytest
from sqlalchemy import Column, Integer, DateTime, select
from sqlalchemy.orm import declarative_base

from app.core.cursor_pagination import (
    CompositeCursor,
    apply_keyset_pagination,
    build_next_cursor,
)


# ---------------------------------------------------------------------------
# CompositeCursor - encode/decode
# ---------------------------------------------------------------------------

def test_composite_cursor_roundtrip_with_sort_value():
    cursor = CompositeCursor(sort_value="2025-11-16 10:30:00", id_value=42)

    decoded = CompositeCursor.from_string(cursor.to_string())

    assert decoded.sort_value == "2025-11-16 10:30:00"
    assert decoded.id_value == 42


def test_composite_cursor_roundtrip_with_null_sort_value():
    cursor = CompositeCursor(sort_value=None, id_value=7)

    decoded = CompositeCursor.from_string(cursor.to_string())

    assert decoded.sort_value is None
    assert decoded.id_value == 7


def test_composite_cursor_from_string_invalid_base64_raises_value_error():
    with pytest.raises(ValueError):
        CompositeCursor.from_string("$$$ isso nao e base64 valido $$$")


def test_composite_cursor_from_string_missing_id_raises_value_error():
    import base64
    import json

    malformed = base64.urlsafe_b64encode(json.dumps({"t": "2025-01-01"}).encode()).decode()

    with pytest.raises(ValueError):
        CompositeCursor.from_string(malformed)


def test_composite_cursor_handles_missing_base64_padding():
    """Cursors em query string às vezes chegam sem o padding '=' do
    base64 (removido por alguns clientes HTTP) - o decode precisa
    tolerar isso, mesmo padrão já usado em CompositeCursor/
    DriverReportCompositeCursor em app/schemas/history.py e
    driver_reports.py."""
    cursor = CompositeCursor(sort_value="2025-11-16 10:30:00", id_value=1)
    stripped = cursor.to_string().rstrip("=")

    decoded = CompositeCursor.from_string(stripped)

    assert decoded.sort_value == "2025-11-16 10:30:00"
    assert decoded.id_value == 1


# ---------------------------------------------------------------------------
# build_next_cursor
# ---------------------------------------------------------------------------

def test_build_next_cursor_with_datetime():
    cursor_str = build_next_cursor(datetime(2025, 11, 16, 10, 30, 0), id_value=5)

    decoded = CompositeCursor.from_string(cursor_str)
    assert decoded.sort_value == "2025-11-16 10:30:00"
    assert decoded.id_value == 5


def test_build_next_cursor_with_none():
    cursor_str = build_next_cursor(None, id_value=9)

    decoded = CompositeCursor.from_string(cursor_str)
    assert decoded.sort_value is None
    assert decoded.id_value == 9


# ---------------------------------------------------------------------------
# apply_keyset_pagination - forma do SQL gerado
# ---------------------------------------------------------------------------

_Base = declarative_base()


class _FakeEntity(_Base):
    __tablename__ = "fake_entity"
    id = Column(Integer, primary_key=True)
    sort_col = Column(DateTime, nullable=True)


def test_apply_keyset_pagination_without_cursor_only_orders():
    """Sem cursor (primeira página), só o ORDER BY deve ser aplicado -
    nenhum WHERE adicional."""
    query = apply_keyset_pagination(
        select(_FakeEntity), _FakeEntity.sort_col, _FakeEntity.id, cursor=None
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "ORDER BY fake_entity.sort_col DESC NULLS LAST, fake_entity.id DESC" in compiled
    assert "WHERE" not in compiled


def test_apply_keyset_pagination_with_non_null_cursor():
    cursor = CompositeCursor(sort_value="2025-11-16 10:30:00", id_value=42)
    query = apply_keyset_pagination(
        select(_FakeEntity), _FakeEntity.sort_col, _FakeEntity.id, cursor=cursor
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    # Deve incluir as 3 condições da keyset pagination combinadas com OR:
    # NULLs (sempre elegíveis, pois vêm depois de qualquer valor não-nulo),
    # valores estritamente menores, e empate exato desempatado pelo id.
    assert "fake_entity.sort_col IS NULL" in compiled
    assert "fake_entity.sort_col <" in compiled
    assert "fake_entity.sort_col = " in compiled
    assert "fake_entity.id <" in compiled


def test_apply_keyset_pagination_with_null_cursor():
    """Quando o cursor está no grupo dos NULLs, a próxima página só pode
    conter outras linhas NULL com id menor - não deve comparar contra
    nenhum valor de data."""
    cursor = CompositeCursor(sort_value=None, id_value=42)
    query = apply_keyset_pagination(
        select(_FakeEntity), _FakeEntity.sort_col, _FakeEntity.id, cursor=cursor
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "fake_entity.sort_col IS NULL" in compiled
    assert "fake_entity.id <" in compiled
    # Não deve haver comparação "menor que" contra a coluna de data (só
    # contra id) - só o IS NULL puro.
    assert "fake_entity.sort_col <" not in compiled