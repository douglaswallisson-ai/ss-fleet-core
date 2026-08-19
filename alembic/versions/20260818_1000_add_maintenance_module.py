"""Módulo de manutenção: catálogo do fabricante, plano preventivo e ordens

Cria no schema `mova` as tabelas dos módulos que hoje só existem no front,
gravando em sessão do navegador — o dado some ao fechar a aba.

Todas usam o prefixo `mnt_` para ficar evidente, em qualquer listagem do banco,
o que veio deste módulo e o que é do sistema legado. Sem o prefixo, daqui a dois
anos ninguém distingue.

O vínculo com veículo é por `unit_id`, referenciando `mova.tracked_unit` — não
se cria um cadastro paralelo de frota.

Revision ID: a1b2c3d4e5f6
Revises: 9f2c7a5e1d3b
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "9f2c7a5e1d3b"
branch_labels = None
depends_on = None

SCHEMA = "mova"


def upgrade() -> None:
    # ---------------------------------------------------------------- #
    # Catálogo do fabricante                                            #
    # ---------------------------------------------------------------- #

    # `mnt_montadora` e `mnt_modelo` foram descartados: `mova.vehicle_manufacturer`
    # e `mova.vehicle_model` já existem, com 52 fabricantes e 576 modelos em uso.
    # Criar um catálogo paralelo faria o parâmetro de manutenção apontar para um
    # modelo diferente do que o veículo referencia, e ninguém notaria até os
    # números não baterem.

    op.create_table(
        "mnt_parametro",
        sa.Column("id", sa.Integer, primary_key=True),
        # Referencia o catálogo existente, não um modelo próprio.
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.vehicle_model.id"), nullable=False),
        sa.Column("sistema", sa.String(60), nullable=False),
        sa.Column("item", sa.String(160), nullable=False),
        sa.Column("acao", sa.String(40), nullable=False),
        sa.Column("especificacao", sa.Text),
        # Os três gatilhos são independentes e o disparo é o que ocorrer
        # primeiro. Nulo significa que aquele gatilho não se aplica — diferente
        # de zero, que significaria vencimento imediato.
        sa.Column("intervalo_km", sa.Integer),
        sa.Column("intervalo_horas", sa.Integer),
        sa.Column("intervalo_meses", sa.Integer),
        # Parâmetro que vale só para um tipo de operação. Nulo vale para todos.
        sa.Column("tipo_operacao", sa.String(20)),
        # Origem do dado: manual do fabricante, concessionária ou estimativa.
        # Registrado para o gestor saber o quanto confiar no número.
        sa.Column("origem", sa.String(40), server_default="manual_fabricante"),
        sa.Column("status_dado", sa.String(20), server_default="confirmado"),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_mnt_parametro_modelo", "mnt_parametro", ["vehicle_model_id"], schema=SCHEMA)

    op.create_table(
        "mnt_regra_ajuste",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("manufacturer_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.vehicle_manufacturer.id")),
        sa.Column("nome", sa.String(120), nullable=False),
        # Sinal de telemetria observado, ex.: parado_motor_ligado.
        sa.Column("indicador", sa.String(60), nullable=False),
        sa.Column("operador", sa.String(20), nullable=False),
        sa.Column("limiar", sa.Numeric(10, 2), nullable=False),
        # Multiplicador do intervalo. 0.7 encurta em 30%.
        sa.Column("fator", sa.Numeric(4, 2), nullable=False),
        sa.Column("justificativa", sa.Text),
        sa.Column("ativa", sa.Boolean, nullable=False, server_default=sa.true()),
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- #
    # Vínculo do veículo com o catálogo                                 #
    # ---------------------------------------------------------------- #

    # `mnt_veiculo_modelo` descartada: `mova.tracked_unit` já tem
    # `vehicle_model_id`. Um segundo vínculo permitiria que o veículo apontasse
    # para um modelo no cadastro e outro na manutenção.

    # ---------------------------------------------------------------- #
    # Execução: o que já foi feito                                      #
    # ---------------------------------------------------------------- #

    op.create_table(
        "mnt_execucao",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("unit_id", sa.Integer, nullable=False),
        sa.Column("parametro_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.mnt_parametro.id"), nullable=False),
        sa.Column("executado_em", sa.DateTime(timezone=True), nullable=False),
        # Marcos no momento da execução: é a partir deles que se calcula o
        # próximo vencimento.
        sa.Column("odometro", sa.Integer, nullable=False),
        sa.Column("horimetro", sa.Numeric(10, 1)),
        sa.Column("ordem_servico_id", sa.Integer),
        sa.Column("observacao", sa.Text),
        sa.Column("registrado_por", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    # Índice pela consulta mais frequente: última execução de um item num
    # veículo. Sem ele, o cálculo do plano varre a tabela inteira por veículo.
    op.create_index(
        "ix_mnt_execucao_unit_param",
        "mnt_execucao",
        ["unit_id", "parametro_id", "executado_em"],
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- #
    # Ordens de serviço                                                 #
    # ---------------------------------------------------------------- #

    # Não confundir com `mova.device_maintenance`, que registra manutenção do
    # *rastreador* — se o GPS, o GSM e o CAN do equipamento estão funcionando.
    # Esta é a ordem de serviço do veículo, que não existe no banco.
    op.create_table(
        "mnt_ordem_servico",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("numero", sa.String(40), nullable=False, unique=True),
        sa.Column("unit_id", sa.Integer, nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("origem", sa.String(20), server_default="manual"),
        sa.Column("status", sa.String(30), nullable=False, server_default="aberta"),
        sa.Column("descricao", sa.Text),
        sa.Column("oficina", sa.String(120)),
        sa.Column("interna", sa.Boolean, server_default=sa.true()),
        sa.Column("odometro", sa.Integer),
        sa.Column("parametro_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.mnt_parametro.id")),
        sa.Column("custo_previsto", sa.Numeric(12, 2)),
        sa.Column("custo_real", sa.Numeric(12, 2)),
        sa.Column("aberta_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("iniciada_em", sa.DateTime(timezone=True)),
        sa.Column("concluida_em", sa.DateTime(timezone=True)),
        sa.Column("aberta_por", sa.Integer),
        sa.Column("group_id", sa.Integer),
        sa.Column("subgroup_id", sa.Integer),
        schema=SCHEMA,
    )
    op.create_index("ix_mnt_os_unit_status", "mnt_ordem_servico", ["unit_id", "status"], schema=SCHEMA)
    op.create_index("ix_mnt_os_group", "mnt_ordem_servico", ["group_id"], schema=SCHEMA)

    op.create_table(
        "mnt_ordem_item",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ordem_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.mnt_ordem_servico.id", ondelete="CASCADE"), nullable=False),
        sa.Column("descricao", sa.String(240), nullable=False),
        sa.Column("tipo", sa.String(20), server_default="servico"),
        sa.Column("quantidade", sa.Numeric(10, 2), server_default="1"),
        sa.Column("valor_unitario", sa.Numeric(12, 2)),
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- #
    # Pneus                                                             #
    # ---------------------------------------------------------------- #

    op.create_table(
        "mnt_pneu",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("numero_fogo", sa.String(40), nullable=False, unique=True),
        sa.Column("marca", sa.String(60)),
        sa.Column("medida", sa.String(40)),
        sa.Column("modelo", sa.String(80)),
        sa.Column("dot", sa.String(20)),
        # Posição no veículo em notação de eixo, ex.: 1E, 2DI. Nulo significa
        # em estoque.
        sa.Column("unit_id", sa.Integer),
        sa.Column("posicao", sa.String(10)),
        sa.Column("status", sa.String(30), nullable=False, server_default="estoque"),
        sa.Column("sulco_mm", sa.Numeric(5, 2)),
        sa.Column("pressao_psi", sa.Integer),
        sa.Column("km_instalacao", sa.Integer),
        sa.Column("km_acumulado", sa.Integer, server_default="0"),
        sa.Column("vida", sa.Integer, server_default="1"),
        sa.Column("custo", sa.Numeric(12, 2)),
        sa.Column("group_id", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_mnt_pneu_unit", "mnt_pneu", ["unit_id", "posicao"], schema=SCHEMA)

    op.create_table(
        "mnt_pneu_movimento",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pneu_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.mnt_pneu.id"), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("unit_id_origem", sa.Integer),
        sa.Column("posicao_origem", sa.String(10)),
        sa.Column("unit_id_destino", sa.Integer),
        sa.Column("posicao_destino", sa.String(10)),
        sa.Column("odometro", sa.Integer),
        sa.Column("sulco_mm", sa.Numeric(5, 2)),
        sa.Column("observacao", sa.Text),
        sa.Column("em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("registrado_por", sa.Integer),
        schema=SCHEMA,
    )
    op.create_index("ix_mnt_pneu_mov_pneu", "mnt_pneu_movimento", ["pneu_id", "em"], schema=SCHEMA)

    # ---------------------------------------------------------------- #
    # Códigos de falha                                                  #
    # ---------------------------------------------------------------- #

    op.create_table(
        "mnt_dtc",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("unit_id", sa.Integer, nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        # SPN identifica o componente e FMI o tipo de falha, no padrão J1939.
        # Guardados separados para agrupar por componente mesmo quando a
        # natureza da falha muda.
        sa.Column("spn", sa.Integer),
        sa.Column("fmi", sa.Integer),
        sa.Column("sistema", sa.String(60)),
        sa.Column("descricao", sa.Text),
        sa.Column("severidade", sa.String(20), server_default="atencao"),
        sa.Column("primeira_ocorrencia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultima_ocorrencia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ocorrencias", sa.Integer, server_default="1"),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("lampada_acesa", sa.Boolean, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.create_index("ix_mnt_dtc_unit_ativo", "mnt_dtc", ["unit_id", "ativo"], schema=SCHEMA)
    op.create_unique_constraint(
        "uq_mnt_dtc_unit_codigo", "mnt_dtc", ["unit_id", "codigo", "primeira_ocorrencia"], schema=SCHEMA
    )


def downgrade() -> None:
    # Ordem inversa da criação, respeitando as chaves estrangeiras.
    for tabela in (
        "mnt_dtc",
        "mnt_pneu_movimento",
        "mnt_pneu",
        "mnt_ordem_item",
        "mnt_ordem_servico",
        "mnt_execucao",
        "mnt_regra_ajuste",
        "mnt_parametro",
    ):
        op.drop_table(tabela, schema=SCHEMA)
