"""Schemas da saúde da frota."""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class UnidadeNaoSaudavel(BaseModel):
    """Unidade que disparou alguma condição da cascata."""

    unit_id: int
    label: Optional[str] = None
    #: Número da categoria que classificou a unidade.
    categoria: int
    #: Texto exibido ao usuário, já pronto.
    motivo: str
    #: Valor que disparou a condição, para o gestor conferir sem abrir outra
    #: tela — "verificar inércia" sem o número não diz o que verificar.
    valor: Optional[float] = None


class FleetHealthResponse(BaseModel):
    """
    Saúde da frota no último dia com dado.

    Mede qualidade de sinal e comportamento de condução, **não** manutenção:
    é a fração de unidades que não dispararam nenhuma condição de alerta.
    """

    #: Dia efetivamente avaliado. Nunca é hoje — a tabela consolidada não tem
    #: linha do dia corrente.
    referencia: Optional[date] = None
    total_unidades: int
    unidades_saudaveis: int
    unidades_nao_saudaveis: int
    #: Percentual saudável. Nulo sem unidade avaliável, porque zero seria lido
    #: como frota inteira com problema.
    percentual_saudavel: Optional[float] = None
    #: Contagem por motivo, do mais frequente ao menos.
    por_motivo: dict[str, int]
    #: As unidades, para a tela listar quem precisa de atenção.
    nao_saudaveis: list[UnidadeNaoSaudavel]
