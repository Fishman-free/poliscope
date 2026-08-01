from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

MIGRATOR_DATABASE_URL_ENV = "POLISCOPE_MIGRATOR_DATABASE_URL"
APP_DATABASE_URL_ENV = "POLISCOPE_APP_DATABASE_URL"
PROJECTOR_DATABASE_URL_ENV = "POLISCOPE_PROJECTOR_DATABASE_URL"
APP_ROLE = "poliscope_app"
PROJECTOR_ROLE = "poliscope_projector"
APP_PASSWORD_ENV = "POLISCOPE_APP_DATABASE_PASSWORD"
PROJECTOR_PASSWORD_ENV = "POLISCOPE_PROJECTOR_DATABASE_PASSWORD"


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Independently loaded database URLs for isolated identities."""

    @staticmethod
    def migrator_url_from_env(environ: Mapping[str, str] | None = None) -> str:
        return _identity_url(environ, MIGRATOR_DATABASE_URL_ENV)

    @staticmethod
    def app_url_from_env(environ: Mapping[str, str] | None = None) -> str:
        return _identity_url(environ, APP_DATABASE_URL_ENV)

    @staticmethod
    def projector_url_from_env(environ: Mapping[str, str] | None = None) -> str:
        return _identity_url(environ, PROJECTOR_DATABASE_URL_ENV)


def _identity_url(environ: Mapping[str, str] | None, name: str) -> str:
    values = os.environ if environ is None else environ
    return _required(values, name)


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value
