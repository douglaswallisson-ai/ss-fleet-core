"""Schemas do ranking de motoristas (Pontuação do Power BI)."""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class FaixasMotorista(BaseModel):
    """Percentual de cada faixa sobre a soma das 13 faixas (0 a 100)."""

    verde: Optional[float] = None
    extra_economica: Optional[float] = None
    inercia: Optional[float] = None
    eco_roll: Optional[float] = None
    baixa_velocidade: Optional[float] = None
    amarela: Optional[float] = None
    vermelha: Optional[float] = None
    #: `time_blue`. O nome da coluna não diz, mas é batendo transmissão.
    batendo_transmissao: Optional[float] = None
    #: `time_banguela`.
    movimento_sem_tracao: Optional[float] = None
    parado_acelerando: Optional[float] = None
    #: Inclui o parado produtivo, como no Power BI.
    parado_ligado: Optional[float] = None
    #: A parte produtiva do parado ligado, para quem precisa separar.
    parado_produtivo: Optional[float] = None
    tolerancia: Optional[float] = None


class EventosPorHora(BaseModel):
    aceleracao_brusca: Optional[float] = None
    freada_brusca: Optional[float] = None
    velocidade_excessiva: Optional[float] = None
    embreagem: Optional[float] = None


class EventosQtd(BaseModel):
    """Quantidades no período (Power BI, Análise de Condução QTD)."""

    aceleracao_brusca: int = 0
    freada_brusca: int = 0
    velocidade_excessiva: int = 0
    velocidade_chuva: int = 0
    embreagem: int = 0


class MotoristaRanking(BaseModel):
    posicao: Optional[int] = None
    driver_id: int
    nome: Optional[str] = None
    cnh_validade: Optional[date] = None
    cnh_numero: Optional[str] = None
    cnh_categoria: Optional[str] = None
    km: float
    horas: float
    litros: float
    #: Km com combustível > 0 ÷ litros (Power BI, P8). Nulo sem combustível.
    kml: Optional[float] = None
    pontuacao: Optional[float] = None
    #: 5 a 0, pelos cortes 90/80/70/60/50.
    estrelas: int
    faixas: FaixasMotorista
    eventos_por_hora: EventosPorHora
    eventos: EventosQtd = EventosQtd()
    #: Sem linha de faixa no período: a nota não sai, em vez de sair zero.
    sem_faixas: bool


class ResumoRanking(BaseModel):
    motoristas: int
    km_total: float
    horas_total: float
    nota_media: Optional[float] = None
    #: Horas sem condutor identificado ÷ horas trabalhadas (0 a 100).
    pct_horas_nao_identificado: Optional[float] = None
    #: Sem peso cadastrado em Metas e Pesos a nota não tem como sair.
    pesos_cadastrados: bool


class RankingMotoristasResponse(BaseModel):
    inicio: date
    fim: date
    resumo: ResumoRanking
    motoristas: list[MotoristaRanking]
