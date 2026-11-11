"""Cost impact of planned changes (Terraform plans, Kubernetes manifests)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ccradar import estimate
from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError
from ccradar.costs.unit_prices import bill_prices

router = APIRouter(prefix="/api/v1/estimates", tags=["estimates"])
Reader = Annotated[AuthContext, require(Permission.READ)]
MAX_PLAN = 20_000_000


class K8sIn(BaseModel):
    after: str = Field(max_length=5_000_000)
    before: str = Field(default="", max_length=5_000_000)
    daemon_nodes: int = Field(default=3, ge=1, le=10_000)


def _prices(db: SessionDep, auth: AuthContext) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    return bill_prices(db, auth.org_id)


@router.get("/prices")
def prices(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Hourly prices per instance type from the bill; save as a file for the CI command."""
    return _prices(db, auth)


@router.post("/terraform")
async def terraform(auth: Reader, db: SessionDep, request: Request) -> dict[str, Any]:
    """Body: the JSON from ``terraform show -json plan.out`` (20 MB at most)."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_PLAN:
            raise AppError("The plan is too large (20 MB at most).", code="file_too_large")
    try:
        plan = json.loads(body)
    except ValueError as exc:
        raise AppError(
            "Send the JSON from `terraform show -json plan.out`.", code="bad_plan"
        ) from exc
    if not isinstance(plan, dict) or "resource_changes" not in plan:
        raise AppError("This is not a Terraform plan (no resource_changes).", code="bad_plan")
    p = _prices(db, auth)
    result = estimate.terraform(plan, p["prices"], p["currency"])
    return {**result.to_dict(), "markdown": estimate.markdown(result)}


@router.post("/kubernetes")
def kubernetes(body: K8sIn, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Compare requested CPU and memory of workloads (YAML or JSON) before and after."""
    try:
        result = estimate.kubernetes(
            body.after,
            body.before,
            currency="USD",
            daemon_nodes=body.daemon_nodes,
        )
    except estimate.ManifestError as exc:
        raise AppError(
            "The manifests could not be read as YAML or JSON.", code="bad_manifest"
        ) from exc
    return {**result.to_dict(), "markdown": estimate.markdown(result)}
