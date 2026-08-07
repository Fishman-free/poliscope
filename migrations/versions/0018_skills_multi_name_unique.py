"""Allow several skills from one repository: unique (user_id, github_url, name).

Revision ID: 0018_skills_multi_name_unique
Revises: 0017_output_language
Create Date: 2026-08-07

Migration 0013 keyed skills by ``(user_id, github_url)`` -- one row per
repository. Round-4 request: a *collection* repository (several SKILL.md
files, e.g. Imbad0202/academic-research-skills) now installs every skill as
its own row, so the same URL legitimately owns many rows distinguished by
name. The unique constraint moves from URL to (URL, name); an existing row
is untouched, and a re-add of the same URL+name still conflicts (409) as
before.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_skills_multi_name_unique"
down_revision: str | None = "0017_output_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "skills"


def upgrade() -> None:
    op.drop_constraint("uq_skills_user_url", TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_skills_user_name", TABLE, ["user_id", "github_url", "name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_skills_user_name", TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_skills_user_url", TABLE, ["user_id", "github_url"]
    )
