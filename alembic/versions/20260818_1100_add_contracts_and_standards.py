"""Contratos comerciais, padrão por linha e multas

Segunda parte dos módulos que só existiam no front. Mesmo critério da anterior:
prefixo por domínio (`ctr_`, `pdr_`, `inf_`) para separar do legado.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

SCHEMA = "mova"


def upgrade() -> None:
    # ---------------------------------------------------------------- #
    # Contratos comerciais                                              #
    # ---------------------------------------------------------------- #

    # `ctr_contrato`, `ctr_contrato_modalidade` e `ctr_contrato_usuario` foram
    # descartadas.
    #
    # `mova.contract` já existe, com 103 contratos em seis grupos, e é mais
    # completa do que a minha proposta em pontos que importam: índice de
    # reajuste, primeira cobrança, tipo de contrato, segmento e hierarquia por
    # `parent`.
    #
    # Criar uma tabela paralela levaria a contratos cadastrados em dois lugares,
    # divergindo — e ninguém perceberia até a cobrança sair errada.
    #
    # Do que eu havia proposto, só o aditivo não existe. É o que fica.

    op.create_table(
        "ctr_aditivo",
        sa.Column("id", sa.Integer, primary_key=True),
        # Aponta para o contrato existente, não para um cadastro próprio.
        sa.Column("contract_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.contract.id", ondelete="CASCADE"), nullable=False),
        sa.Column("numero", sa.String(40), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("assinado_em", sa.Date, nullable=False),
        sa.Column("novo_termino", sa.Date),
        sa.Column("descricao", sa.Text),
        sa.Column("registrado_por", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- #
    # Padrão de condução por linha                                      #
    # ---------------------------------------------------------------- #

    op.create_table(
        "pdr_faixa_horaria",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("nome", sa.String(60), nullable=False),
        sa.Column("hora_inicio", sa.Time, nullable=False),
        sa.Column("hora_fim", sa.Time, nullable=False),
        sa.Column("ordem", sa.Integer, server_default="0"),
        schema=SCHEMA,
    )

    # Complementa `mova.driver_scoring`, que já pontua cada viagem com peso por
    # indicador — inclusive excesso de velocidade sob chuva, separado do seco.
    #
    # O que falta lá é o **esperado**: a nota é absoluta, sem comparação contra
    # o padrão da linha e da faixa horária. É justamente o que torna a avaliação
    # justa entre um motorista de corredor e um de bairro, e é o que estas duas
    # tabelas trazem.
    op.create_table(
        "pdr_padrao_linha",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        # Nulo nos dois significa padrão global da conta. Linha sem faixa vale
        # para a linha inteira. É a herança em três níveis: faixa, linha, conta.
        sa.Column("buss_line_id", sa.Integer),
        sa.Column("faixa_horaria_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.pdr_faixa_horaria.id")),
        sa.Column("indicador", sa.String(60), nullable=False),
        sa.Column("valor_esperado", sa.Numeric(12, 3), nullable=False),
        # De onde veio o número: definido à mão, sugerido do histórico ou
        # herdado. Sem isso o gestor não sabe se pode confiar no valor.
        sa.Column("origem", sa.String(30), server_default="manual"),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_por", sa.Integer),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_pdr_padrao",
        "pdr_padrao_linha",
        ["account_id", "buss_line_id", "faixa_horaria_id", "indicador"],
        schema=SCHEMA,
    )
    op.create_index("ix_pdr_padrao_linha", "pdr_padrao_linha", ["buss_line_id"], schema=SCHEMA)

    # ---------------------------------------------------------------- #
    # Multas                                                            #
    # ---------------------------------------------------------------- #

    op.create_table(
        "inf_multa",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ait", sa.String(40), nullable=False),
        sa.Column("unit_id", sa.Integer, nullable=False),
        sa.Column("driver_id", sa.Integer),
        sa.Column("infracao", sa.String(240)),
        sa.Column("codigo_ctb", sa.String(20)),
        sa.Column("gravidade", sa.String(20)),
        sa.Column("pontos", sa.Integer),
        sa.Column("valor", sa.Numeric(12, 2)),
        sa.Column("cometida_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local", sa.Text),
        sa.Column("orgao", sa.String(80)),
        # Prazo legal para indicar o condutor. Perder essa data faz a multa
        # recair sobre a empresa, com valor multiplicado — é o campo que mais
        # justifica o módulo existir.
        sa.Column("prazo_indicacao", sa.Date),
        sa.Column("indicado_em", sa.Date),
        sa.Column("status", sa.String(30), nullable=False, server_default="pendente"),
        sa.Column("observacao", sa.Text),
        sa.Column("group_id", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_inf_multa_unit", "inf_multa", ["unit_id"], schema=SCHEMA)
    op.create_index("ix_inf_multa_prazo", "inf_multa", ["prazo_indicacao", "status"], schema=SCHEMA)
    op.create_unique_constraint("uq_inf_multa_ait", "inf_multa", ["ait"], schema=SCHEMA)


def downgrade() -> None:
    for tabela in (
        "inf_multa",
        "pdr_padrao_linha",
        "pdr_faixa_horaria",
        "ctr_aditivo",
    ):
        op.drop_table(tabela, schema=SCHEMA)
