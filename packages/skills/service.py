"""Skill lifecycle: download from GitHub, persist, keep on disk.

``add_from_url`` owns the whole "a URL becomes a remembered skill" story:
parse -> fetch -> write the SKILL.md into the account's directory -> record
the row. ``ensure_downloaded`` is the worker's side: a task enabled this
skill, so the file must exist on disk when the council runs -- a deleted or
moved file is re-downloaded, never silently skipped.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.skills.github import fetch_skill_markdown
from packages.skills.repository import SkillsRepository, StoredSkill

SKILLS_ROOT_ENV = "POLISCOPE_SKILLS_ROOT"
DEFAULT_SKILLS_ROOT = "/tmp/poliscope-skills"


class SkillsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client: httpx.AsyncClient | None = None,
        root: str | None = None,
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._root = Path(root or os.environ.get(SKILLS_ROOT_ENV, DEFAULT_SKILLS_ROOT))

    async def add_from_url(self, user_id: UUID, url: str) -> StoredSkill:
        """Download a GitHub skill and remember it for the account."""
        name, markdown = await fetch_skill_markdown(self._client, url)
        directory = self._root / str(user_id) / _safe_name(name)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "SKILL.md"
        target.write_text(markdown, encoding="utf-8")
        repository = SkillsRepository(self._session)
        return await repository.add(
            user_id,
            name=name,
            github_url=url.strip(),
            downloaded_path=str(target),
        )

    async def ensure_downloaded(self, skill: StoredSkill) -> str:
        """Return the skill's markdown, re-downloading if the file is gone.

        The worker calls this for every skill a task enabled; a missing file
        (deleted on disk, moved, or a restored backup without the directory)
        is re-fetched from the recorded URL rather than reported as a gap --
        the researcher's choice to enable the skill stands until they remove
        it, and a disk hiccup must not silently drop it.
        """
        path = Path(skill.downloaded_path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        _, markdown = await fetch_skill_markdown(self._client, skill.github_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return markdown


def _safe_name(name: str) -> str:
    """A directory-safe skill name: letters, digits, and a small separator set."""
    cleaned = "".join(
        char if char.isalnum() or char in "-_." else "_" for char in name
    )
    return cleaned.strip("._") or "skill"


__all__ = [
    "DEFAULT_SKILLS_ROOT",
    "SKILLS_ROOT_ENV",
    "SkillsService",
]
