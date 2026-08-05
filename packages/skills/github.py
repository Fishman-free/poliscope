"""GitHub skill resolution and download, standard HTTP only.

A "skill" is a GitHub repository that carries a SKILL.md -- either at the
repository root or under a conventional skills directory. We never guess:
the URL is parsed structurally, the repository's contents are listed through
the GitHub API, and the first candidate that actually exists is downloaded.
Anything else fails with a reason (CLAUDE.md 7), never with a fabricated
"skill".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from packages.kernel.http_retry import send_with_retry

MAX_SKILL_BYTES = 2 * 1024 * 1024

_REPO_URL = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/(?P<branch>[^/]+)(?:/(?P<path>.*))?)?$"
)


class SkillFetchError(Exception):
    """Raised when a URL cannot become a downloaded skill."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SkillLocation:
    owner: str
    repo: str
    branch: str = "HEAD"
    subpath: str = ""


def parse_skill_url(url: str) -> SkillLocation:
    """Parse a GitHub URL into owner/repo/branch/subpath.

    Accepts ``https://github.com/owner/repo``, ``.git`` suffixes, and
    ``/tree/branch/path`` or ``/blob/branch/path`` forms. Everything else is
    refused with the reason -- a paste of a non-GitHub URL must not silently
    become "no skill".
    """
    match = _REPO_URL.fullmatch(url.strip())
    if match is None:
        raise SkillFetchError(
            "not a GitHub repository URL; expected "
            "https://github.com/owner/repo"
        )
    return SkillLocation(
        owner=match.group("owner"),
        repo=match.group("repo"),
        branch=match.group("branch") or "HEAD",
        subpath=(match.group("path") or "").strip("/"),
    )


def _frontmatter_name(markdown: str) -> str | None:
    """The ``name:`` field of a SKILL.md frontmatter block, if present."""
    if not markdown.startswith("---"):
        return None
    end = markdown.find("\n---", 3)
    if end == -1:
        return None
    for line in markdown[3:end].splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            return value or None
    return None


def _candidate_paths(location: SkillLocation) -> tuple[str, ...]:
    """SKILL.md candidates in the order they are tried.

    A URL pointing at a subdirectory resolves under that directory; otherwise
    the repo root and the two conventional skill directories are tried, so
    ``https://github.com/owner/repo`` works for both a root SKILL.md and a
    repo that keeps its skills in ``skills/`` or ``.claude/skills/``.
    """
    base = f"{location.subpath}/" if location.subpath else ""
    candidates = [f"{base}SKILL.md"]
    if not location.subpath:
        candidates.extend(
            [
                "skills/SKILL.md",
                ".claude/skills/SKILL.md",
            ]
        )
    return tuple(candidates)


async def fetch_skill_markdown(
    client: httpx.AsyncClient, url: str
) -> tuple[str, str]:
    """Download a skill's SKILL.md, returning ``(name, markdown)``.

    The name is the frontmatter ``name:`` when present, else the repository
    name -- honest, and stable for the worker's per-account directory.
    """
    location = parse_skill_url(url)

    async def _get(api_path: str) -> httpx.Response:
        try:
            response, _retries = await send_with_retry(
                lambda: client.get(
                    f"https://api.github.com/repos/{location.owner}/{location.repo}"
                    f"{api_path}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "poliscope-skills",
                    },
                )
            )
            return response
        except httpx.HTTPStatusError as error:
            # Status semantics live in the caller's loop: 404 means "try the
            # next candidate path", 403 means rate-limited, anything else is
            # reported as a fetch failure. send_with_retry raises on 4xx
            # before the status can be read, so the response is turned back
            # into a value for those checks to own.
            return error.response

    for candidate in _candidate_paths(location):
        response = await _get(f"/contents/{candidate}?ref={location.branch}")
        if response.status_code == 404:
            continue
        if response.status_code == 403:
            raise SkillFetchError(
                "GitHub API rate limit or access denied; "
                "check the repository's visibility"
            )
        if response.status_code != 200:
            raise SkillFetchError(
                f"GitHub returned HTTP {response.status_code} for "
                f"{location.owner}/{location.repo}"
            )
        try:
            payload = response.json()
            markdown = _decode_content(payload)
        except (ValueError, KeyError) as error:
            raise SkillFetchError("GitHub response was not a readable file") from error
        if len(markdown.encode("utf-8")) > MAX_SKILL_BYTES:
            raise SkillFetchError("skill exceeds the 2 MB limit")
        name = _frontmatter_name(markdown) or location.repo
        return name, markdown

    raise SkillFetchError(
        "no SKILL.md found in the repository "
        "(looked at root, skills/, .claude/skills/)"
    )


def _decode_content(payload: object) -> str:
    """Decode a GitHub contents API file payload (base64 body)."""
    if not isinstance(payload, dict):
        raise SkillFetchError("GitHub response was not a JSON object")
    encoding = payload.get("encoding")
    body = payload.get("content")
    if encoding != "base64" or not isinstance(body, str):
        raise SkillFetchError("GitHub response had no base64 content")
    import base64

    try:
        return base64.b64decode(body).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise SkillFetchError("skill file is not valid UTF-8") from error


__all__ = [
    "MAX_SKILL_BYTES",
    "SkillFetchError",
    "SkillLocation",
    "fetch_skill_markdown",
    "parse_skill_url",
]
