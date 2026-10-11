"""
Regras de segurança (revisão de 08/10/2026). Rodam sem banco.

- Rotas herdadas que gravam em produção ficam bloqueadas por padrão.
- O servidor não sobe fora do desenvolvimento com os segredos padrão do config.py.
- O login bloqueia depois de LOGIN_MAX_FALHAS senhas erradas.
"""

import importlib

import pytest
from fastapi import HTTPException

from app import main
from app.api.v1.endpoints import auth
from app.core.config import settings


@pytest.mark.parametrize(
    "metodo,caminho",
    [
        ("POST", "/api/v1/vehicles/"),
        ("PUT", "/api/v1/vehicles/10"),
        ("DELETE", "/api/v1/devices/3"),
        ("POST", "/api/v1/drivers/"),
        ("POST", "/api/v1/groups/"),
        ("DELETE", "/api/v1/subgroups/9"),
        ("POST", "/api/v1/device-associations/"),
        ("DELETE", "/api/v1/video-device-associations/4"),
        ("POST", "/api/v1/events/123/acknowledge"),
        ("POST", "/api/v1/admin/permissions/grant"),
        ("DELETE", "/api/v1/admin/user-group-access/users/1/all"),
    ],
)
def test_gravacao_em_producao_e_reconhecida(metodo, caminho):
    assert main._grava_em_producao(metodo, caminho)


@pytest.mark.parametrize(
    "metodo,caminho",
    [
        ("GET", "/api/v1/vehicles/"),
        ("GET", "/api/v1/admin/permissions/"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/contratos"),            # provisório (SQLite), não produção
        ("POST", "/api/v1/escala-viagem/viagens"),
        ("POST", "/api/v1/reports/history/export/estimate"),
        ("POST", "/api/v1/cco/avisos/x/marcar"),
        ("POST", "/api/v1/eventos/timeline/x"),
    ],
)
def test_rotas_novas_e_leitura_nao_sao_bloqueadas(metodo, caminho):
    assert not main._grava_em_producao(metodo, caminho)


def test_middleware_devolve_403_sem_chegar_na_rota(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "PERMITIR_GRAVACAO_PRODUCAO", False)
    cliente = TestClient(main.app)
    r = cliente.post("/api/v1/vehicles/", json={})
    assert r.status_code == 403
    assert "produção" in r.json()["detail"]["message"]


def test_segredo_padrao_impede_subir_em_producao(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", main._SEGREDOS_PADRAO["SECRET_KEY"])
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        importlib.reload(main)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    importlib.reload(main)


class _RedisFalso:
    def __init__(self):
        self.d: dict[str, int] = {}

    async def get(self, k):
        return self.d.get(k)

    async def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    async def expire(self, k, s):
        return True

    async def delete(self, k):
        self.d.pop(k, None)


class _Resultado:
    def scalars(self):
        return self

    def first(self):
        return None  # login inexistente = senha errada


class _BancoFalso:
    async def execute(self, *_a, **_k):
        return _Resultado()


@pytest.mark.asyncio
async def test_login_bloqueia_depois_de_muitas_senhas_erradas(monkeypatch):
    from app.schemas.user import LoginRequest

    redis = _RedisFalso()
    monkeypatch.setattr(auth, "async_redis_client", redis)
    cred = LoginRequest(login="Fulano", password="errada")
    for _ in range(auth.LOGIN_MAX_FALHAS):
        with pytest.raises(HTTPException) as e:
            await auth.login(cred, db=_BancoFalso())
        assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        await auth.login(LoginRequest(login=" fulano ", password="certa?"), db=_BancoFalso())
    assert e.value.status_code == 429
