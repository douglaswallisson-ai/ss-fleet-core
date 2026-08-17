"""add_api_permissions_system

Revision ID: 2f8a9c4b5d1e
Revises: 1b61341607cd
Create Date: 2025-01-18 17:00:00.000000-03:00

CORRIGIDO EM 2026-07-27 (F2-02):

Esta migration originalmente criava as tabelas api_* sem qualificar
schema (op.create_table('api_resources', ...) sem schema='mova'), e as
FKs apontavam para 'users.id' (tambem sem schema). Isso "funcionava" em
producao porque o search_path da conexao resolve nomes sem prefixo para
`mova` primeiro - mas quebrava `alembic upgrade head` em qualquer banco
novo/vazio (staging, CI), com erro `relation "users" does not exist`.

Corrigido para qualificar tudo explicitamente com schema='mova', e para
garantir que mova.users exista (via CREATE TABLE IF NOT EXISTS mínimo,
so' com a coluna id) antes da FK de api_user_permissions - ja' que o
baseline completo de mova.users (migration 9f2c7a5e1d3b) so' roda depois
desta na cadeia. O baseline usa ALTER TABLE ADD COLUMN IF NOT EXISTS
para mova.users especificamente, entao completa as colunas que faltarem
sem conflitar com este stub minimo.

Producao nao e afetada: mova.users e as tabelas api_* ja existem la'.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2f8a9c4b5d1e'
down_revision = '1b61341607cd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mova;")

    # Stub minimo de mova.users - o baseline (9f2c7a5e1d3b) completa as
    # demais colunas via ADD COLUMN IF NOT EXISTS, sem conflitar com isto.
    op.execute('CREATE TABLE IF NOT EXISTS mova.users ("id" INTEGER PRIMARY KEY);')

    # ========================================
    # 1. Create api_resources table
    # ========================================
    op.create_table(
        'api_resources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('resource_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resource_name'),
        schema='mova',
    )
    op.create_index('idx_api_resources_active', 'api_resources', ['is_active'], schema='mova')

    # ========================================
    # 2. Create api_actions table
    # ========================================
    op.create_table(
        'api_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('action_name', sa.String(length=20), nullable=False),
        sa.Column('http_methods', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('action_name'),
        schema='mova',
    )

    # ========================================
    # 3. Create api_permissions table
    # ========================================
    op.create_table(
        'api_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=False),
        sa.Column('action_id', sa.Integer(), nullable=False),
        sa.Column('permission_key', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['resource_id'], ['mova.api_resources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['action_id'], ['mova.api_actions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resource_id', 'action_id', name='unique_resource_action'),
        sa.UniqueConstraint('permission_key'),
        schema='mova',
    )
    op.create_index('idx_api_permissions_key', 'api_permissions', ['permission_key'], schema='mova')
    op.create_index('idx_api_permissions_active', 'api_permissions', ['is_active'], schema='mova')

    # ========================================
    # 4. Create api_user_permissions table
    # ========================================
    op.create_table(
        'api_user_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('revoked_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['mova.users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['permission_id'], ['mova.api_permissions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['mova.users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['mova.users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'permission_id', name='unique_user_permission'),
        schema='mova',
    )
    op.create_index('idx_api_user_permissions_user', 'api_user_permissions', ['user_id'], schema='mova')
    op.create_index('idx_api_user_permissions_granted', 'api_user_permissions', ['user_id', 'granted'], schema='mova')
    op.create_index('idx_api_user_permissions_composite', 'api_user_permissions', ['user_id', 'permission_id', 'granted'], schema='mova')

    # ========================================
    # 5. Create api_user_permissions_history table
    # ========================================
    op.create_table(
        'api_user_permissions_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('permission_key', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False),
        sa.Column('changed_by', sa.Integer(), nullable=False),
        sa.Column('changed_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('user_email', sa.String(length=255), nullable=True),
        sa.Column('resource_name', sa.String(length=100), nullable=True),
        sa.Column('action_name', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='mova',
    )
    op.create_index('idx_api_permissions_history_user', 'api_user_permissions_history', ['user_id'], schema='mova')
    op.create_index('idx_api_permissions_history_date', 'api_user_permissions_history', [sa.text('changed_at DESC')], schema='mova')
    op.create_index('idx_api_permissions_history_changed_by', 'api_user_permissions_history', ['changed_by'], schema='mova')

    # ========================================
    # 6. Seed data - API Actions
    # ========================================
    op.execute("""
        INSERT INTO mova.api_actions (action_name, http_methods, description) VALUES
        ('read', 'GET', 'Read and list resources'),
        ('create', 'POST', 'Create new resources'),
        ('update', 'PUT,PATCH', 'Update existing resources'),
        ('delete', 'DELETE', 'Delete resources');
    """)

    # ========================================
    # 7. Seed data - API Resources
    # ========================================
    op.execute("""
        INSERT INTO mova.api_resources (resource_name, description) VALUES
        ('vehicles', 'Vehicle management - tracked units'),
        ('drivers', 'Driver/conductor management'),
        ('devices', 'Tracking device management'),
        ('reports', 'Telemetry and analytics reports'),
        ('users', 'User management'),
        ('groups', 'Group and subgroup management');
    """)

    # ========================================
    # 8. Seed data - API Permissions (cross join)
    # ========================================
    op.execute("""
        INSERT INTO mova.api_permissions (resource_id, action_id, permission_key, description)
        SELECT
            r.id,
            a.id,
            r.resource_name || '.' || a.action_name,
            a.description || ' for ' || r.resource_name
        FROM mova.api_resources r
        CROSS JOIN mova.api_actions a
        WHERE r.is_active = true;
    """)

    # ========================================
    # 9. Grant ALL permissions to user 4674 (Admin)
    # ========================================
    op.execute("""
        INSERT INTO mova.api_user_permissions (user_id, permission_id, granted, granted_by, notes)
        SELECT
            4674,
            p.id,
            true,
            4674,
            'Initial setup - Full admin access'
        FROM mova.api_permissions p
        WHERE p.is_active = true
        ON CONFLICT (user_id, permission_id) DO NOTHING;
    """)

    # ========================================
    # 10. Grant READ-only permissions to user 1391 (Viewer)
    # ========================================
    op.execute("""
        INSERT INTO mova.api_user_permissions (user_id, permission_id, granted, granted_by, notes)
        SELECT
            1391,
            p.id,
            true,
            4674,
            'Initial setup - Read-only access'
        FROM mova.api_permissions p
        JOIN mova.api_actions a ON p.action_id = a.id
        WHERE p.is_active = true
          AND a.action_name = 'read'
        ON CONFLICT (user_id, permission_id) DO NOTHING;
    """)

    # ========================================
    # 11. Create trigger for audit history
    # ========================================
    op.execute("""
        CREATE OR REPLACE FUNCTION log_api_permission_change()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO mova.api_user_permissions_history (
                user_id,
                permission_id,
                permission_key,
                action,
                granted,
                changed_by,
                changed_at,
                user_email,
                resource_name,
                action_name
            )
            SELECT
                COALESCE(NEW.user_id, OLD.user_id),
                COALESCE(NEW.permission_id, OLD.permission_id),
                p.permission_key,
                CASE
                    WHEN TG_OP = 'INSERT' THEN 'granted'
                    WHEN TG_OP = 'DELETE' THEN 'revoked'
                    ELSE 'modified'
                END,
                COALESCE(NEW.granted, false),
                COALESCE(NEW.granted_by, NEW.revoked_by, OLD.granted_by),
                NOW(),
                u.email,
                r.resource_name,
                a.action_name
            FROM mova.api_permissions p
            JOIN mova.api_resources r ON p.resource_id = r.id
            JOIN mova.api_actions a ON p.action_id = a.id
            JOIN mova.users u ON u.id = COALESCE(NEW.user_id, OLD.user_id)
            WHERE p.id = COALESCE(NEW.permission_id, OLD.permission_id);

            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER api_user_permissions_audit
        AFTER INSERT OR UPDATE OR DELETE ON mova.api_user_permissions
        FOR EACH ROW EXECUTE FUNCTION log_api_permission_change();
    """)


def downgrade() -> None:
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS api_user_permissions_audit ON mova.api_user_permissions;")
    op.execute("DROP FUNCTION IF EXISTS log_api_permission_change();")

    # Drop tables in reverse order
    op.drop_table('api_user_permissions_history', schema='mova')
    op.drop_table('api_user_permissions', schema='mova')
    op.drop_table('api_permissions', schema='mova')
    op.drop_table('api_actions', schema='mova')
    op.drop_table('api_resources', schema='mova')
    # Nao remove mova.users (stub) nem mova schema - podem ter outros dados