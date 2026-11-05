"""Health, readiness and public instance metadata."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel

from costlens import PRODUCT_NAME, VENDOR, __version__
from costlens.api.deps import DatabaseDep, SettingsDep
from costlens.config import DeploymentMode
from costlens.core.metrics import PROMETHEUS_CONTENT_TYPE, REGISTRY

router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: str


class InstanceMeta(BaseModel):
    product: str
    vendor: str
    version: str
    deployment_mode: str
    environment: str
    ai_enabled: bool


@router.get("/healthz", response_model=Health)
def healthz() -> Health:
    """Liveness: the process is up."""
    return Health(status="ok")


@router.get("/readyz", response_model=Health)
def readyz(db: DatabaseDep, response: Response) -> Health:
    """Readiness: dependencies (database) are reachable."""
    if not db.ping():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="unavailable")
    return Health(status="ok")


@router.get("/api/v1/meta", response_model=InstanceMeta)
def meta(settings: SettingsDep) -> InstanceMeta:
    """Public, non-sensitive facts about this instance for the web UI."""
    return InstanceMeta(
        product=PRODUCT_NAME,
        vendor=VENDOR,
        version=__version__,
        deployment_mode=settings.deployment_mode.value,
        environment=settings.environment_name,
        ai_enabled=settings.ai_enabled,
    )


@router.get("/metrics", include_in_schema=False)
def metrics(settings: SettingsDep, authorization: str | None = Header(default=None)) -> Response:
    """Prometheus metrics. Needs ``Authorization: Bearer <COSTLENS_METRICS_TOKEN>``
    except in local mode with no token configured."""
    token = settings.metrics_token
    if token is None:
        if settings.deployment_mode is not DeploymentMode.LOCAL:
            raise HTTPException(status_code=404)
    else:
        expected = f"Bearer {token.get_secret_value()}"
        if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return Response(REGISTRY.render(), media_type=PROMETHEUS_CONTENT_TYPE)
