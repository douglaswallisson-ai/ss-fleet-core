"""
Utilitários de paginação por cursor (keyset pagination).

DS-1379 (épico DS-1342): extrai o padrão de cursor composto já usado nos
endpoints de relatório (ver app/schemas/history.py:CompositeCursor,
app/schemas/driver_reports.py:DriverReportCompositeCursor) para um lugar
único, reaproveitável por qualquer endpoint de listagem que precise de
paginação real (sem o custo de OFFSET crescendo com o tamanho da página).

Por que keyset pagination em vez de OFFSET/LIMIT:
- OFFSET/LIMIT faz o banco escanear e descartar `offset` linhas a cada
  página - o custo cresce linearmente com a profundidade da página.
- Keyset pagination usa uma condição WHERE baseada no último valor visto
  (`WHERE (coluna_ordenacao, id) < (ultimo_valor, ultimo_id)`), que o
  Postgres resolve usando o índice diretamente, com custo praticamente
  constante independente da página.

Requisito para usar esta paginação: a query precisa de uma coluna de
ordenação (pode ser NULL) mais uma coluna de desempate única (id) para
garantir uma ordem total determinística - sem isso, linhas com o mesmo
valor na coluna de ordenação podem ser puladas ou repetidas entre páginas.
"""

import base64
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, or_


class CompositeCursor:
    """
    Cursor composto genérico: (valor da coluna de ordenação, id de desempate).

    Serializado como JSON compacto e codificado em base64 URL-safe, no
    mesmo formato já usado pelos cursors de relatório (`{"t": ..., "i": ...}`).

    O valor de ordenação (`sort_value`) é sempre armazenado como string
    ISO (`YYYY-MM-DD HH:MM:SS`) ou `None` (linha cuja coluna de ordenação
    é NULL no banco).
    """

    def __init__(self, sort_value: Optional[str], id_value: int):
        self.sort_value = sort_value
        self.id_value = id_value

    def to_string(self) -> str:
        """Codifica o cursor como string base64 URL-safe."""
        cursor_dict = {"t": self.sort_value, "i": self.id_value}
        return base64.urlsafe_b64encode(json.dumps(cursor_dict).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> "CompositeCursor":
        """
        Decodifica um cursor a partir da string base64.

        Raises:
            ValueError: cursor malformado (base64/JSON inválido ou faltando
                a chave "i"). O chamador deve tratar isso como HTTP 400.
        """
        missing_padding = len(cursor_str) % 4
        if missing_padding:
            cursor_str += "=" * (4 - missing_padding)
        try:
            cursor_dict = json.loads(base64.urlsafe_b64decode(cursor_str.encode()).decode())
            return cls(sort_value=cursor_dict.get("t"), id_value=cursor_dict["i"])
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cursor invalido: {cursor_str!r}") from exc


def apply_keyset_pagination(
    query,
    sort_column,
    id_column,
    cursor: Optional[CompositeCursor],
    datetime_format: str = "%Y-%m-%d %H:%M:%S",
):
    """
    Aplica ORDER BY e (se houver cursor) o WHERE de keyset pagination a
    uma query SQLAlchemy Core/ORM.

    Ordenação: `sort_column DESC NULLS LAST, id_column DESC` - NULLS LAST
    explícito (em vez de depender do padrão do Postgres, que é NULLS
    FIRST para DESC) simplifica o raciocínio: linhas com sort_column NULL
    sempre aparecem por último, então uma vez que o cursor "entra" no
    grupo dos NULLs, só precisa comparar por id.

    Args:
        query: Select statement do SQLAlchemy (Core ou ORM).
        sort_column: Coluna de ordenação primária (pode ser nullable).
        id_column: Coluna de desempate, deve ser única (chave primária).
        cursor: Cursor da página anterior, ou None para a primeira página.
        datetime_format: Formato usado para interpretar `cursor.sort_value`
            de volta como `datetime` (default: timestamp completo).

    Returns:
        Query com ORDER BY e WHERE (quando aplicável) já aplicados.
    """
    query = query.order_by(sort_column.desc().nullslast(), id_column.desc())

    if cursor is None:
        return query

    if cursor.sort_value is not None:
        cursor_dt = datetime.strptime(cursor.sort_value, datetime_format)
        query = query.where(
            or_(
                sort_column.is_(None),
                sort_column < cursor_dt,
                and_(sort_column == cursor_dt, id_column < cursor.id_value),
            )
        )
    else:
        # Cursor já está no grupo das linhas com sort_column NULL (o
        # último grupo, por causa do NULLS LAST) - só resta desempatar por id.
        query = query.where(and_(sort_column.is_(None), id_column < cursor.id_value))

    return query


def build_next_cursor(sort_value: Optional[datetime], id_value: int) -> str:
    """
    Monta a string de cursor para o último registro de uma página, pronta
    para ser devolvida como `next_cursor` na resposta.

    Args:
        sort_value: Valor da coluna de ordenação do último registro da
            página atual (`datetime` ou `None`).
        id_value: id do último registro da página atual.
    """
    sort_str = sort_value.strftime("%Y-%m-%d %H:%M:%S") if sort_value else None
    return CompositeCursor(sort_value=sort_str, id_value=id_value).to_string()