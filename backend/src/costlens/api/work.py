"""Work-tracking integrations and tickets / pull requests opened from findings."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, plan_feature, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.findings.workflow import event
from costlens.models import Finding, FindingLink, WorkIntegration
from costlens.work import service

router = APIRouter(tags=["work tracking"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Kind = Literal["jira", "servicenow", "github", "gitlab"]
DEFAULT_URL = {"github": "https://api.github.com", "gitlab": "https://gitlab.com"}
NAME = re.compile(r"^[\w.\-/ ]{1,200}$")


class IntegrationIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=200)
    token: str = Field(min_length=4, max_length=2000)
    config: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required(self) -> IntegrationIn:
        need = {
            "jira": ["project_key"],
            "servicenow": [],
            "github": ["repo"],
            "gitlab": ["project"],
        }[self.kind]
        missing = [k for k in need if not self.config.get(k)]
        if missing:
            raise ValueError(f"missing settings: {', '.join(missing)}")
        for v in self.config.values():
            if not NAME.match(v):
                raise ValueError(f"not a valid name: {v!r}")
        if self.kind == "github" and not re.fullmatch(r"[\w.\-]+/[\w.\-]+", self.config["repo"]):
            raise ValueError("repo must look like owner/name")
        if self.kind in ("jira", "servicenow") and not self.base_url:
            raise ValueError("base_url is required (e.g. https://acme.atlassian.net)")
        if self.kind in ("jira", "servicenow") and not self.username:
            raise ValueError("username is required")
        return self


class IntegrationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    token: str | None = Field(default=None, min_length=4, max_length=2000)


class IntegrationOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    base_url: str
    username: str | None
    token_hint: str
    config: dict[str, Any]
    enabled: bool
    last_used_at: datetime | None
    last_error: str | None


class LinkIn(BaseModel):
    integration_id: uuid.UUID


def _out(i: WorkIntegration) -> IntegrationOut:
    return IntegrationOut(
        id=i.id,
        kind=i.kind,
        name=i.name,
        base_url=i.base_url,
        username=i.username,
        token_hint=f"…{i.token[-4:]}",
        config=i.config or {},
        enabled=i.enabled,
        last_used_at=i.last_used_at,
        last_error=i.last_error,
    )


def _link_out(link: FindingLink) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "kind": link.kind,
        "provider": link.provider,
        "external_id": link.external_id,
        "url": link.url,
        "created_at": link.created_at.isoformat(),
    }


def _get(db: SessionDep, integration_id: uuid.UUID) -> WorkIntegration:
    i = db.get(WorkIntegration, integration_id)
    if i is None:
        raise NotFoundError("Integration not found.")
    return i


@router.get("/api/v1/org/work-integrations", response_model=list[IntegrationOut])
def list_integrations(auth: Reader, db: SessionDep) -> list[IntegrationOut]:
    return [_out(i) for i in db.scalars(select(WorkIntegration).order_by(WorkIntegration.name))]


@router.post(
    "/api/v1/org/work-integrations",
    response_model=IntegrationOut,
    status_code=201,
    dependencies=[plan_feature("work_integrations")],
)
def create_integration(
    body: IntegrationIn, request: Request, auth: Admin, db: SessionDep
) -> IntegrationOut:
    assert auth.org_id is not None  # noqa: S101
    url = service.check_url(body.base_url or DEFAULT_URL[body.kind], request.app.state.resolver)
    i = WorkIntegration(
        org_id=auth.org_id,
        kind=body.kind,
        name=body.name,
        base_url=url,
        username=body.username,
        token=body.token,
        config=body.config,
    )
    db.add(i)
    db.flush()
    return _out(i)


@router.patch("/api/v1/org/work-integrations/{integration_id}", response_model=IntegrationOut)
def update_integration(
    integration_id: uuid.UUID, body: IntegrationPatch, auth: Admin, db: SessionDep
) -> IntegrationOut:
    i = _get(db, integration_id)
    for field in ("name", "enabled", "token"):
        value = getattr(body, field)
        if value is not None:
            setattr(i, field, value)
    db.flush()
    return _out(i)


@router.delete("/api/v1/org/work-integrations/{integration_id}", status_code=204)
def delete_integration(integration_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_get(db, integration_id))
    return Response(status_code=204)


@router.get("/api/v1/findings/{finding_id}/links")
def list_links(finding_id: uuid.UUID, auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [
        _link_out(link)
        for link in db.scalars(
            select(FindingLink)
            .where(FindingLink.finding_id == finding_id)
            .order_by(FindingLink.created_at)
        )
    ]


@router.post("/api/v1/findings/{finding_id}/links", status_code=201)
def open_link(
    finding_id: uuid.UUID, body: LinkIn, request: Request, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    f = db.get(Finding, finding_id)
    if f is None:
        raise NotFoundError("Finding not found.")
    i = _get(db, body.integration_id)
    if not i.enabled:
        raise AppError("This integration is turned off.", code="integration_disabled")
    settings = request.app.state.settings
    link_url = f"{settings.base_url.rstrip('/')}/savings?finding={f.id}"
    external_id, url = service.open_item(
        request.app.state.http, i, f, link_url, request.app.state.resolver
    )
    kind = "ticket" if i.kind in service.TICKETS else "pull_request"
    link = FindingLink(
        org_id=f.org_id,
        finding_id=f.id,
        integration_id=i.id,
        kind=kind,
        provider=i.kind,
        external_id=external_id,
        url=url,
        created_by_id=auth.user.id,
    )
    db.add(link)
    word = "Ticket" if kind == "ticket" else "Pull request"
    event(db, f, "comment", auth.user.id, f"{word} {external_id} opened in {i.name}: {url}")
    db.flush()
    return _link_out(link)
