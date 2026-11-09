"""Partner mode: client organisations, markup and re-billing, branding, link codes."""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from costlens.accounts import partners
from costlens.accounts import service as acc
from costlens.accounts.roles import Permission, Role
from costlens.api.deps import CurrentAuth, SessionDep, SettingsDep, plan_feature, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError, PermissionDeniedError
from costlens.costs.fx import converted
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CostDaily, Organization

router = APIRouter(tags=["partners"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Owner = Annotated[AuthContext, require(Permission.MANAGE_BILLING)]
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
CENT = Decimal("0.01")


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    default_currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    markup_pct: float = Field(default=0, ge=0, le=500)


class ClientPatch(BaseModel):
    markup_pct: float | None = Field(default=None, ge=0, le=500)


class BrandingIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    primary_color: str = Field(default="#2563eb", max_length=7)


class LinkIn(BaseModel):
    code: str = Field(min_length=8, max_length=100)


class PartnerFlag(BaseModel):
    is_partner: bool


def _partner(db: SessionDep, auth: AuthContext) -> Organization:
    org = db.get(Organization, auth.org_id)
    if org is None or not org.is_partner:
        raise AppError("This organisation is not a partner.", status=403, code="not_partner")
    return org


def _month_cost(db: SessionDep, org: Organization, start: date, end: date) -> Decimal:
    set_tenant(db, org_id=org.id)
    t = CostDaily
    amount = converted(
        t.effective_cost, t.billing_currency, t.charge_date, org.default_currency, t.org_id
    )
    v = db.scalar(
        select(func.sum(amount)).where(
            t.org_id == org.id, t.charge_date >= start, t.charge_date < end
        )
    )
    return Decimal(str(v or 0)).quantize(CENT)


def _markup(org: Organization) -> Decimal:
    return Decimal(str(((org.settings or {}).get("partner") or {}).get("markup_pct", 0)))


def _client(db: SessionDep, partner: Organization, client_id: uuid.UUID) -> Organization:
    set_tenant(db, bypass=True)
    c = db.get(Organization, client_id)
    if c is None or c.partner_id != partner.id:
        raise NotFoundError("Client not found.")
    return c


@router.get("/api/v1/partner/clients")
def list_clients(auth: Admin, db: SessionDep) -> list[dict[str, Any]]:
    partner = _partner(db, auth)
    today = utcnow().date()
    month = today.replace(day=1)
    last = (month - timedelta(days=1)).replace(day=1)
    set_tenant(db, bypass=True)
    out = []
    for c in partners.clients(db, partner.id):
        mtd, prev = (
            _month_cost(db, c, month, today + timedelta(days=1)),
            _month_cost(db, c, last, month),
        )
        out.append(
            {
                "id": str(c.id),
                "name": c.name,
                "currency": c.default_currency,
                "access": ((c.settings or {}).get("partner") or {}).get("role", "admin"),
                "markup_pct": float(_markup(c)),
                "month_to_date": float(mtd),
                "last_month": float(prev),
                "last_month_rebilled": float(
                    (prev * (1 + _markup(c) / 100)).quantize(CENT, ROUND_HALF_UP)
                ),
            }
        )
    set_tenant(db, org_id=partner.id, user_id=auth.user.id)
    return out


@router.post("/api/v1/partner/clients", status_code=201, dependencies=[plan_feature("partner")])
def create_client(body: ClientIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    partner = _partner(db, auth)
    set_tenant(db, bypass=True)
    name = body.name.strip()
    org = Organization(
        name=name, slug=acc._unique_slug(db, name), default_currency=body.default_currency
    )
    db.add(org)
    org.partner_id = partner.id
    org.settings = {
        **(org.settings or {}),
        "partner": {"role": Role.OWNER.value, "markup_pct": body.markup_pct},
    }
    db.flush()
    set_tenant(db, org_id=partner.id, user_id=auth.user.id)
    return {"id": str(org.id), "name": org.name, "slug": org.slug}


@router.get("/api/v1/partner/portfolio")
def portfolio(auth: Admin, db: SessionDep) -> list[dict[str, Any]]:
    """Clients sorted by what needs attention: failing connections, budgets heading over,
    unusual cost increases this month, fast growth, and open savings."""
    from costlens.costs.budgets import all_status
    from costlens.models import CloudConnection, CostAnomaly, Finding

    partner = _partner(db, auth)
    today = utcnow().date()
    month = today.replace(day=1)
    last = (month - timedelta(days=1)).replace(day=1)
    out = []
    set_tenant(db, bypass=True)
    for c in partners.clients(db, partner.id):
        set_tenant(db, org_id=c.id)
        reasons: list[str] = []
        score = 0.0
        broken = [
            x.name
            for x in db.scalars(select(CloudConnection).where(CloudConnection.status == "error"))
        ]
        if broken:
            score += 50 * len(broken)
            reasons.append(f"{len(broken)} connection(s) failing")
        over = [b["name"] for b in all_status(db, c.id, today) if (b["forecast_pct"] or 0) >= 100]
        if over:
            score += 30 * len(over)
            reasons.append(f"{len(over)} budget(s) heading over")
        anomalies = (
            db.scalar(
                select(func.count())
                .select_from(CostAnomaly)
                .where(CostAnomaly.last_day >= month, CostAnomaly.status == "open")
            )
            or 0
        )
        if anomalies:
            score += 10 * anomalies
            reasons.append(f"{anomalies} unusual cost increase(s) this month")
        mtd = _month_cost(db, c, month, today + timedelta(days=1))
        prev = _month_cost(db, c, last, month)
        days_in = max(today.day, 1)
        pace = float(mtd) / days_in * 30
        growth = (pace - float(prev)) / float(prev) * 100 if prev else 0.0
        if growth >= 20:
            score += min(growth, 200) / 4
            reasons.append(f"spend pace {growth:.0f}% above last month")
        savings = (
            db.scalar(
                select(func.coalesce(func.sum(Finding.savings_monthly), 0)).where(
                    Finding.status.in_(("open", "approved"))
                )
            )
            or 0
        )
        if float(savings) > 0:
            score += min(float(savings) / 100, 20)
            reasons.append(f"{float(savings):,.0f} {c.default_currency}/month of savings open")
        out.append(
            {
                "id": str(c.id),
                "name": c.name,
                "currency": c.default_currency,
                "attention": round(score, 1),
                "reasons": reasons,
                "month_to_date": float(mtd),
                "last_month": float(prev),
                "savings_open_monthly": round(float(savings), 2),
            }
        )
    set_tenant(db, org_id=partner.id, user_id=auth.user.id)
    return sorted(out, key=lambda r: (-r["attention"], r["name"]))


@router.get("/api/v1/partner/clients/{client_id}/statement.pdf")
def statement_pdf(
    client_id: uuid.UUID,
    auth: Admin,
    db: SessionDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
) -> Response:
    """The client's statement in the partner's branding: re-billed amounts only."""
    from costlens.reports.rebill import to_pdf

    partner = _partner(db, auth)
    data = rebill(client_id, auth, db, month)
    brand = (partner.settings or {}).get("branding") or {}
    pdf = to_pdf(
        brand.get("display_name") or partner.name,
        brand.get("primary_color") or "#2563eb",
        data["client"],
        date.fromisoformat(f"{month}-01").strftime("%B %Y"),
        data["currency"],
        [(x["service"], x["rebilled"]) for x in data["lines"]],
        data["rebilled"],
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="statement-{month}.pdf"'},
    )


@router.patch("/api/v1/partner/clients/{client_id}")
def update_client(
    client_id: uuid.UUID, body: ClientPatch, auth: Admin, db: SessionDep
) -> dict[str, Any]:
    partner = _partner(db, auth)
    c = _client(db, partner, client_id)
    if body.markup_pct is not None:
        c.settings = {
            **(c.settings or {}),
            "partner": {**((c.settings or {}).get("partner") or {}), "markup_pct": body.markup_pct},
        }
    db.flush()
    return {"id": str(c.id), "markup_pct": float(_markup(c))}


@router.get("/api/v1/partner/clients/{client_id}/rebill")
def rebill(
    client_id: uuid.UUID,
    auth: Admin,
    db: SessionDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
) -> dict[str, Any]:
    """Cost per service for a month, the markup and the re-billed amount (partner only)."""
    partner = _partner(db, auth)
    c = _client(db, partner, client_id)
    start = date.fromisoformat(f"{month}-01")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    set_tenant(db, org_id=c.id)
    t = CostDaily
    amount = converted(
        t.effective_cost, t.billing_currency, t.charge_date, c.default_currency, t.org_id
    )
    rows = db.execute(
        select(t.service_name, func.sum(amount))
        .where(t.org_id == c.id, t.charge_date >= start, t.charge_date < end)
        .group_by(t.service_name)
        .order_by(func.sum(amount).desc())
    ).all()
    factor = 1 + _markup(c) / 100
    lines = []
    for service, cost in rows:
        cost_d = Decimal(str(cost or 0)).quantize(CENT)
        lines.append(
            {
                "service": service,
                "cost": float(cost_d),
                "rebilled": float((cost_d * factor).quantize(CENT, ROUND_HALF_UP)),
            }
        )
    total = sum((Decimal(str(x["cost"])) for x in lines), Decimal(0))
    rebilled = sum((Decimal(str(x["rebilled"])) for x in lines), Decimal(0))
    set_tenant(db, org_id=partner.id, user_id=auth.user.id)
    return {
        "client": c.name,
        "month": month,
        "currency": c.default_currency,
        "markup_pct": float(_markup(c)),
        "lines": lines,
        "cost": float(total),
        "rebilled": float(rebilled),
        "margin": float(rebilled - total),
    }


@router.put("/api/v1/partner/branding")
def set_branding(body: BrandingIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    partner = _partner(db, auth)
    if not COLOR.match(body.primary_color):
        raise AppError("Use a colour like #2563eb.", code="bad_color")
    branding = {
        **((partner.settings or {}).get("branding") or {}),
        **body.model_dump(),
    }  # keeps the logo
    partner.settings = {**(partner.settings or {}), "branding": branding}
    db.flush()
    return body.model_dump()


@router.post("/api/v1/partner/link-codes", status_code=201, dependencies=[plan_feature("partner")])
def link_code(auth: Admin, db: SessionDep) -> dict[str, Any]:
    """A code a client's owner enters to put their organisation under this partner."""
    partner = _partner(db, auth)
    code = partners.new_link_code(partner)
    db.flush()
    return {"code": code, "valid_days": partners.CODE_DAYS}


@router.get("/api/v1/org/branding")
def branding(auth: CurrentAuth, db: SessionDep) -> dict[str, Any] | None:
    """The partner's branding shown to this organisation (or its own, for a partner)."""
    if auth.org_id is None:
        return None
    org = db.get(Organization, auth.org_id)
    if org is None:
        return None
    source = org
    if org.partner_id:
        set_tenant(db, bypass=True)
        source = db.get(Organization, org.partner_id) or org
    return ((source.settings or {}).get("branding")) or None


@router.post("/api/v1/org/partner", status_code=204)
def join_partner(body: LinkIn, auth: Owner, db: SessionDep) -> Response:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    if org.is_partner:
        raise AppError("A partner cannot be another partner's client.", code="is_partner")
    set_tenant(db, bypass=True)
    partner = partners.partner_for_code(db, body.code, use=True)
    if partner is None or partner.id == org.id:
        raise AppError("This partner code is not valid or has expired.", code="bad_code")
    org.partner_id = partner.id
    org.settings = {**(org.settings or {}), "partner": {"role": Role.ADMIN.value, "markup_pct": 0}}
    db.flush()
    return Response(status_code=204)


@router.delete("/api/v1/org/partner", status_code=204)
def leave_partner(auth: Owner, db: SessionDep) -> Response:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.partner_id = None
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "partner"}
    db.flush()
    return Response(status_code=204)


@router.put("/api/v1/platform/organizations/{org_id}/partner")
def mark_partner(
    org_id: uuid.UUID, body: PartnerFlag, auth: CurrentAuth, db: SessionDep
) -> dict[str, Any]:
    """Yugiverse staff only: turn partner mode on or off for an organisation."""
    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Only CostLens staff can do this.")
    set_tenant(db, bypass=True)
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    org.is_partner = body.is_partner
    db.flush()
    return {"id": str(org.id), "is_partner": org.is_partner}


# --- 8.13: logo, invitation links, partner sign-up -------------------------------------


@router.put("/api/v1/partner/logo")
async def upload_logo(request: Request, auth: Admin, db: SessionDep) -> dict[str, Any]:
    """Raw image body: PNG, WebP or SVG (sanitised), 200 KB at most."""
    from costlens.core.images import MAX_BYTES, ImageError, check_logo, data_url

    partner = _partner(db, auth)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BYTES:
            raise AppError(
                f"The image is too large ({MAX_BYTES // 1000} KB at most).", code="bad_image"
            )
    try:
        kind, data = check_logo(body)
    except ImageError as exc:
        raise AppError(str(exc), code="bad_image") from exc
    branding = dict((partner.settings or {}).get("branding") or {})
    branding["logo"] = data_url(kind, data)
    partner.settings = {**(partner.settings or {}), "branding": branding}
    db.flush()
    return {"type": kind, "bytes": len(data)}


@router.delete("/api/v1/partner/logo", status_code=204)
def remove_logo(auth: Admin, db: SessionDep) -> Response:
    partner = _partner(db, auth)
    branding = {
        k: v for k, v in ((partner.settings or {}).get("branding") or {}).items() if k != "logo"
    }
    partner.settings = {**(partner.settings or {}), "branding": branding}
    db.flush()
    return Response(status_code=204)


@router.get("/api/v1/partner-invites/{code}")
def invite_info(code: str, auth: CurrentAuth, db: SessionDep) -> dict[str, Any]:
    """Who is inviting (for the invitation page). Does not spend the code."""
    set_tenant(db, bypass=True)
    p = partners.partner_for_code(db, code)
    if p is None:
        raise AppError("This invitation is not valid or has expired.", status=404, code="bad_code")
    brand = (p.settings or {}).get("branding") or {}
    return {
        "partner": brand.get("display_name") or p.name,
        "logo": brand.get("logo"),
        "valid_days": partners.CODE_DAYS,
    }


class AcceptIn(BaseModel):
    organization_id: uuid.UUID | None = None  # link this organisation (you must own it)
    new_name: str | None = Field(default=None, min_length=1, max_length=200)  # or create one


@router.post("/api/v1/partner-invites/{code}/accept")
def accept_invite(code: str, body: AcceptIn, auth: CurrentAuth, db: SessionDep) -> dict[str, Any]:
    """Accept a partner's invitation: link an organisation you own, or create a new one."""
    if (body.organization_id is None) == (body.new_name is None):
        raise AppError("Choose an organisation or give a new name.", code="choose_one")
    if body.organization_id is not None:
        m = acc.get_membership(db, body.organization_id, auth.user.id)
        if m is None or m.role is not Role.OWNER:
            raise PermissionDeniedError("Only an owner can put an organisation under a partner.")
        set_tenant(db, bypass=True)
        org = db.get(Organization, body.organization_id)
        assert org is not None  # noqa: S101
    else:
        org = acc.create_organization(db, name=body.new_name or "", owner=auth.user)
        set_tenant(db, bypass=True)
    if org.is_partner:
        raise AppError("A partner cannot be another partner's client.", code="is_partner")
    partner = partners.partner_for_code(db, code, use=True)
    if partner is None or partner.id == org.id:
        raise AppError("This invitation is not valid or has expired.", code="bad_code")
    org.partner_id = partner.id
    org.settings = {**(org.settings or {}), "partner": {"role": Role.ADMIN.value, "markup_pct": 0}}
    if auth.api_key_id is None:  # open the organisation for the person who accepted
        acc.switch_organization(db, auth.session, org.id)
    db.flush()
    return {"organization_id": str(org.id), "partner": partner.name}


class ApplicationIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    website: str = Field(default="", max_length=300)
    message: str = Field(default="", max_length=2000)


@router.post("/api/v1/org/partner-application")
def apply_partner(
    body: ApplicationIn, auth: Owner, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Owners apply to become a partner (or become one at once, when self-serve)."""
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    if settings.partner_signup == "off":
        raise AppError(
            "Partner sign-up is closed on this installation.", status=403, code="signup_off"
        )
    if org.is_partner:
        return {"status": "approved"}
    if org.partner_id:
        raise AppError("A partner's client cannot become a partner.", code="is_client")
    application = {
        **body.model_dump(),
        "by": auth.user.email,
        "at": utcnow().isoformat(),
        "status": "pending",
    }
    if settings.partner_signup == "self_serve":
        org.is_partner = True
        application["status"] = "approved"
    org.settings = {**(org.settings or {}), "partner_application": application}
    db.flush()
    return {"status": application["status"]}


@router.get("/api/v1/platform/partner-applications")
def list_applications(auth: CurrentAuth, db: SessionDep) -> list[dict[str, Any]]:
    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Only CostLens staff can do this.")
    set_tenant(db, bypass=True)
    out = []
    for o in db.scalars(select(Organization).where(Organization.is_partner.is_(False))):
        a = (o.settings or {}).get("partner_application")
        if a and a.get("status") == "pending":
            out.append({"organization_id": str(o.id), "name": o.name, **a})
    return out


class Decision(BaseModel):
    approve: bool


@router.post("/api/v1/platform/partner-applications/{org_id}")
def decide_application(
    org_id: uuid.UUID, body: Decision, auth: CurrentAuth, db: SessionDep
) -> dict[str, Any]:
    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Only CostLens staff can do this.")
    set_tenant(db, bypass=True)
    org = db.get(Organization, org_id)
    a = ((org.settings if org else None) or {}).get("partner_application")
    if org is None or not a or a.get("status") != "pending":
        raise NotFoundError("Application not found.")
    org.is_partner = body.approve
    org.settings = {
        **(org.settings or {}),
        "partner_application": {
            **a,
            "status": "approved" if body.approve else "rejected",
            "decided_by": auth.user.email,
        },
    }
    db.flush()
    return {"organization_id": str(org.id), "status": org.settings["partner_application"]["status"]}
