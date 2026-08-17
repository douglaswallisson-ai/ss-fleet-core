"""
Security utilities for authentication and authorization.
Handles JWT tokens, password hashing, and API key generation.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import hashlib
import time

from jose import jwt, JWTError
from passlib.context import CryptContext
import secrets

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: Plain text password
        hashed_password: Bcrypt hashed password

    Returns:
        True if password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password

    Returns:
        Hashed password string
    """
    return pwd_context.hash(password)


def create_password_sha1(password: str) -> str:
    """
    Create SHA1 hash of password (legacy compatibility).

    Args:
        password: Plain text password

    Returns:
        SHA1 hashed password string (40 hex characters)
    """
    return hashlib.sha1(password.encode()).hexdigest()


def verify_password_sha1(plain_password: str, sha1_hash: str) -> bool:
    """
    Verify a plain password against a SHA1 hash (legacy compatibility).

    Args:
        plain_password: Plain text password
        sha1_hash: SHA1 hashed password from database

    Returns:
        True if password matches, False otherwise
    """
    calculated_hash = create_password_sha1(plain_password)
    return calculated_hash.lower() == sha1_hash.lower()


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token.

    Args:
        subject: Token subject (usually user_id)
        expires_delta: Token expiration time (optional)

    Returns:
        Encoded JWT token string
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "access"
    }

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(subject: str) -> str:
    """
    Create JWT refresh token with longer expiration.

    Args:
        subject: Token subject (usually user_id)

    Returns:
        Encoded JWT refresh token string
    """
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh"
    }

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and verify JWT token.

    Args:
        token: JWT token string

    Returns:
        Token payload dict if valid, None if invalid

    Raises:
        JWTError: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError as e:
        logger.warning("jwt_decode_failed", error=str(e))
        raise


def generate_api_key(prefix: str = "sk_live") -> str:
    """
    Generate a secure API key.

    Format: {prefix}_{random_string}
    Example: sk_live_abc123xyz789def456ghi

    Args:
        prefix: API key prefix (sk_live or sk_test)

    Returns:
        Generated API key string
    """
    random_part = secrets.token_urlsafe(32)
    return f"{prefix}_{random_part}"


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key for secure storage.

    Args:
        api_key: Plain API key

    Returns:
        Hashed API key
    """
    return pwd_context.hash(api_key)


def verify_api_key(plain_api_key: str, hashed_api_key: str) -> bool:
    """
    Verify an API key against its hash.

    Args:
        plain_api_key: Plain API key from request
        hashed_api_key: Hashed API key from database

    Returns:
        True if API key matches, False otherwise
    """
    return pwd_context.verify(plain_api_key, hashed_api_key)


# ============================================================
# SSO Handoff (F4-01 / F4-02)
# ============================================================
#
# Formato do ticket, emitido pela plataforma padrao (fora deste repositorio):
#
#   payload_raw = "{user_id}:{issued_at_unix}:{nonce}"
#   payload_b64 = base64url_no_padding(payload_raw)
#   signature   = hex(HMAC_SHA256(payload_b64, SSO_SHARED_SECRET))
#   ticket      = "{payload_b64}.{signature}"
#
# - user_id: mova.users.id (mesmo espaco de IDs das duas plataformas)
# - issued_at_unix: timestamp Unix (segundos) de quando o ticket foi criado
# - nonce: string aleatoria unica por ticket (garante uso unico)
#
# O HMAC usa SSO_SHARED_SECRET, um segredo dedicado a essa integracao,
# DIFERENTE do SECRET_KEY usado para assinar os JWTs de sessao.

import base64
import hmac


class SSOTicketError(Exception):
    """Erro de validacao de um ticket de SSO handoff (assinatura invalida, expirado, malformado)."""
    pass


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def create_sso_ticket(user_id: str, nonce: Optional[str] = None) -> str:
    """
    Gera um ticket de SSO handoff. Existe principalmente para uso em testes
    automatizados e no script de exemplo da integracao - em producao, quem
    gera o ticket e a plataforma padrao (fora deste repositorio), replicando
    este mesmo formato no lado dela.
    """
    nonce = nonce or secrets.token_urlsafe(16)
    payload_raw = f"{user_id}:{int(time.time())}:{nonce}"
    payload_b64 = _b64url_encode(payload_raw.encode())
    signature = hmac.new(
        settings.SSO_SHARED_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_sso_ticket(ticket: str) -> Dict[str, Any]:
    """
    Valida um ticket de SSO handoff emitido pela plataforma padrao.

    Verifica, nesta ordem: formato, assinatura HMAC (comparacao em tempo
    constante), e janela de expiracao. NAO verifica uso unico - isso e
    responsabilidade do endpoint (precisa de acesso ao Redis).

    Args:
        ticket: string no formato "{payload_b64}.{signature}"

    Returns:
        dict com user_id (str), issued_at (int, unix timestamp) e nonce (str)

    Raises:
        SSOTicketError: ticket malformado, assinatura invalida, ou expirado
    """
    try:
        payload_b64, signature = ticket.split(".", 1)
    except ValueError:
        raise SSOTicketError("Formato de ticket invalido")

    expected_signature = hmac.new(
        settings.SSO_SHARED_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        raise SSOTicketError("Assinatura do ticket invalida")

    try:
        payload_raw = _b64url_decode(payload_b64).decode()
        user_id, issued_at_str, nonce = payload_raw.split(":", 2)
        issued_at = int(issued_at_str)
    except (ValueError, UnicodeDecodeError):
        raise SSOTicketError("Payload do ticket malformado")

    age_seconds = time.time() - issued_at
    if age_seconds < 0 or age_seconds > settings.SSO_TICKET_MAX_AGE_SECONDS:
        raise SSOTicketError("Ticket expirado")

    return {"user_id": user_id, "issued_at": issued_at, "nonce": nonce}