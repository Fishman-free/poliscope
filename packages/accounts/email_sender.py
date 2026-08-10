"""Registration (and password-reset) verification emails over SMTP.

Standard library only (``smtplib``): the send rate is throttled to roughly
five emails per address per day, far below what connection reuse would buy,
so pulling in ``aiosmtplib`` would be an unnecessary dependency (YAGNI). The
SMTP round-trip is blocking, so it runs on the default executor via
``asyncio.to_thread`` -- the event loop is never blocked.

Honesty invariants (CLAUDE.md 7): a missing or failing SMTP config raises
``EmailServiceUnavailable`` (mapped to 503 by the router) instead of silently
pretending the mail was sent. The caller rolls back the verification row on
failure, so a code that was never actually delivered does not consume quota.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

SMTP_HOST_ENV = "SMTP_HOST"
SMTP_PORT_ENV = "SMTP_PORT"
SMTP_USER_ENV = "SMTP_USER"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
SMTP_FROM_ENV = "SMTP_FROM"

DEFAULT_SMTP_PORT = 587

SMTP_TIMEOUT_SECONDS = 10


class EmailServiceUnavailable(Exception):
    """SMTP is not configured, or the send failed. Map to 503, never pretend."""


class EmailSender(Protocol):
    async def send_verification_code(
        self, to_email: str, code: str, purpose: str = "register"
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str | None = None
    port: int = DEFAULT_SMTP_PORT
    user: str | None = None
    password: str | None = None
    sender: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SmtpConfig:
        values = os.environ if environ is None else environ
        try:
            port = int(values.get(SMTP_PORT_ENV) or DEFAULT_SMTP_PORT)
        except ValueError:
            port = DEFAULT_SMTP_PORT
        return cls(
            host=values.get(SMTP_HOST_ENV) or None,
            port=port,
            user=values.get(SMTP_USER_ENV) or None,
            password=values.get(SMTP_PASSWORD_ENV) or None,
            sender=values.get(SMTP_FROM_ENV) or None,
        )

    @property
    def is_configured(self) -> bool:
        # host + sender are the hard requirements; user/password are optional
        # for an unauthenticated local relay.
        return bool(self.host and self.sender)


class SmtpEmailSender:
    """Sends verification emails through the configured SMTP server."""

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    async def send_verification_code(
        self, to_email: str, code: str, purpose: str = "register"
    ) -> None:
        await asyncio.to_thread(self._send_sync, to_email, code, purpose)

    def _send_sync(self, to_email: str, code: str, purpose: str = "register") -> None:
        host = self._config.host
        sender = self._config.sender
        # is_configured guarantees both; assert so the type narrows and a
        # mis-constructed config fails loudly rather than sending from None.
        assert host is not None
        assert sender is not None
        is_reset = purpose == "reset"
        message = EmailMessage()
        message["Subject"] = (
            "Poliscope 密码重置验证码 / Password reset code"
            if is_reset
            else "Poliscope 注册验证码 / Verification code"
        )
        message["From"] = sender
        message["To"] = to_email
        body_zh = (
            f"你的 Poliscope 密码重置验证码是 {code}，5 分钟内有效。"
            if is_reset
            else f"你的 Poliscope 注册验证码是 {code}，5 分钟内有效。"
        )
        body_en = (
            f"Your Poliscope password-reset code is {code}, valid for 5 minutes."
            if is_reset
            else f"Your Poliscope verification code is {code}, valid for 5 minutes."
        )
        message.set_content(f"{body_zh}\n\n{body_en}")
        try:
            with smtplib.SMTP(
                host, self._config.port, timeout=SMTP_TIMEOUT_SECONDS
            ) as smtp:
                if self._config.user or self._config.password:
                    smtp.starttls()
                    if self._config.user:
                        smtp.login(self._config.user, self._config.password or "")
                smtp.send_message(message)
        except smtplib.SMTPException as error:  # includes SMTPAuthenticationError
            raise EmailServiceUnavailable(str(error)) from error


__all__ = [
    "DEFAULT_SMTP_PORT",
    "EmailSender",
    "EmailServiceUnavailable",
    "SMTP_FROM_ENV",
    "SMTP_HOST_ENV",
    "SMTP_PASSWORD_ENV",
    "SMTP_PORT_ENV",
    "SMTP_USER_ENV",
    "SmtpConfig",
    "SmtpEmailSender",
]
