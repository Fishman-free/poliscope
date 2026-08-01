"""Create research, model call, and tool call foundations.

Revision ID: 0001_research_and_calls
Revises:
Create Date: 2026-08-01
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import (
    APP_PASSWORD_ENV,
    APP_ROLE,
    PROJECTOR_PASSWORD_ENV,
    PROJECTOR_ROLE,
)

revision: str = "0001_research_and_calls"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUSINESS_TABLES = (
    "research_tasks",
    "research_scopes",
    "atomic_claims",
    "model_calls",
    "tool_calls",
)


def _create_login_role(role_name: str, password_env: str) -> None:
    password = os.environ.get(password_env)
    if not password:
        raise RuntimeError(f"Missing required migration secret: {password_env}")
    connection = op.get_bind()
    if context.is_offline_mode():
        raise RuntimeError("Role creation requires an online migrator connection")
    alter_sql = connection.execute(
        sa.text(
            "SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', "
            ":role_name, :password)"
        ),
        {"role_name": role_name, "password": password},
    ).scalar_one()
    role_exists = connection.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
        {"role_name": role_name},
    ).scalar_one_or_none()
    if role_exists is not None:
        memberships = connection.execute(
            sa.text(
                "SELECT 1 FROM pg_auth_members members "
                "JOIN pg_roles member_role ON member_role.oid = members.member "
                "JOIN pg_roles granted_role ON granted_role.oid = members.roleid "
                "WHERE member_role.rolname = :role_name "
                "OR granted_role.rolname = :role_name LIMIT 1"
            ),
            {"role_name": role_name},
        ).scalar_one_or_none()
        if memberships is not None:
            raise RuntimeError(
                f"Database role {role_name} must have no role memberships"
            )
    if role_exists is None:
        create_sql = connection.execute(
            sa.text(
                "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', "
                ":role_name, :password)"
            ),
            {"role_name": role_name, "password": password},
        ).scalar_one()
        connection.exec_driver_sql(create_sql)
    connection.exec_driver_sql(alter_sql)


def _grant_runtime_permissions() -> None:
    connection = op.get_bind()
    database_name = connection.execute(
        sa.text("SELECT current_database()")
    ).scalar_one()
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    quoted_tables = ", ".join(BUSINESS_TABLES)
    op.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {APP_ROLE}"))
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
    op.execute(
        sa.text(f"REVOKE TEMPORARY ON DATABASE {quoted_database} FROM {APP_ROLE}")
    )
    op.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {quoted_tables} TO {APP_ROLE}"
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}"))
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {PROJECTOR_ROLE}"))
    op.execute(
        sa.text(f"REVOKE TEMPORARY ON DATABASE {quoted_database} FROM {PROJECTOR_ROLE}")
    )
    op.execute(sa.text(f"REVOKE ALL ON {quoted_tables} FROM {PROJECTOR_ROLE}"))


def upgrade() -> None:
    _create_login_role(APP_ROLE, APP_PASSWORD_ENV)
    _create_login_role(PROJECTOR_ROLE, PROJECTOR_PASSWORD_ENV)

    op.create_table(
        "research_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("wall_clock_minutes", sa.Integer(), nullable=False),
        sa.Column("model_cost_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("tool_call_limit", sa.Integer(), nullable=False),
        sa.Column("source_limit", sa.Integer(), nullable=False),
        sa.Column(
            "user_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_research_tasks_status", "research_tasks", ["status"])
    op.create_index("ix_research_tasks_created_by", "research_tasks", ["created_by"])
    op.create_index("ix_research_tasks_created_at", "research_tasks", ["created_at"])

    op.create_table(
        "research_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("populations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("regions", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_until", sa.Date(), nullable=False),
        sa.Column("evidence_priorities", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("allow_preprints", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_research_scopes_status", "research_scopes", ["status"])
    op.create_index("ix_research_scopes_created_by", "research_scopes", ["created_by"])
    op.create_index("ix_research_scopes_created_at", "research_scopes", ["created_at"])

    op.create_table(
        "atomic_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("falsification_condition", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_atomic_claims_task_id", "atomic_claims", ["task_id"])
    op.create_index("ix_atomic_claims_status", "atomic_claims", ["status"])
    op.create_index("ix_atomic_claims_created_by", "atomic_claims", ["created_by"])
    op.create_index("ix_atomic_claims_created_at", "atomic_claims", ["created_at"])

    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False),
        sa.Column("model_class", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("schema_status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_task_id", "model_calls", ["task_id"])
    op.create_index("ix_model_calls_status", "model_calls", ["status"])
    op.create_index("ix_model_calls_created_by", "model_calls", ["created_by"])
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=255), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("schema_status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_calls_task_id", "tool_calls", ["task_id"])
    op.create_index("ix_tool_calls_status", "tool_calls", ["status"])
    op.create_index("ix_tool_calls_created_by", "tool_calls", ["created_by"])
    op.create_index("ix_tool_calls_created_at", "tool_calls", ["created_at"])

    _grant_runtime_permissions()


def downgrade() -> None:
    connection = op.get_bind()
    database_name = connection.execute(
        sa.text("SELECT current_database()")
    ).scalar_one()
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    quoted_tables = ", ".join(BUSINESS_TABLES)
    op.execute(sa.text(f"REVOKE ALL ON {quoted_tables} FROM {APP_ROLE}"))
    op.execute(sa.text(f"REVOKE ALL ON {quoted_tables} FROM {PROJECTOR_ROLE}"))
    op.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {APP_ROLE}"))
    op.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}"))
    op.execute(
        sa.text(f"REVOKE TEMPORARY ON DATABASE {quoted_database} FROM {APP_ROLE}")
    )
    op.execute(
        sa.text(f"REVOKE TEMPORARY ON DATABASE {quoted_database} FROM {PROJECTOR_ROLE}")
    )
    for table_name in reversed(BUSINESS_TABLES):
        op.drop_table(table_name)
