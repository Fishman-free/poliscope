"""Verification-code generation, email normalization, and message mapping.

Pure logic -- no database, no SMTP. The atomic database behaviour
(send/consume with throttle and attempt limits) is covered by the
integration suite in tests/integration/test_email_verification_api.py.
"""

from __future__ import annotations

from packages.accounts.security import generate_verification_code
from packages.accounts.verification import (
    InvalidEmail,
    VerifyCodeOutcome,
    _verify_message,
    normalize_email,
)


def test_generated_code_is_six_digits() -> None:
    for _ in range(200):
        code = generate_verification_code()
        assert len(code) == 6
        assert code.isdigit()


def test_codes_are_not_all_identical() -> None:
    seen = {generate_verification_code() for _ in range(50)}
    assert len(seen) > 1


def test_normalize_lowercases_and_strips() -> None:
    assert normalize_email("  USER@Example.COM ") == "user@example.com"


def test_normalize_accepts_plain_addresses() -> None:
    assert normalize_email("a@b.co") == "a@b.co"
    assert normalize_email("first.last+tag@sub.example.org") == (
        "first.last+tag@sub.example.org"
    )


def test_normalize_rejects_garbage() -> None:
    for bad in ("", "   ", "not-an-email", "@missing-local", "user@", "user@host",
                "user@@host.com", "user host@example.com", "a" * 300 + "@x.com"):
        try:
            normalize_email(bad)
        except InvalidEmail:
            continue
        raise AssertionError(f"expected InvalidEmail for {bad!r}")


def test_verify_message_covers_every_outcome() -> None:
    for outcome in VerifyCodeOutcome:
        if outcome is VerifyCodeOutcome.OK:
            continue  # OK has no failure message
        assert _verify_message(outcome)
