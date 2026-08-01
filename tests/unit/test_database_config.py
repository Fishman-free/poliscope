import pytest

from packages.kernel.config import (
    APP_PASSWORD_ENV,
    APP_ROLE,
    PROJECTOR_PASSWORD_ENV,
    PROJECTOR_ROLE,
    DatabaseConfig,
)


def test_database_urls_are_loaded_independently() -> None:
    assert DatabaseConfig.migrator_url_from_env(
        {"POLISCOPE_MIGRATOR_DATABASE_URL": "migrator-url"}
    ) == "migrator-url"
    assert DatabaseConfig.app_url_from_env(
        {"POLISCOPE_APP_DATABASE_URL": "app-url"}
    ) == "app-url"
    assert DatabaseConfig.projector_url_from_env(
        {"POLISCOPE_PROJECTOR_DATABASE_URL": "projector-url"}
    ) == "projector-url"


def test_database_role_and_password_environment_names_are_centralized() -> None:
    assert APP_ROLE == "poliscope_app"
    assert PROJECTOR_ROLE == "poliscope_projector"
    assert APP_PASSWORD_ENV == "POLISCOPE_APP_DATABASE_PASSWORD"
    assert PROJECTOR_PASSWORD_ENV == "POLISCOPE_PROJECTOR_DATABASE_PASSWORD"


def test_missing_identity_url_fails_without_fallback() -> None:
    with pytest.raises(
        ValueError,
        match="POLISCOPE_MIGRATOR_DATABASE_URL",
    ):
        DatabaseConfig.migrator_url_from_env(
            {"POLISCOPE_APP_DATABASE_URL": "must-not-be-used"}
        )
