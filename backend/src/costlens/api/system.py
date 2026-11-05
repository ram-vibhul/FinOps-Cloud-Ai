"""Health, readiness and public instance metadata."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from costlens import PRODUCT_NAME, VENDOR, __version__
from costlens.api.deps import DatabaseDep, SettingsDep

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
