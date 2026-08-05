"""Password hashing and bearer-token handling for the account system.

Standard-library PBKDF2 only (zero new dependencies): the stored string is
``pbkdf2$iterations$salt$hash`` so the work factor can rise later without
invalidating old hashes, and tokens are stored as sha256 so a leaked database
does not leak live sessions.
"""

from __future__ import annotations

from packages.accounts.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2$")
    assert verify_password("correct horse battery staple", stored)


def test_wrong_password_fails() -> None:
    stored = hash_password("right-password")
    assert not verify_password("wrong-password", stored)


def test_same_password_hashes_differ_per_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")
    assert first != second
    assert verify_password("same password", first)
    assert verify_password("same password", second)


def test_garbage_stored_hash_is_false_not_crash() -> None:
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "")
    assert not verify_password("anything", "md5$1$aa$bb")


def test_token_is_hashed_before_persistence() -> None:
    token = generate_token()
    digest = hash_token(token)
    assert digest != token
    assert len(digest) == 64  # sha256 hex
    assert hash_token(token) == digest  # deterministic for lookup


def test_tokens_are_unique() -> None:
    first = generate_token()
    second = generate_token()
    assert first != second
    assert hash_token(first) != hash_token(second)
