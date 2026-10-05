"""
Application configuration using Pydantic Settings.
Loads configuration from environment variables and .env file.
"""

from typing import List, Optional
from pydantic import Field, PostgresDsn, RedisDsn, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    All settings can be overridden via .env file or environment variables.
    """

    # Application settings
    APP_NAME: str = Field(default="Fleet Management Platform", description="Application name")
    APP_VERSION: str = Field(default="0.1.0", description="Application version")
    ENVIRONMENT: str = Field(default="development", description="Environment (development, staging, production)")
    DEBUG: bool = Field(default=True, description="Debug mode")
    API_V1_PREFIX: str = Field(default="/api/v1", description="API v1 prefix")

    # Server settings
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, description="Server port")

    # Database settings (Aurora PostgreSQL)
    DATABASE_URL: PostgresDsn = Field(
        default="postgresql://user:password@aurora-cluster.rds.amazonaws.com:5432/database",
        description="PostgreSQL connection URL (Master - Read/Write)"
    )
    DATABASE_REPLICA_URL: Optional[PostgresDsn] = Field(
        default=None,
        description="PostgreSQL read replica URL (Read-Only). If not set, falls back to DATABASE_URL"
    )
    #: Caminho de busca de schema aplicado na conexão.
    #:
    #: Necessário para usuários cujo ``search_path`` não inclui ``mova``: os
    #: modelos de permissão referenciam a tabela pelo nome simples, e sem o
    #: caminho a consulta falha logo após a autenticação.
    DATABASE_SEARCH_PATH: Optional[str] = Field(
        default=None,
        description="search_path aplicado na conexão, ex.: public,audit,mova",
    )

    DATABASE_POOL_SIZE: int = Field(default=10, description="Database connection pool size")
    DATABASE_MAX_OVERFLOW: int = Field(default=20, description="Maximum overflow connections")
    DATABASE_REPLICA_POOL_SIZE: int = Field(default=15, description="Read replica connection pool size")
    DATABASE_REPLICA_MAX_OVERFLOW: int = Field(default=30, description="Read replica maximum overflow connections")
    # Separado do DEBUG: imprimir cada SQL com os parâmetros (listas de milhares
    # de ids) trava o servidor quando várias telas consultam ao mesmo tempo.
    SQL_ECHO: bool = Field(default=False, description="Imprime cada consulta SQL no log")

    # Redis settings
    REDIS_URL: RedisDsn = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL"
    )
    REDIS_CACHE_TTL: int = Field(default=3600, description="Default cache TTL in seconds")

    # Security settings
    SECRET_KEY: str = Field(
        default="your-super-secret-key-change-this-in-production",
        description="Secret key for JWT encoding"
    )
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=15,
        description="Access token expiration time in minutes"
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=7,
        description="Refresh token expiration time in days"
    )
    SSO_SHARED_SECRET: str = Field(
        default="change-this-shared-secret-in-production",
        description=(
            "Segredo compartilhado com a plataforma padrao da empresa, usado para "
            "validar a assinatura HMAC dos tickets de SSO handoff (F4-02). "
            "NAO e o mesmo valor de SECRET_KEY (esse assina os JWTs de sessao)."
        )
    )
    EMBED_PARCEIROS: str = Field(
        default="",
        description=(
            "Parceiros que embutem as telas no sistema deles (JSON: id, nome, segredo, "
            "contas, origens, marca). Ver app/api/v1/endpoints/embed.py."
        ),
    )
    ROTEIRIZADOR_OSRM_URL: str = Field(
        default="",
        description="Servidor OSRM (malha viária) da roteirização. Vazio = estimativa por linha reta. Ver roteirizacao.py.",
    )
    JIMI_PROXY_URL: str = Field(
        default="",
        description="API Gateway do proxy JIMI (câmeras JC450): status online e abrir vídeo ao vivo. Ver cameras.py.",
    )
    JIMI_STREAM_CDN: str = Field(
        default="",
        description="CloudFront HTTPS que serve o FLV da JIMI no lugar da porta 8881 (HTTP). Ver cameras.py.",
    )
    HIKVISION_API_URL: str = Field(
        default="",
        description="API Gateway do proxy Hikvision HAT Cloud (G40/G40 PRO). Ver cameras.py.",
    )
    HIKVISION_API_KEY: str = Field(
        default="",
        description="x-api-key do API Gateway Hikvision. Segredo: só no .env, nunca no código.",
    )
    SS_ADMIN_USER_IDS: str = Field(
        default="",
        description=(
            "Ids de usuários da SS tratados como super admin mesmo sem a caixa "
            "'Usuário SS' (users.user_mova) marcada no cadastro, separados por vírgula. "
            "Paliativo enquanto o cadastro não é corrigido no sistema atual."
        ),
    )
    SSO_TICKET_MAX_AGE_SECONDS: int = Field(
        default=60,
        description="Janela maxima de validade de um ticket de SSO handoff, em segundos"
    )

    # CORS settings
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:3001", "http://localhost:8080"],
        description="Allowed CORS origins"
    )

    @validator("CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v):
        """Parse CORS origins from string or list."""
        if isinstance(v, str):
            return [i.strip() for i in v.split(",")]
        return v

    # Sentry (Error Tracking)
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry DSN for error tracking")
    SENTRY_ENVIRONMENT: str = Field(default="development", description="Sentry environment")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(
        default=0.1,
        description="Sentry traces sample rate (0.0 to 1.0)"
    )

    # Logging settings
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    LOG_FORMAT: str = Field(default="json", description="Log format (json or text)")

    # Prometheus settings
    PROMETHEUS_ENABLED: bool = Field(default=True, description="Enable Prometheus metrics")
    PROMETHEUS_PORT: int = Field(default=9091, description="Prometheus port")

    # Rate limiting
    RATE_LIMIT_ENABLED: bool = Field(default=True, description="Enable rate limiting")
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=60,
        description="Rate limit per minute for authenticated users"
    )
    API_KEY_RATE_LIMIT_PER_HOUR: int = Field(
        default=1000,
        description="Rate limit per hour for API keys"
    )

    # File upload settings
    MAX_UPLOAD_SIZE: int = Field(
        default=10485760,
        description="Maximum file upload size in bytes (10MB default)"
    )

    # Timezone
    TIMEZONE: str = Field(default="America/Sao_Paulo", description="Application timezone")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    @property
    def database_url_async(self) -> str:
        """Get asynchronous database URL for Master (Read/Write operations)."""
        url = str(self.DATABASE_URL)
        if "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return url

    @property
    def database_url_sync(self) -> str:
        """Get synchronous database URL (for Alembic migrations)."""
        return str(self.DATABASE_URL).replace("+asyncpg", "")

    @property
    def database_replica_url_async(self) -> str:
        """
        Get asynchronous database URL for Read Replica (SELECT operations).
        Falls back to master if replica URL is not configured.
        """
        if self.DATABASE_REPLICA_URL:
            url = str(self.DATABASE_REPLICA_URL)
            if "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://")
            return url
        # Fallback to master if replica not configured
        return self.database_url_async

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENVIRONMENT == "development"


# Global settings instance
settings = Settings()