"""Payment provider webhooks (B.5): ``POST /api/v1/billing/webhooks/{razorpay|stripe}``."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ccradar.api.deps import SessionDep, SettingsDep
from ccradar.billing import webhooks
from ccradar.billing.plans import catalogue
from ccradar.core.errors import AppError

router = APIRouter(tags=["billing"], include_in_schema=False)


@router.post("/api/v1/billing/webhooks/{provider}")
async def payment_webhook(
    provider: str, request: Request, db: SessionDep, settings: SettingsDep
) -> dict[str, str]:
    secrets = {
        "razorpay": settings.razorpay_webhook_secret,
        "stripe": settings.stripe_webhook_secret,
    }
    secret = secrets.get(provider)
    if secret is None:
        raise HTTPException(status_code=404)
    body = await request.body()
    if len(body) > webhooks.MAX_BODY:
        raise AppError("Payload too large.", status=413, code="too_large")
    try:
        if provider == "razorpay":
            webhooks.verify_razorpay(
                secret.get_secret_value(), body, request.headers.get("x-razorpay-signature")
            )
            event = webhooks.parse_razorpay(body, request.headers.get("x-razorpay-event-id"))
        else:
            webhooks.verify_stripe(
                secret.get_secret_value(), body, request.headers.get("stripe-signature")
            )
            event = webhooks.parse_stripe(body)
    except webhooks.SignatureError as exc:
        raise AppError("Invalid signature.", status=400, code="bad_signature") from exc
    except ValueError as exc:  # not JSON
        raise AppError("Invalid payload.", status=400, code="bad_payload") from exc
    try:
        outcome = webhooks.receive(db, catalogue(settings), event)
    except IntegrityError:  # the same event delivered twice at the same moment
        db.rollback()
        outcome = "duplicate"
    return {"status": outcome}
