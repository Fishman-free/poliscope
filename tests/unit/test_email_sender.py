"""SMTP config parsing and the blocking send, exercised with a fake SMTP.

The send itself runs inside ``asyncio.to_thread``; the unit tests call the
sync ``_send_sync`` directly against a faked ``smtplib.SMTP`` so no real
network is ever touched, and verify the config mapping separately.
"""

from __future__ import annotations

import smtplib

import pytest

from packages.accounts.email_sender import (
    EmailServiceUnavailable,
    SmtpConfig,
    SmtpEmailSender,
)


class _FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[object] = []
        self.closed = False

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, message: object) -> None:
        self.sent.append(message)


def test_config_from_env_maps_all_variables() -> None:
    config = SmtpConfig.from_env(
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "2525",
            "SMTP_USER": "sender",
            "SMTP_PASSWORD": "secret",
            "SMTP_FROM": "no-reply@example.com",
        }
    )
    assert config.host == "smtp.example.com"
    assert config.port == 2525
    assert config.user == "sender"
    assert config.password == "secret"
    assert config.sender == "no-reply@example.com"
    assert config.is_configured


def test_config_from_env_defaults_port_and_optional_fields() -> None:
    config = SmtpConfig.from_env({"SMTP_HOST": "localhost", "SMTP_FROM": "a@b.co"})
    assert config.port == 587
    assert config.user is None
    assert config.password is None
    assert config.is_configured


def test_config_is_configured_requires_host_and_sender() -> None:
    assert not SmtpConfig.from_env({}).is_configured
    assert not SmtpConfig.from_env({"SMTP_HOST": "x"}).is_configured
    assert not SmtpConfig.from_env({"SMTP_FROM": "a@b.co"}).is_configured
    assert SmtpConfig.from_env(
        {"SMTP_HOST": "x", "SMTP_FROM": "a@b.co"}
    ).is_configured


def test_config_handles_a_bad_port_gracefully() -> None:
    config = SmtpConfig.from_env(
        {"SMTP_HOST": "x", "SMTP_PORT": "not-a-number", "SMTP_FROM": "a@b.co"}
    )
    assert config.port == 587
    assert config.is_configured


def test_send_sync_builds_and_sends_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSMTP("smtp.example.com", 587, 10)

    def _make(host: str, port: int, timeout: int) -> _FakeSMTP:
        fake.host, fake.port, fake.timeout = host, port, timeout
        return fake

    monkeypatch.setattr(smtplib, "SMTP", _make)
    config = SmtpConfig.from_env(
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "u",
            "SMTP_PASSWORD": "p",
            "SMTP_FROM": "no-reply@example.com",
        }
    )
    SmtpEmailSender(config)._send_sync("reader@example.com", "123456")

    assert fake.host == "smtp.example.com"
    assert fake.port == 587
    assert fake.started_tls is True
    assert fake.login_args == ("u", "p")
    assert len(fake.sent) == 1
    message = fake.sent[0]
    # The body is quoted-printable encoded (it contains Chinese); assert on
    # the decoded content, not the raw MIME serialisation.
    from email.message import Message

    assert isinstance(message, Message)
    payload = message.get_payload(decode=True)
    assert isinstance(payload, bytes)
    body = payload.decode("utf-8", errors="replace")
    assert "reader@example.com" in str(message["To"])
    assert "123456" in body


def test_send_sync_skips_auth_when_no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSMTP("localhost", 587, 10)
    monkeypatch.setattr(smtplib, "SMTP", lambda *_, **__: fake)
    config = SmtpConfig.from_env({"SMTP_HOST": "localhost", "SMTP_FROM": "a@b.co"})
    SmtpEmailSender(config)._send_sync("reader@example.com", "654321")
    assert fake.started_tls is False
    assert fake.login_args is None


def test_send_failure_wraps_in_email_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_: object, **__: object) -> _FakeSMTP:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    config = SmtpConfig.from_env(
        {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "u",
         "SMTP_PASSWORD": "wrong", "SMTP_FROM": "a@b.co"}
    )
    with pytest.raises(EmailServiceUnavailable):
        SmtpEmailSender(config)._send_sync("reader@example.com", "123456")
