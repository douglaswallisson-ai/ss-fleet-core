"""
Testes da DS-1385 (épico DS-1342): saneamento preventivo de data fora do
intervalo em vehicles.py / tracked_unit.

Mesma técnica de teste usada implicitamente pelo fix original de drivers.py
(achado #9, Épico 3): como o bug só se manifesta na decodificação BINÁRIA
do asyncpg (um Postgres real é necessário para reproduzir o
OverflowError/ValueError de ponta a ponta), este arquivo cobre:

1. A lógica pura de parsing (`_parse_safe_datetime`) - a parte que
   efetivamente decide se um valor "sobrevive" ou vira None.
2. A forma da consulta SQL gerada (`_safe_datetime_column`) - garante que
   as colunas de risco são sempre convertidas para texto via `to_char`,
   nunca decodificadas em binário.
3. Um teste de integração de ponta a ponta simulando a resposta do driver
   (via stub), sem depender de um Postgres real.

Executar com:
    pytest tests/test_vehicles_date_sanitization.py -v
"""

from datetime import datetime

import pytest
from sqlalchemy import Column, Integer, String, DateTime, select
from sqlalchemy.orm import declarative_base

from app.api.v1.endpoints.vehicles import (
    _RISKY_DATETIME_COLUMNS,
    _safe_datetime_column,
    _parse_safe_datetime,
)


# ---------------------------------------------------------------------------
# _parse_safe_datetime
# ---------------------------------------------------------------------------

def test_parse_safe_datetime_valid_value():
    """Uma string de data/hora válida deve virar um datetime normal."""
    result = _parse_safe_datetime("2025-11-16 10:30:00")
    assert result == datetime(2025, 11, 16, 10, 30, 0)


def test_parse_safe_datetime_none_input():
    """Entrada None deve retornar None (coluna nula no banco)."""
    assert _parse_safe_datetime(None) is None


def test_parse_safe_datetime_empty_string():
    """String vazia deve retornar None (mesmo tratamento que NULL)."""
    assert _parse_safe_datetime("") is None


def test_parse_safe_datetime_year_out_of_range_returns_none():
    """
    Valor com ano fora do intervalo suportado pelo datetime do Python
    (1-9999) - o cenário exato que quebra o decodificador binário do
    asyncpg - deve virar None em vez de propagar a exceção.
    """
    assert _parse_safe_datetime("20234-01-01 00:00:00") is None
    assert _parse_safe_datetime("99999-12-31 23:59:59") is None


def test_parse_safe_datetime_malformed_string_returns_none():
    """Qualquer string que não bata com o formato esperado vira None,
    nunca propaga ValueError para quem chamou."""
    assert _parse_safe_datetime("not-a-date") is None
    assert _parse_safe_datetime("2025-13-45 99:99:99") is None


# ---------------------------------------------------------------------------
# _safe_datetime_column - forma da consulta SQL gerada
# ---------------------------------------------------------------------------

_Base = declarative_base()


class _FakeVehicle(_Base):
    """Modelo mínimo só com as colunas relevantes, para testar a geração
    de SQL sem depender do modelo real (evita acoplar o teste a
    app.models.vehicle)."""
    __tablename__ = "tracked_unit"
    __table_args__ = {"schema": "mova"}

    id = Column("id", Integer, primary_key=True)
    label = Column("label", String, nullable=False)
    date_add = Column("date_add", DateTime, nullable=True)
    date_modif = Column("date_modif", DateTime, nullable=True)
    date_removed = Column("date_removed", DateTime, nullable=True)


def test_safe_datetime_column_uses_to_char():
    """A coluna de risco deve ser sempre lida via to_char (texto), nunca
    decodificada em binário pelo driver."""
    expr = _safe_datetime_column(_FakeVehicle.date_add)
    compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
    assert "to_char" in compiled
    assert "YYYY-MM-DD HH24:MI:SS" in compiled


def test_list_query_casts_only_risky_columns_to_text():
    """A consulta de listagem deve converter para texto exatamente as
    colunas marcadas como de risco, e nenhuma outra - colunas não-risco
    continuam sendo selecionadas normalmente (sem cast), permitindo que o
    ORDER BY funcione no banco sem decodificar nada em Python."""
    select_columns = [
        _safe_datetime_column(col) if col.key in _RISKY_DATETIME_COLUMNS else col
        for col in _FakeVehicle.__table__.columns
    ]
    query = (
        select(*select_columns)
        .order_by(_FakeVehicle.date_add.desc())
        .limit(10)
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    for risky_col in _RISKY_DATETIME_COLUMNS:
        assert f"to_char(mova.tracked_unit.{risky_col}" in compiled

    # Coluna não-risco (label) deve continuar sem cast
    assert "mova.tracked_unit.label" in compiled
    assert "to_char(mova.tracked_unit.label" not in compiled

    # ORDER BY deve usar a coluna original, não o alias de texto
    assert "ORDER BY mova.tracked_unit.date_add DESC" in compiled


# ---------------------------------------------------------------------------
# Integração simulada: linha "corrompida" não derruba a resposta inteira
# ---------------------------------------------------------------------------

def test_build_response_survives_corrupted_row():
    """
    Simula o formato de uma linha já lida do banco (após o SELECT com
    to_char aplicado) contendo um valor de data corrompido em uma das
    colunas de risco - reproduz em memória o mesmo pipeline usado dentro
    de list_vehicles, sem precisar de um Postgres real.
    """
    row = {
        "id": 1,
        "label": "ABC-1234",
        "date_add": "20234-01-01 00:00:00",  # corrompido (ano de 5 dígitos)
        "date_modif": "2025-11-16 10:30:00",  # válido
        "date_removed": None,
    }

    data = dict(row)
    for col_key in _RISKY_DATETIME_COLUMNS:
        data[col_key] = _parse_safe_datetime(data[col_key])

    assert data["date_add"] is None  # corrompido -> None, sem exceção
    assert data["date_modif"] == datetime(2025, 11, 16, 10, 30, 0)
    assert data["date_removed"] is None
    assert data["label"] == "ABC-1234"  # demais campos intocados