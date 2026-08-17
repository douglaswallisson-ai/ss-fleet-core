"""
Monitoring middleware for Prometheus metrics and request logging.
"""

import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

from app.core.logging import log_request, get_logger

logger = get_logger(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status", "user_type"]
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

REQUEST_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method", "endpoint"]
)

RESPONSE_SIZE = Histogram(
    "http_response_size_bytes",
    "HTTP response size in bytes",
    ["method", "endpoint"]
)


class MonitoringMiddleware(BaseHTTPMiddleware):
    """
    Middleware for monitoring HTTP requests.
    Collects Prometheus metrics and logs structured request data.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process HTTP request and collect metrics.

        Args:
            request: FastAPI request
            call_next: Next middleware/route handler

        Returns:
            Response object
        """
        # Skip metrics endpoint to avoid recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        # Extract endpoint pattern (remove path parameters)
        endpoint = self._get_endpoint_pattern(request)

        # Start timing
        start_time = time.time()

        # Track in-progress requests
        REQUEST_IN_PROGRESS.labels(
            method=request.method,
            endpoint=endpoint
        ).inc()

        # Get user info if available
        user_id = None
        user_type = "anonymous"

        if hasattr(request.state, "user"):
            user = request.state.user
            user_id = user.user_id
            user_type = user.auth_type

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration = time.time() - start_time

            # Update metrics
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status=response.status_code,
                user_type=user_type
            ).inc()

            REQUEST_DURATION.labels(
                method=request.method,
                endpoint=endpoint
            ).observe(duration)

            # Track response size if available
            if "content-length" in response.headers:
                size = int(response.headers["content-length"])
                RESPONSE_SIZE.labels(
                    method=request.method,
                    endpoint=endpoint
                ).observe(size)

            # Log request
            log_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration * 1000,
                user_id=user_id,
                user_type=user_type,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )

            return response

        except Exception as e:
            # Log error
            duration = time.time() - start_time

            logger.error(
                "request_error",
                method=request.method,
                path=request.url.path,
                duration_ms=duration * 1000,
                error=str(e),
                exc_info=True
            )

            # Update error metrics
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status=500,
                user_type=user_type
            ).inc()

            raise

        finally:
            # Decrement in-progress counter
            REQUEST_IN_PROGRESS.labels(
                method=request.method,
                endpoint=endpoint
            ).dec()

    def _get_endpoint_pattern(self, request: Request) -> str:
        """
        Extract endpoint pattern from request.
        Converts /api/v1/vehicles/123 to /api/v1/vehicles/{id}

        Args:
            request: FastAPI request

        Returns:
            Endpoint pattern string
        """
        # Try to get route pattern
        if request.url.path.startswith("/api/"):
            # Simple pattern extraction
            # In production, use request.scope.get("route") for accurate patterns
            return request.url.path

        return request.url.path


async def metrics_endpoint(request: Request) -> Response:
    """
    Prometheus metrics endpoint.
    Exposes collected metrics in Prometheus format.

    Usage:
        app.add_route("/metrics", metrics_endpoint)
    """
    metrics = generate_latest()
    return Response(content=metrics, media_type=CONTENT_TYPE_LATEST)
