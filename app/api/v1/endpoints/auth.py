"""
Authentication endpoints.
Handles user login, token refresh, and logout.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import (
    verify_password_sha1,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_sso_ticket,
    SSOTicketError,
)
from app.core.redis import async_redis_client
from app.core.config import settings
from app.core.access_control import invalidate_user_groups_cache
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.user import LoginRequest, SSOHandoffRequest, TokenResponse, MeResponse
from app.middleware.auth import AuthenticatedUser, get_current_user
from app.services.permission_cache import permission_cache

logger = get_logger(__name__)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    User login endpoint (SHA1 compatibility with mova.users).

    Authenticates user by login field and returns JWT access and refresh tokens.
    """
    # Get user by login (not email)
    # Note: Legacy table may have duplicate logins, get first active user with matching password
    result = await db.execute(
        select(User).where(
            User.login == credentials.login,
            User.status == 1  # Only active users
        ).order_by(User.id)
    )
    user = result.scalars().first()

    # Verify user exists and password is correct (SHA1 validation)
    if not user or not verify_password_sha1(credentials.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    # Check if user is active (status = 1)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    # Create tokens
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    # Store refresh token in Redis
    await async_redis_client.setex(
        f"refresh_token:{refresh_token}",
        settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        str(user.id)
    )

    # Note: No last_login update since field doesn't exist in mova.users
    # If needed, add column: ALTER TABLE mova.users ADD COLUMN last_login timestamp;
    # user.last_login = datetime.utcnow()
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/sso-handoff", response_model=TokenResponse)
async def sso_handoff(
    request: SSOHandoffRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    SSO handoff endpoint (F4-02).

    Recebe um ticket de curta duracao (janela de SSO_TICKET_MAX_AGE_SECONDS,
    padrao 60s) emitido pela plataforma padrao da empresa, valida a
    assinatura HMAC e a identidade do usuario contra mova.users, e emite
    os tokens de sessao normais do Dashboard Start (mesmo formato do
    /login). O ticket e de uso unico - uma segunda tentativa com o mesmo
    ticket e rejeitada, mesmo dentro da janela de validade.

    Ver decisao de arquitetura (F4-01) para o desenho completo do fluxo.
    """
    # 1. Valida formato, assinatura e expiracao do ticket
    try:
        ticket_data = verify_sso_ticket(request.ticket)
    except SSOTicketError as e:
        logger.warning("sso_handoff_invalid_ticket", reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket de SSO invalido ou expirado"
        )

    user_id = ticket_data["user_id"]
    nonce = ticket_data["nonce"]

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        logger.warning("sso_handoff_invalid_user_id", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket de SSO invalido ou expirado"
        )

    # 2. Garante uso unico: primeira requisicao a "consumir" o nonce vence,
    # qualquer outra (replay, double-click, etc.) e rejeitada.
    nonce_key = f"sso_ticket_used:{nonce}"
    is_first_use = await async_redis_client.set(
        nonce_key,
        user_id,
        nx=True,
        ex=settings.SSO_TICKET_MAX_AGE_SECONDS * 2  # margem de seguranca sobre a janela do ticket
    )
    if not is_first_use:
        logger.warning("sso_handoff_ticket_replay", user_id=user_id, nonce=nonce)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket de SSO ja utilizado"
        )

    # 3. Confirma que o usuario existe e esta ativo em mova.users
    result = await db.execute(
        select(User).where(
            User.id == user_id_int,
            User.status == 1
        )
    )
    user = result.scalars().first()

    if not user:
        logger.warning("sso_handoff_user_not_found_or_inactive", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario nao encontrado ou inativo"
        )

    # 4. Emite os tokens de sessao normais (mesmo fluxo do /login)
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    await async_redis_client.setex(
        f"refresh_token:{refresh_token}",
        settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        str(user.id)
    )

    logger.info("sso_handoff_success", user_id=user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Refresh access token.

    Uses refresh token to generate a new access token.
    """
    # Verify refresh token exists in Redis
    user_id = await async_redis_client.get(f"refresh_token:{refresh_token}")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    # Verify refresh token signature
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    # Generate new access token
    new_access_token = create_access_token(subject=user_id)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/logout")
async def logout(refresh_token: str):
    """
    User logout.

    Revokes refresh token by removing it from Redis.
    """
    # Delete refresh token from Redis
    await async_redis_client.delete(f"refresh_token:{refresh_token}")

    return {"message": "Successfully logged out"}


@router.post("/refresh-permissions")
async def refresh_permissions(
    current_user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Refresh current user's cached permissions and group access.

    Use this endpoint when:
    - An admin informed you that your permissions were changed
    - You need to see newly granted group/subgroup access immediately
    - You're experiencing permission issues that may be caused by stale cache

    This invalidates both:
    - Permission cache (api_user_permissions)
    - Group access cache (user_group_access)

    After calling this endpoint, the next API request will reload fresh data from the database.
    """
    user_id = current_user.user_id

    # Invalidate permission cache
    await permission_cache.invalidate_user(user_id)

    # Invalidate group access cache
    await invalidate_user_groups_cache(user_id)

    logger.info(
        "user_permissions_refreshed",
        user_id=user_id,
        auth_type=current_user.auth_type
    )

    return {
        "status": "refreshed",
        "user_id": user_id,
        "message": "Permission and group access cache invalidated. Next request will use fresh data."
    }


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna os dados do usuário autenticado (nome real, login, email).

    Usado pelo frontend para exibir o nome real do usuário no lugar de
    placeholders - especialmente usuários que entram via SSO handoff
    (F4-03), que até aqui só tinham o "Usuário {id}" disponível.
    """
    result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    resposta = MeResponse.model_validate(user)
    # O front decide o perfil pelo user_mova: aqui ele já reflete a lista
    # SS_ADMIN_USER_IDS, para a mesma regra valer nos dois lados.
    resposta.user_mova = 1 if user.is_super_admin else (user.user_mova or 0)
    return resposta