"""Fix api_user_permissions_audit trigger: distinguish granted/revoked on UPDATE and qualify users schema

Revision ID: 7c1e9a2f4b3d
Revises: 2f8a9c4b5d1e
Create Date: 2026-07-24 18:00:00.000000

ATUALIZADO EM 2026-07-27 (F2-02): NEW_FUNCTION_SQL tambem passou a
qualificar mova.api_user_permissions_history, mova.api_permissions,
mova.api_resources e mova.api_actions (antes so' mova.users estava
qualificado). Sem isso, "alembic upgrade head" quebrava num banco
novo/vazio, pois essas tabelas so' resolviam sem schema via search_path
implicito, que nao existe fora de producao.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '7c1e9a2f4b3d'
down_revision = '2f8a9c4b5d1e'
branch_labels = None
depends_on = None


NEW_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION log_api_permission_change()
    RETURNS TRIGGER AS $$
    DECLARE
        v_action VARCHAR(20);
        v_changed_by INTEGER;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            v_action := 'granted';
            v_changed_by := NEW.granted_by;
        ELSIF TG_OP = 'DELETE' THEN
            v_action := 'revoked';
            v_changed_by := OLD.revoked_by;
        ELSE
            -- UPDATE: only classify as granted/revoked when the `granted`
            -- flag actually flips; any other change (e.g. notes) is "modified"
            IF OLD.granted IS DISTINCT FROM NEW.granted THEN
                IF NEW.granted THEN
                    v_action := 'granted';
                    v_changed_by := NEW.granted_by;
                ELSE
                    v_action := 'revoked';
                    v_changed_by := NEW.revoked_by;
                END IF;
            ELSE
                v_action := 'modified';
                v_changed_by := COALESCE(NEW.revoked_by, NEW.granted_by);
            END IF;
        END IF;

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
            v_action,
            COALESCE(NEW.granted, false),
            v_changed_by,
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
"""

OLD_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION log_api_permission_change()
    RETURNS TRIGGER AS $$
    BEGIN
        INSERT INTO api_user_permissions_history (
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
        FROM api_permissions p
        JOIN api_resources r ON p.resource_id = r.id
        JOIN api_actions a ON p.action_id = a.id
        JOIN users u ON u.id = COALESCE(NEW.user_id, OLD.user_id)
        WHERE p.id = COALESCE(NEW.permission_id, OLD.permission_id);

        RETURN COALESCE(NEW, OLD);
    END;
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(NEW_FUNCTION_SQL)


def downgrade() -> None:
    op.execute(OLD_FUNCTION_SQL)