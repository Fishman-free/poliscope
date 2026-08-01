import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config

ENV_PATH = Path(__file__).resolve().parents[2] / "migrations" / "env.py"


def test_migration_env_prefers_migrator_url_over_app_url() -> None:
    environ = {
        "POLISCOPE_MIGRATOR_DATABASE_URL": "postgresql+asyncpg://migrator/db",
        "POLISCOPE_APP_DATABASE_URL": "postgresql+asyncpg://app/db",
    }
    with (
        patch.dict(os.environ, environ, clear=True),
        patch("alembic.context.config", Config(), create=True),
        patch("alembic.context.is_offline_mode", return_value=True),
        patch("alembic.context.configure") as configure,
        patch("alembic.context.begin_transaction"),
        patch("alembic.context.run_migrations"),
    ):
        runpy.run_path(str(ENV_PATH))
    assert configure.call_args.kwargs["url"] == environ[
        "POLISCOPE_MIGRATOR_DATABASE_URL"
    ]


def test_migration_env_requires_migrator_url_without_fallback() -> None:
    with (
        patch.dict(
            os.environ,
            {"POLISCOPE_APP_DATABASE_URL": "postgresql+asyncpg://app/db"},
            clear=True,
        ),
        patch("alembic.context.config", Config(), create=True),
        pytest.raises(ValueError, match="POLISCOPE_MIGRATOR_DATABASE_URL"),
    ):
        runpy.run_path(str(ENV_PATH))
