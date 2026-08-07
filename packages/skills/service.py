"""Skill lifecycle: download from GitHub, persist, keep on disk.

``add_from_url`` owns the whole "a URL becomes a remembered skill" story:
parse -> fetch -> write the SKILL.md into the account's directory -> record
the row. ``ensure_downloaded`` is the worker's side: a task enabled this
skill, so the file must exist on disk when the council runs -- a deleted or
moved file is re-downloaded, never silently skipped.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.skills.github import fetch_skills_from_repo
from packages.skills.repository import (
    SkillAlreadyAdded,
    SkillsRepository,
    StoredSkill,
)

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

    async def add_from_url(
        self,
        user_id: UUID,
        url: str,
        llm_assistant: Callable[..., Awaitable[object]] | None = None,
    ) -> tuple[StoredSkill, ...]:
        """Download a GitHub skill (or every skill of a collection) and
        remember them for the account.

        ``llm_assistant`` is the round-4 smart-install hook: when the repo has
        no SKILL.md at any conventional location or tree depth, the caller may
        provide an assistant (the API layer wires the account's model
        settings) that picks an existing markdown file or synthesises a skill
        summary instead of failing. A repo carrying several SKILL.md files
        installs **all** of them as separate skills (round-4 request: when
        the model cannot pick one, install everything).

        Returns the added skills; a skill already present for this account
        (same URL and name) is skipped rather than failing the batch. When
        every skill was already present, raises ``SkillAlreadyAdded``.
        """
        # Conditional keyword keeps the call compatible with test fakes that
        # monkeypatch fetch_skills_from_repo with the two-argument signature.
        if llm_assistant is None:
            fetched = await fetch_skills_from_repo(self._client, url)
        else:
            fetched = await fetch_skills_from_repo(
                self._client, url, llm_assistant=llm_assistant
            )
        repository = SkillsRepository(self._session)
        added: list[StoredSkill] = []
        # Distinct skills may share a frontmatter name; keep each directory
        # unique by suffixing collisions (name-2, name-3, ...).
        used: dict[str, int] = {}
        for name, markdown in fetched:
            base = _safe_name(name)
            count = used.get(base, 0)
            used[base] = count + 1
            directory_name = base if count == 0 else f"{base}-{count + 1}"
            directory = self._root / str(user_id) / directory_name
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "SKILL.md"
            target.write_text(markdown, encoding="utf-8")
            try:
                added.append(
                    await repository.add(
                        user_id,
                        name=name,
                        github_url=url.strip(),
                        downloaded_path=str(target),
                    )
                )
            except SkillAlreadyAdded:
                continue
        if not added:
            raise SkillAlreadyAdded(url.strip())
        return tuple(added)

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
        # A collection repo carries several skills: re-download the one whose
        # name this row records, falling back to the first entry.
        fetched = await fetch_skills_from_repo(self._client, skill.github_url)
        markdown = next(
            (body for name, body in fetched if name == skill.name),
            fetched[0][1],
        )
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
