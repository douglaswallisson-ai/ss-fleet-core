"""
Modo embutido — as telas da plataforma dentro do sistema de um parceiro
(ex.: Citatti), já logadas e com as cores e a logo dele.

Fluxo (mesma ideia do SSO handoff que já existe em /auth/sso-handoff):

1. O SERVIDOR do parceiro gera um ticket assinado com o segredo dele
   (HMAC-SHA256), válido por 60 s e de uso único, dizendo quem é o usuário.
2. A página do parceiro abre um iframe em
   `https://<plataforma>/embed?ticket=...&tela=/app/mapa`.
3. A tela /embed troca o ticket por tokens normais aqui (`POST /embed/entrar`)
   e abre a tela pedida, sem menu nem cabeçalho da SS.

Formato do ticket: `<payload>.<assinatura>`
- payload = base64url de um JSON {"p": id do parceiro, "u": login, e-mail ou
  id do usuário na SS, "t": emitido em (unix), "n": nonce aleatório}
- assinatura = hex do HMAC-SHA256(segredo do parceiro, payload)

Parceiros: variável de ambiente EMBED_PARCEIROS (JSON), por exemplo
[{"id": "citatti", "nome": "Citatti", "segredo": "...", "contas": [539],
  "origens": ["https://app.citatti.com.br"],
  "marca": {"cor": "#0A4D8C", "destaque": "#F2A900", "logo": "https://.../logo.svg"}}]

Regras de segurança:
- o usuário precisa estar ativo e pertencer a uma das `contas` do parceiro;
- usuários internos da SS (user_mova) não entram por parceiro, a não ser
  que o parceiro tenha "permitir_usuarios_ss": true (demonstrações da SS);
- o front só aceita abrir dentro de uma das `origens` do parceiro.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_read
from app.core.logging import get_logger
from app.core.redis import async_redis_client
from app.core.security import create_access_token, create_refresh_token

logger = get_logger(__name__)
router = APIRouter()

VALIDADE_S = 60


def parceiros() -> dict[str, dict]:
    try:
        lista = json.loads(settings.EMBED_PARCEIROS or "[]")
    except json.JSONDecodeError:
        logger.error("embed_parceiros_json_invalido")
        return {}
    return {p["id"]: p for p in lista if p.get("id") and p.get("segredo")}


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def gerar_ticket(parceiro_id: str, segredo: str, usuario: str, nonce: Optional[str] = None) -> str:
    """Referência para o parceiro (e para testes): como montar um ticket válido."""
    import secrets

    corpo = json.dumps({"p": parceiro_id, "u": usuario, "t": int(time.time()), "n": nonce or secrets.token_hex(12)})
    payload = base64.urlsafe_b64encode(corpo.encode()).decode().rstrip("=")
    return f"{payload}.{hmac.new(segredo.encode(), payload.encode(), hashlib.sha256).hexdigest()}"


class Entrada(BaseModel):
    ticket: str = Field(..., min_length=10, max_length=2000)


def _negado(motivo: str, **extra):
    logger.warning("embed_entrada_negada", motivo=motivo, **extra)
    raise HTTPException(401, "Acesso inválido ou expirado. Volte à plataforma de origem e abra a tela de novo.")


@router.post("/entrar")
async def entrar(dados: Entrada, db: AsyncSession = Depends(get_db_read)):
    try:
        payload_b64, assinatura = dados.ticket.split(".", 1)
        corpo = json.loads(_b64d(payload_b64))
        pid, usuario, emitido, nonce = str(corpo["p"]), str(corpo["u"]).strip(), int(corpo["t"]), str(corpo["n"])
    except Exception:
        _negado("formato")

    parceiro = parceiros().get(pid)
    if not parceiro:
        _negado("parceiro_desconhecido", parceiro=pid)
    esperado = hmac.new(parceiro["segredo"].encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(assinatura, esperado):
        _negado("assinatura", parceiro=pid)
    idade = time.time() - emitido
    if idade < -5 or idade > VALIDADE_S:
        _negado("expirado", parceiro=pid)
    if len(nonce) < 8:
        _negado("nonce", parceiro=pid)

    # Uso único: o primeiro a consumir o nonce vence.
    if not await async_redis_client.set(f"embed_ticket:{pid}:{nonce}", "1", nx=True, ex=VALIDADE_S * 3):
        _negado("reuso", parceiro=pid)

    u = (
        await db.execute(
            text(
                """
                SELECT id, name, login, email, account_id, COALESCE(user_mova, 0) AS user_mova
                FROM mova.users
                WHERE status = 1 AND (lower(login) = lower(:u) OR lower(email) = lower(:u)
                                      OR id::text = :u)
                ORDER BY id LIMIT 1
                """
            ),
            {"u": usuario},
        )
    ).mappings().first()
    if not u:
        _negado("usuario_inexistente", parceiro=pid)
    interno = bool(u["user_mova"])
    if interno and not parceiro.get("permitir_usuarios_ss"):
        _negado("usuario_interno_ss", parceiro=pid, user_id=u["id"])
    contas = parceiro.get("contas") or []
    if not interno and (not contas or u["account_id"] not in contas):
        _negado("conta_fora_do_parceiro", parceiro=pid, user_id=u["id"])

    access = create_access_token(subject=str(u["id"]))
    refresh = create_refresh_token(subject=str(u["id"]))
    await async_redis_client.setex(f"refresh_token:{refresh}", settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, str(u["id"]))
    logger.info("embed_entrada", parceiro=pid, user_id=u["id"])

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "parceiro": {
            "id": pid,
            "nome": parceiro.get("nome") or pid,
            "origens": parceiro.get("origens") or [],
            "marca": parceiro.get("marca") or {},
        },
    }
