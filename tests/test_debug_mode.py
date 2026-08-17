"""
Testes da DS-1376 (épico DS-1342): DEBUG=true em produção expunha /docs,
/rapidoc e deixava reload=True ativo no uvicorn.

Usa fastapi.testclient.TestClient contra o app real (app.main), alternando
settings.DEBUG via monkeypatch para simular produção (False) e
desenvolvimento (True).

Executar com:
    pytest tests/test_debug_exposure.py -v
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_module():
    """
    Importa (ou reimporta) app.main a cada teste, para que a alternância de
    settings.DEBUG feita pelo teste anterior não vaze para o próximo -
    algumas configurações do FastAPI (como openapi_url) são fixadas na
    construção do objeto `FastAPI(...)`, então precisamos reconstruir o
    app depois de mudar DEBUG.
    """
    import app.main as main_module
    importlib.reload(main_module)
    return main_module


def _reload_with_debug(app_module, monkeypatch, debug: bool):
    """Ajusta settings.DEBUG e reconstrói app.main para refletir o valor."""
    monkeypatch.setattr(app_module.settings, "DEBUG", debug)
    importlib.reload(app_module)
    return app_module


# ---------------------------------------------------------------------------
# Produção (DEBUG=False): tudo deve estar fechado
# ---------------------------------------------------------------------------

def test_docs_returns_404_when_debug_false(app_module, monkeypatch):
    mod = _reload_with_debug(app_module, monkeypatch, debug=False)
    client = TestClient(mod.app)

    response = client.get("/docs")

    assert response.status_code == 404


def test_rapidoc_returns_404_when_debug_false(app_module, monkeypatch):
    mod = _reload_with_debug(app_module, monkeypatch, debug=False)
    client = TestClient(mod.app)

    response = client.get("/rapidoc")

    assert response.status_code == 404


def test_docs_info_returns_404_when_debug_false(app_module, monkeypatch):
    mod = _reload_with_debug(app_module, monkeypatch, debug=False)
    client = TestClient(mod.app)

    response = client.get("/docs-info")

    assert response.status_code == 404


def test_openapi_json_returns_404_when_debug_false(app_module, monkeypatch):
    """
    Cobre o achado adicional da DS-1376: antes da correção, /openapi.json
    (o schema cru que /docs e /rapidoc consomem) continuava público mesmo
    com as UIs de documentação bloqueadas.
    """
    mod = _reload_with_debug(app_module, monkeypatch, debug=False)
    client = TestClient(mod.app)

    response = client.get("/openapi.json")

    assert response.status_code == 404


def test_health_and_root_remain_public_when_debug_false(app_module, monkeypatch):
    """Endpoints operacionais (/, /health) não devem ser afetados pela
    correção - só a documentação deve ficar indisponível em produção."""
    mod = _reload_with_debug(app_module, monkeypatch, debug=False)
    client = TestClient(mod.app)

    assert client.get("/").status_code == 200
    # /health depende de DB/Redis (stubados como sempre saudáveis aqui);
    # o importante é que não seja 404 (ou seja, a rota existe e está ativa).
    assert client.get("/health").status_code != 404


# ---------------------------------------------------------------------------
# Desenvolvimento (DEBUG=True): documentação deve continuar acessível
# ---------------------------------------------------------------------------

def test_docs_available_when_debug_true(app_module, monkeypatch):
    mod = _reload_with_debug(app_module, monkeypatch, debug=True)
    client = TestClient(mod.app)

    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_json_available_when_debug_true(app_module, monkeypatch):
    mod = _reload_with_debug(app_module, monkeypatch, debug=True)
    client = TestClient(mod.app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json().get("info", {}).get("title") == mod.settings.APP_NAME


# ---------------------------------------------------------------------------
# reload desacoplado de DEBUG
# ---------------------------------------------------------------------------

def test_uvicorn_run_never_receives_reload_true(monkeypatch):
    """
    Trip-wire: independente do valor de settings.DEBUG, executar
    `python -m app.main` (simulado via runpy) nunca deve chamar
    uvicorn.run com reload=True. Substitui uvicorn.run por um dublê que
    apenas registra os argumentos recebidos, para não subir um servidor
    de verdade.
    """
    import runpy
    import sys
    import types

    calls = []

    fake_uvicorn = types.ModuleType("uvicorn")

    def _fake_run(*args, **kwargs):
        calls.append(kwargs)

    fake_uvicorn.run = _fake_run

    for debug_value in (True, False):
        import app.core.config as config_module
        monkeypatch.setattr(config_module.settings, "DEBUG", debug_value)
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        # Remove o app.main já importado para forçar reexecução completa
        # do módulo (inclusive o bloco `if __name__ == "__main__"`, que
        # runpy ativa ao rodar com run_name="__main__").
        sys.modules.pop("app.main", None)

        runpy.run_module("app.main", run_name="__main__")

    assert len(calls) == 2
    for call_kwargs in calls:
        assert call_kwargs["reload"] is False


def test_main_source_does_not_couple_reload_to_debug():
    """
    Varredura estática complementar: garante que ninguém reintroduza
    `reload=settings.DEBUG` (ou equivalente) no código-fonte de main.py.
    """
    import pathlib
    main_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "reload=settings.DEBUG" not in source
    assert "reload=DEBUG" not in source