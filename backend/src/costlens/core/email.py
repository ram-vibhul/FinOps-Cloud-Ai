"""Outgoing email: SMTP in production, console locally, in-memory in tests.

SMTP works with any provider (Amazon SES, Postmark, Mailgun, a customer's own
relay). Messages are sent from the ``email.send`` background job so a slow mail
server never slows a request; the job is retried on failure.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import Any, Protocol

from costlens.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    # [{"filename", "content_type", "data" (base64)}]: kept JSON-safe for the job queue
    attachments: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Email:
        return cls(**data)


class EmailSender(Protocol):
    def send(self, message: Email) -> None: ...


class MemorySender:
    def __init__(self) -> None:
        self.outbox: list[Email] = []

    def send(self, message: Email) -> None:
        self.outbox.append(message)


class ConsoleSender:
    """Local development: print the message (links included) to the log."""

    def send(self, message: Email) -> None:
        log.warning("EMAIL to=%s subject=%r\n%s", message.to, message.subject, message.text)


class SmtpSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        security: str,
        from_address: str,
        timeout: float = 15.0,
    ) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.security = security
        self.from_address = from_address
        self.timeout = timeout

    def build(self, message: Email) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self.from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = make_msgid(domain=self.from_address.rsplit("@", 1)[-1].strip("> "))
        if message.reply_to:
            mime["Reply-To"] = message.reply_to
        mime.set_content(message.text)
        if message.html:
            mime.add_alternative(message.html, subtype="html")
        for a in message.attachments:
            import base64

            maintype, _, subtype = a.get("content_type", "application/octet-stream").partition("/")
            mime.add_attachment(
                base64.b64decode(a["data"]),
                maintype=maintype,
                subtype=subtype,
                filename=a["filename"],
            )
        return mime

    def send(self, message: Email) -> None:
        context = ssl.create_default_context()
        if self.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context
            )
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with server:
            if self.security == "starttls":
                server.starttls(context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(self.build(message))


def build_sender(settings: Settings) -> EmailSender:
    if settings.smtp_host:
        return SmtpSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            security=settings.smtp_security,
            from_address=settings.email_from,
        )
    return ConsoleSender()


_sender: EmailSender | None = None


def configure_email(sender: EmailSender) -> None:
    global _sender  # noqa: PLW0603 - process-wide, set at startup
    _sender = sender


def email_sender() -> EmailSender:
    if _sender is None:
        raise RuntimeError("email is not configured")
    return _sender
