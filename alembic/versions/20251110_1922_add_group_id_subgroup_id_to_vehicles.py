"""add_group_id_subgroup_id_to_vehicles

Revision ID: 1b61341607cd
Revises: (none - primeira migration da cadeia)
Create Date: 2025-11-10 19:22:17.664378-03:00

NO-OP A PARTIR DE 2026-07-27 (F2-02):

Esta migration originalmente alterava uma tabela `fleet_vehicles`, que
nunca teve um `CREATE TABLE` em nenhuma migration - ou seja, dependia de
a tabela ja existir previamente no banco de destino. Isso quebrava
`alembic upgrade head` em qualquer ambiente novo/vazio (staging, CI,
disaster recovery), com erro `relation "fleet_vehicles" does not exist`.

Confirmado com o time (dev leader) que `fleet_vehicles` e uma tabela sem
uso atual: nenhum model SQLAlchemy, nenhum endpoint, nenhuma referencia
em `app/`. O model `Vehicle` real usa `mova.tracked_unit`.

Em vez de apagar este arquivo (o que exigiria re-encadear a proxima
migration e poderia confundir qualquer ambiente que ja tenha esta
revisao registrada em `alembic_version`), o corpo foi esvaziado para
um no-op. O ID da revisao (`1b61341607cd`) e mantido intacto na cadeia.

Producao nao e afetada: como a tabela `fleet_vehicles` la ja tinha essas
colunas alteradas ha muito tempo (a migration original ja rodou nela no
passado), nao ha nada a refazer.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b61341607cd'
down_revision = None  # First migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op deliberado - ver docstring acima (F2-02).
    pass


def downgrade() -> None:
    # No-op deliberado - ver docstring acima (F2-02).
    pass