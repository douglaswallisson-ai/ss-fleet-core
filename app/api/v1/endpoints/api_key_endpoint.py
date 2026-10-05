"""
API Key management endpoints.

DS-1378 (épico DS-1342): implementa POST/GET/DELETE /api/v1/api-keys,
documentado no README mas nunca implementado (achado registrado desde o
F1-01 - a FK de APIKey.owner_id para mova.users.id foi corrigida lá, mas
ficou dormente porque nenhum endpoint escrevia nesses campos até agora).

Modelo de permissão adotado (documentado aqui por não existir ainda um
recurso "api_keys" no catálogo de permissões granular - ver
app/models/api_permissions.py):

- Criar/listar/revogar as PRÓPRIAS chaves: qualquer usuário autenticado
  via JWT (sessão de plataforma) pode gerenciar suas próprias chaves,
  sem exigir uma permissão granular extra - mesmo modelo de "recurso
  próprio" já usado em GET /auth/me. Autenticação via API Key não pode
  ser usada para criar OUTRAS API Keys (evita cadeia de auto-propagação
  de credenciais).
- Ver/revogar chaves de QUALQUER usuário: exige a permissão
  `admin.manage` (mesma usada em app/api/v1/endpoints/permissions.py),
  em vez de inventar uma nova permissão que exigiria uma migration para
  popular api_resources/api_actions/api_permissions.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_read
from app.core.security import generate_api_key, hash_api_key
from app.core.logging import get_logger
from app.middleware.auth import AuthenticatedUser, get_current_user
from app.models.api_key import APIKey
from app.schemas.api_key import (
    APIKeyCreate,
    APIKeyResponse,
    APIKeyCreateResponse,
    APIKeyRevokeResponse,
)

logger = get_logger(__name__)

router = APIRouter()


def _require_jwt_user(current_user: AuthenticatedUser) -> None:
    """
    Bloqueia a criação de novas API Keys por quem já está autenticado via
    API Key - só sessões de plataforma (JWT) podem gerar novas chaves.

    Sem essa checagem, uma API Key vazada poderia ser usada para gerar
    infinitas outras API Keys (o dono nem precisaria logar de novo),
    dificultando a revogação em caso de comprometimento.
    """
    if current_user.auth_type != "jwt":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Keys só podem ser criadas por usuários autenticados via login (JWT), não por outra API Key.",
        )


async def _get_owned_or_admin_key(
    key_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> APIKey:
    """
    Busca uma API Key por ID, permitindo acesso se o usuário atual for o
    dono OU tiver a permissão admin.manage. Lança 404 em qualquer outro
    caso (não vaza a existência da chave para quem não tem acesso).
    """
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API Key not found")

    is_owner = api_key.owner_id == current_user.user_id
    is_admin = current_user.has_permission("admin", "manage")

    if not is_owner and not is_admin:
        # 404 (não 403) de propósito: não confirma para quem não tem
        # acesso que uma chave com esse ID existe.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API Key not found")

    return api_key


@router.post("/", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Cria uma nova API Key para o usuário autenticado.

    A chave em texto plano só é retornada NESTA resposta - a partir daqui
    só o hash bcrypt fica armazenado (mesmo padrão de senhas). Se
    perdida, a única opção é revogar e criar uma nova.

    Requer autenticação via JWT (login de plataforma) - ver
    `_require_jwt_user` para o motivo de não aceitar autenticação via
    outra API Key aqui.
    """
    _require_jwt_user(current_user)

    prefix = "sk_live" if key_data.environment == "live" else "sk_test"
    plain_key = generate_api_key(prefix=prefix)
    key_hash = hash_api_key(plain_key)

    # Prefixo armazenado para lookup rápido em authenticate_api_key -
    # precisa bater exatamente com o que aquela função recalcula a partir
    # da chave enviada pelo cliente (f"{parts[0]}_{parts[1]}_").
    key_prefix = f"{prefix}_"

    new_key = APIKey(
        owner_id=current_user.user_id,
        name=key_data.name,
        description=key_data.description,
        key_prefix=key_prefix,
        key_hash=key_hash,
        permissions=key_data.permissions,
        rate_limit_per_hour=key_data.rate_limit_per_hour,
        expires_at=key_data.expires_at,
        is_active=True,
    )

    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)

    logger.info(
        "api_key_created",
        api_key_id=new_key.id,
        owner_id=new_key.owner_id,
        key_prefix=new_key.key_prefix,
        environment=key_data.environment,
    )

    return APIKeyCreateResponse(
        **APIKeyResponse.model_validate(new_key).model_dump(),
        api_key=plain_key,
    )


@router.get("/", response_model=List[APIKeyResponse])
async def list_api_keys(
    all_users: bool = Query(False, description="Se true, lista chaves de TODOS os usuários (requer admin.manage). Ignorado (sempre False) para quem não tem essa permissão."),
    include_revoked: bool = Query(False, description="Incluir chaves já revogadas (is_active=False)"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Lista as API Keys do usuário autenticado (ou de todos, se
    `all_users=true` e o usuário tiver a permissão `admin.manage`).

    Nunca retorna `key_hash` nem a chave em texto plano - só
    `key_prefix`, suficiente para o usuário reconhecer qual chave é qual
    em logs/integrações sem expor o segredo.
    """
    query = select(APIKey)

    if all_users and current_user.has_permission("admin", "manage"):
        pass  # sem filtro de owner - visão administrativa
    else:
        query = query.where(APIKey.owner_id == current_user.user_id)

    if not include_revoked:
        query = query.where(APIKey.is_active == True)

    query = query.order_by(APIKey.created_at.desc())

    result = await db.execute(query)
    keys = result.scalars().all()

    return keys


@router.get("/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Detalhes de uma API Key específica (só o dono ou admin.manage)."""
    return await _get_owned_or_admin_key(key_id, current_user, db)


@router.delete("/{key_id}", response_model=APIKeyRevokeResponse)
async def revoke_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Revoga uma API Key (soft: is_active=False).

    Não é hard-delete de propósito: preserva `key_prefix`,
    `total_requests` e `last_used_at` para auditoria/investigação, mesmo
    espírito do soft delete usado no resto do sistema (ver
    docs/CRUD_STANDARDS.md), adaptado aqui porque `fleet_api_keys` já
    tem seu próprio campo de status (`is_active`) em vez do padrão
    `status` (1/0/-1) das tabelas `mova.*`.

    Revogar uma chave já revogada é idempotente (não é erro).
    """
    api_key = await _get_owned_or_admin_key(key_id, current_user, db)

    if api_key.is_active:
        api_key.is_active = False
        await db.commit()
        await db.refresh(api_key)

        logger.warning(
            "api_key_revoked",
            api_key_id=api_key.id,
            owner_id=api_key.owner_id,
            revoked_by=current_user.user_id,
        )

    return api_key