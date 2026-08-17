"""
Structured logging configuration using structlog.
Provides JSON-formatted logs for easy parsing by Loki/ELK.
"""

import logging
import sys
from typing import Any, Dict

import structlog
from structlog.stdlib import BoundLogger

from app.core.config import settings


def setup_logging() -> None:
    """
    Configure structured logging with structlog.

    Logs are output in JSON format for easy ingestion by Loki/Elasticsearch.
    In development, logs are pretty-printed for readability.
    """

    # Determine log level from settings
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Structlog processors pipeline
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Add appropriate renderer based on format
    if settings.LOG_FORMAT == "json":
        # JSON format for production (Loki/ELK)
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Pretty format for development
        processors.append(structlog.dev.ConsoleRenderer())

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__ of the module)

    Returns:
        BoundLogger instance for structured logging

    Example:
        logger = get_logger(__name__)
        logger.info("user_login", user_id=123, email="user@example.com")

        # Output (JSON):
        # {
        #   "event": "user_login",
        #   "user_id": 123,
        #   "email": "user@example.com",
        #   "level": "info",
        #   "timestamp": "2025-01-10T10:30:00.123456Z",
        #   "logger": "app.api.auth"
        # }
    """
    return structlog.get_logger(name)


def log_request(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    user_id: int = None,
    user_type: str = "anonymous",
    **kwargs: Any
) -> None:
    """
    Log HTTP request with structured data.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: Request path
        status_code: HTTP status code
        duration_ms: Request duration in milliseconds
        user_id: User ID (if authenticated)
        user_type: Type of user (platform, api_key, anonymous)
        **kwargs: Additional context to log
    """
    logger = get_logger("app.http")

    logger.info(
        "http_request",
        method=method,
        path=path,
        status=status_code,
        duration_ms=round(duration_ms, 2),
        user_id=user_id,
        user_type=user_type,
        **kwargs
    )


def log_error(
    event: str,
    error: Exception,
    context: Dict[str, Any] = None,
    **kwargs: Any
) -> None:
    """
    Log error with structured data and exception info.

    Args:
        event: Error event name
        error: Exception instance
        context: Additional context dictionary
        **kwargs: Additional fields to log
    """
    logger = get_logger("app.error")

    error_data = {
        "event": event,
        "error_type": type(error).__name__,
        "error_message": str(error),
        **kwargs
    }

    if context:
        error_data["context"] = context

    logger.error(**error_data, exc_info=True)


def log_metric(
    metric_name: str,
    value: float,
    unit: str = None,
    tags: Dict[str, str] = None,
    **kwargs: Any
) -> None:
    """
    Log custom metric for monitoring.

    Args:
        metric_name: Name of the metric
        value: Metric value
        unit: Unit of measurement (optional)
        tags: Metric tags/labels
        **kwargs: Additional context
    """
    logger = get_logger("app.metrics")

    metric_data = {
        "metric": metric_name,
        "value": value,
        **kwargs
    }

    if unit:
        metric_data["unit"] = unit

    if tags:
        metric_data["tags"] = tags

    logger.info(**metric_data)
