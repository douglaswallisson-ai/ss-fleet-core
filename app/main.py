"""
Main FastAPI application.
Entry point for the Fleet Management Platform backend.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.openapi.docs import get_redoc_html
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.database import close_db, check_db_health
from app.core.redis import check_redis_health
from app.middleware.monitoring import MonitoringMiddleware, metrics_endpoint

# Import routers
from app.api.v1.api import api_router

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Revisão de segurança (08/10/2026): os valores padrão de SECRET_KEY e
# SSO_SHARED_SECRET estão escritos no config.py, que é público. Com eles,
# qualquer pessoa assina um token de login como qualquer usuário (inclusive
# super admin da SS). Fora do ambiente de desenvolvimento o servidor não sobe
# com esses valores; no desenvolvimento só avisa, para não travar quem testa.
_SEGREDOS_PADRAO = {
    "SECRET_KEY": "your-super-secret-key-change-this-in-production",
    "SSO_SHARED_SECRET": "change-this-shared-secret-in-production",
}
_fracos = [
    nome for nome, padrao in _SEGREDOS_PADRAO.items()
    if getattr(settings, nome) == padrao or len(getattr(settings, nome)) < 32
]
if _fracos:
    if settings.ENVIRONMENT != "development":
        raise RuntimeError(
            f"Defina no .env um valor longo e aleatório (32+ caracteres) para: {', '.join(_fracos)}."
        )
    logger.warning("segredo_padrao_em_uso", variaveis=_fracos)

# Rotas herdadas que gravam no banco de produção. Nenhuma tela as usa hoje; até
# a engenharia validar a gravação em produção, ficam bloqueadas por padrão
# (PERMITIR_GRAVACAO_PRODUCAO no .env libera). As rotas novas gravam no
# armazenamento provisório e não passam por aqui.
_PREFIXOS_GRAVAM_PRODUCAO = tuple(
    f"{settings.API_V1_PREFIX}/{p}"
    for p in (
        "groups", "subgroups", "vehicles", "devices", "drivers",
        "device-associations", "video-device-associations", "admin/",
    )
)
_METODOS_LEITURA = {"GET", "HEAD", "OPTIONS"}


def _grava_em_producao(metodo: str, caminho: str) -> bool:
    if metodo in _METODOS_LEITURA:
        return False
    if caminho.startswith(f"{settings.API_V1_PREFIX}/events/") and caminho.endswith("/acknowledge"):
        return True
    return caminho.startswith(_PREFIXOS_GRAVAM_PRODUCAO)

# Initialize Sentry if DSN is configured
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[FastApiIntegration()],
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
    )
    logger.info("sentry_initialized", environment=settings.SENTRY_ENVIRONMENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # Startup
    logger.info(
        "application_startup",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT
    )

    # Check database connection
    db_healthy = await check_db_health()
    if not db_healthy:
        logger.error("database_connection_failed")
        # In production, you might want to exit here

    # Check Redis connection
    redis_healthy = await check_redis_health()
    if not redis_healthy:
        logger.warning("redis_connection_failed")

    yield

    # Shutdown
    logger.info("application_shutdown")
    await close_db()


# Create FastAPI application
# Disable default docs to create custom routes
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Fleet Management Platform - Backend API",
    docs_url=None,  # Disable default docs (custom routes /docs e /rapidoc abaixo)
    redoc_url=None,  # Disable default redoc
    # DS-1376: /docs, /rapidoc e /docs-info já checavam settings.DEBUG e
    # retornavam 404 em produção, mas o schema OpenAPI cru (/openapi.json)
    # ficava exposto de qualquer forma - é o dado que essas UIs consomem,
    # então escondê-lo fecha o mesmo tipo de exposição de vez.
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def bloquear_gravacao_producao(request, call_next):
    if not settings.PERMITIR_GRAVACAO_PRODUCAO and _grava_em_producao(request.method, request.url.path):
        logger.warning("gravacao_producao_bloqueada", metodo=request.method, caminho=request.url.path)
        return JSONResponse(
            status_code=403,
            content={"detail": {"message": "Gravação no banco de produção desligada nesta versão da plataforma."}},
        )
    return await call_next(request)


# Add monitoring middleware
if settings.PROMETHEUS_ENABLED:
    app.add_middleware(MonitoringMiddleware)
    app.add_route("/metrics", metrics_endpoint, methods=["GET"])
    logger.info("prometheus_metrics_enabled")

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API information."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "status": "operational",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.
    Verifies database and Redis connections.
    """
    db_status = await check_db_health()
    redis_status = await check_redis_health()

    is_healthy = db_status and redis_status

    return JSONResponse(
        status_code=200 if is_healthy else 503,
        content={
            "status": "healthy" if is_healthy else "unhealthy",
            "database": "connected" if db_status else "disconnected",
            "redis": "connected" if redis_status else "disconnected",
        }
    )


# ============================================================================
# API Documentation Endpoints - ReDoc (Read) + RapiDoc (Interactive)
# ============================================================================

@app.get("/docs", include_in_schema=False)
async def redoc_html():
    """
    ReDoc documentation interface (default at /docs).
    Clean, responsive documentation focused on readability.
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=404,
            content={"detail": "Documentation not available in production"}
        )

    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{settings.APP_NAME} - Documentation",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2.1.3/bundles/redoc.standalone.js",
    )


@app.get("/rapidoc", include_in_schema=False)
async def rapidoc_html():
    """
    RapiDoc documentation interface.
    Modern documentation with three layout modes: view, read, focused.
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=404,
            content={"detail": "Documentation not available in production"}
        )

    return HTMLResponse(
        content=f"""
        <!doctype html>
        <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>{settings.APP_NAME} - RapiDoc</title>
                <script
                    type="module"
                    src="https://unpkg.com/rapidoc@9.3.4/dist/rapidoc-min.js"
                ></script>
            </head>
            <body>
                <rapi-doc
                    spec-url="{app.openapi_url}"
                    theme="dark"
                    bg-color="#1e1e1e"
                    text-color="#f0f0f0"
                    header-color="#2d2d2d"
                    primary-color="#4a9eff"
                    nav-bg-color="#2d2d2d"
                    nav-text-color="#f0f0f0"
                    nav-hover-bg-color="#3d3d3d"
                    nav-hover-text-color="#ffffff"
                    nav-accent-color="#4a9eff"
                    layout="row"
                    render-style="view"
                    schema-style="tree"
                    show-header="true"
                    show-info="true"
                    allow-authentication="true"
                    allow-server-selection="true"
                    allow-api-list-style-selection="true"
                    api-key-name="Authorization"
                    api-key-location="header"
                    api-key-value="Bearer "
                >
                    <div slot="nav-logo" style="display: flex; align-items: center; padding: 16px;">
                        <div style="font-size: 20px; font-weight: bold; color: #4a9eff;">
                            {settings.APP_NAME}
                        </div>
                    </div>
                </rapi-doc>
            </body>
        </html>
        """
    )


@app.get("/docs-info", include_in_schema=False)
async def documentation_info():
    """
    Documentation options information.
    Lists all available documentation interfaces.
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=404,
            content={"detail": "Documentation not available in production"}
        )

    return {
        "message": "API documentation interfaces available",
        "interfaces": [
            {
                "name": "ReDoc",
                "url": "/docs",
                "description": "Clean, responsive documentation (default). Best for: Reading documentation, understanding API structure.",
                "features": [
                    "Three-panel responsive layout",
                    "Search functionality",
                    "Code samples in multiple languages",
                    "Download OpenAPI spec"
                ],
                "interactive": False
            },
            {
                "name": "RapiDoc",
                "url": "/rapidoc",
                "description": "Modern documentation with interactive testing. Best for: Testing APIs, flexible viewing, dark theme.",
                "features": [
                    "Three layout modes (view, read, focused)",
                    "Dark/light theme",
                    "Interactive API testing",
                    "Customizable navigation",
                    "API playground"
                ],
                "interactive": True
            }
        ],
        "openapi_spec": app.openapi_url,
        "app_info": {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT
        }
    }


if __name__ == "__main__":
    import uvicorn

    # DS-1376: reload NUNCA deve depender de settings.DEBUG - um "DEBUG=true"
    # esquecido no .env de produção não pode ligar o auto-reload do uvicorn
    # (overhead de CPU/memória monitorando o filesystem, além de não ser
    # este o mecanismo esperado para hot-reload em produção). Para
    # desenvolvimento local com reload, use o CLI diretamente:
    #   uvicorn app.main:app --reload
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )