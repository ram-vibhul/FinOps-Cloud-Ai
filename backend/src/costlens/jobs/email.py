"""Background email delivery."""

from __future__ import annotations

from typing import Any

from costlens.core.email import Email, email_sender
from costlens.jobs.registry import job


@job("email.send", queue="email", retry=5)
def send_email(message: dict[str, Any]) -> None:
    email_sender().send(Email.from_dict(message))
