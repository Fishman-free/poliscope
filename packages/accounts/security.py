"""Password hashing and bearer-token handling, standard library only.

Passwords are PBKDF2-HMAC-SHA256 with a per-password random salt and a
work-factor encoded in the stored string (``pbkdf2$iterations$salt$hash``),
so a later increase of the iterations keeps old hashes verifiable. Tokens are
``secrets.token_urlsafe``; only their sha256 is ever persisted, so a leaked
database does not leak live sessions (the bearer token is handed to the
client exactly once, at login/registration).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Hash a password into the portable ``pbkdf2$...`` format."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return (
        f"pbkdf2${_PBKDF2_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash string."""
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$", 3)
        if scheme != "pbkdf2":
            return False
        iterations_int = int(iterations)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            iterations_int,
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def generate_token() -> str:
    """A fresh bearer token. The plaintext leaves this module only on its
    way to the client; persistence sees only ``hash_token``."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "generate_token",
    "hash_password",
    "hash_token",
    "verify_password",
]
