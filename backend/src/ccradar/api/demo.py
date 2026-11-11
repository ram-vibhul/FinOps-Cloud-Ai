"""Join the demo organisation."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ccradar.accounts import service as acc
from ccradar.api.deps import CurrentAuth, SessionDep, SettingsDep
from ccradar.core.errors import NotFoundError
from ccradar.demo.seed import join_demo

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class DemoJoined(BaseModel):
    organization_id: str


@router.post("/join", response_model=DemoJoined)
def join(auth: CurrentAuth, db: SessionDep, settings: SettingsDep) -> DemoJoined:
    """Explore the demo organisation (read-only) without connecting anything."""
    if not settings.demo_enabled:
        raise NotFoundError("The demo is not available on this installation.")
    org = join_demo(db, auth.user.id)
    acc.switch_organization(db, auth.session, org.id)
    return DemoJoined(organization_id=str(org.id))
