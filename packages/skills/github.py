"""GitHub skill resolution and download, standard HTTP only.

A "skill" is a GitHub repository that carries a SKILL.md -- either at the
repository root, under a conventional skills directory, or at any depth of
the repository tree. Resolution order:

1. The conventional paths (root / ``skills/`` / ``.claude/skills/``) are
   tried first -- the fast path for well-formed skill repos.
2. The whole file tree is then listed recursively and any ``**/SKILL.md``
   wins, so a skill repo that nests its skill under an arbitrary directory
   still installs.
3. Only when the tree contains no SKILL.md at all does an *LLM assistant*
   (optional, provided by the caller) get a say: it reads the candidate
   markdown files (README etc.) and either selects one to install as the
   skill or synthesises a skill summary from them. A repo with no installable
   content still fails with a reason (CLAUDE.md 7), never with a fabricated
   "skill".
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from packages.kernel.http_retry import send_with_retry

MAX_SKILL_BYTES = 2 * 1024 * 1024

# A repo may contain many markdown files (docs/, translations, ...); the
# LLM-assist path only looks at the root-level markdown and one level of
# directories -- deep documentation is not a skill. SKILL.md anywhere in the
# tree is handled by the recursive scan instead.
MAX_TREE_MD_DEPTH = 2

# Defensive cap on the recursive tree listing. GitHub's own `truncated` flag
# is the authoritative limit signal; this is only a sanity guard against a
# response larger than any plausible skill collection.
MAX_TREE_ENTRIES = 50_000

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


async def _get_contents(
    client: httpx.AsyncClient,
    location: SkillLocation,
    api_path: str,
) -> httpx.Response:
    """One GitHub API GET with the project's retry policy.

    Status semantics live in the caller: 404 means "try the next candidate
    path", 403 means rate-limited, anything else is reported as a fetch
    failure. ``send_with_retry`` raises on 4xx before the status can be read,
    so the response is turned back into a value for those checks to own.
    """
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
        return error.response


def _checked_response(
    response: httpx.Response, location: SkillLocation
) -> dict[str, object]:
    """Turn a contents/tree response into its JSON payload or a fetch error."""
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
    payload = response.json()
    if not isinstance(payload, dict):
        raise SkillFetchError("GitHub response was not a JSON object")
    return payload


async def _fetch_file_markdown(
    client: httpx.AsyncClient,
    location: SkillLocation,
    path: str,
) -> tuple[str, str]:
    """Download one file via the contents API, returning ``(name, markdown)``."""
    response = await _get_contents(
        client, location, f"/contents/{path}?ref={location.branch}"
    )
    if response.status_code == 404:
        raise SkillFetchError(f"file {path} not found in the repository")
    payload = _checked_response(response, location)
    try:
        markdown = _decode_content(payload)
    except (ValueError, KeyError) as error:
        raise SkillFetchError("GitHub response was not a readable file") from error
    if len(markdown.encode("utf-8")) > MAX_SKILL_BYTES:
        raise SkillFetchError("skill exceeds the 2 MB limit")
    return _frontmatter_name(markdown) or location.repo, markdown


async def _tree_skill_paths(
    client: httpx.AsyncClient,
    location: SkillLocation,
) -> tuple[str, ...]:
    """List the repo's file tree recursively; return paths ending in SKILL.md.

    The ``git/trees`` API returns the whole tree in one call; ``truncated``
    means the response hit its size limit, which for our purposes is refused
    rather than silently partial (a huge repo is not a skill repo).
    """
    response = await _get_contents(
        client,
        location,
        f"/git/trees/{location.branch}?recursive=1",
    )
    if response.status_code == 404:
        return ()
    payload = _checked_response(response, location)
    if payload.get("truncated") is True:
        raise SkillFetchError(
            "repository tree is too large to scan for SKILL.md"
        )
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise SkillFetchError("GitHub tree response had no tree list")
    if len(tree) > MAX_TREE_ENTRIES:
        raise SkillFetchError(
            f"repository tree exceeds {MAX_TREE_ENTRIES} entries"
        )
    paths: list[str] = []
    for entry in tree:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str) and path.endswith("SKILL.md"):
            paths.append(path)
    # Shallowest SKILL.md first: a root-level one in a repo that also carries
    # a nested copy is the intended entry point.
    return tuple(sorted(paths, key=lambda path: (path.count("/"), path)))


async def _candidate_md_files(
    client: httpx.AsyncClient,
    location: SkillLocation,
) -> list[dict[str, str]]:
    """Markdown files the LLM assistant may consider (root + one level deep).

    Returns ``[{"path": ..., "snippet": ...}]`` with the first ~2 KB of each
    file, so the model can judge which one is the actual skill without us
    downloading every file fully.
    """
    response = await _get_contents(
        client, location, f"/contents/{location.subpath}?ref={location.branch}"
    )
    if response.status_code == 404:
        return []
    payload = _checked_response(response, location)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    candidates: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        if entry.get("type") == "dir":
            # One level deep: peek at each subdirectory's markdown files.
            sub = await _get_contents(
                client, location, f"/contents/{path}?ref={location.branch}"
            )
            if sub.status_code != 200:
                continue
            sub_payload = sub.json()
            if not isinstance(sub_payload, dict):
                continue
            for child in sub_payload.get("entries") or []:
                if not isinstance(child, dict):
                    continue
                child_path = str(child.get("path", ""))
                if child_path.endswith(".md") and len(candidates) < 12:
                    snippet = await _peek_markdown(client, location, child_path)
                    if snippet is not None:
                        candidates.append({"path": child_path, "snippet": snippet})
            continue
        if path.endswith(".md") and len(candidates) < 12:
            snippet = await _peek_markdown(client, location, path)
            if snippet is not None:
                candidates.append({"path": path, "snippet": snippet})
    return candidates


async def _peek_markdown(
    client: httpx.AsyncClient,
    location: SkillLocation,
    path: str,
) -> str | None:
    """First ~2 KB of a markdown file, or None when it cannot be read."""
    response = await _get_contents(
        client, location, f"/contents/{path}?ref={location.branch}"
    )
    if response.status_code != 200:
        return None
    try:
        return _decode_content(response.json())[:2000]
    except (SkillFetchError, ValueError, KeyError):
        return None


SkillAssistant = Callable[
    [httpx.AsyncClient, SkillLocation, list[dict[str, str]]],
    Awaitable[object],
]


async def fetch_skills_from_repo(
    client: httpx.AsyncClient,
    url: str,
    llm_assistant: SkillAssistant | None = None,
) -> tuple[tuple[str, str], ...]:
    """Download every skill a repository carries, ``((name, markdown), ...)``.

    A repo with a single SKILL.md returns one entry; a skill *collection*
    (several SKILL.md files, none at the root) returns **all** of them as
    separate skills -- round-4 request: when the model cannot pick one, every
    skill is installed rather than failing. The name is the frontmatter
    ``name:`` when present, else the repository name -- honest, and stable
    for the worker's per-account directory.

    Resolution order: conventional paths -> every ``**/SKILL.md`` in the tree
    -> (only when the tree has no SKILL.md at all) the LLM assistant's pick
    or synthesis. The assistant's returned object is duck-typed to
    ``{selected_path, name, markdown}`` so the packages/skills.llm_assist
    implementation and a test fake can both satisfy it without a shared
    schema import.
    """
    location = parse_skill_url(url)

    for candidate in _candidate_paths(location):
        try:
            return (await _fetch_file_markdown(client, location, candidate),)
        except SkillFetchError as error:
            if "not found" not in error.reason:
                raise
            # 404: try the next candidate.

    # Conventional paths all missed; scan the whole tree for SKILL.md files.
    skill_paths = await _tree_skill_paths(client, location)
    if skill_paths:
        # Every SKILL.md in the tree becomes its own skill, shallowest first.
        # The rare root-level SKILL.md alongside nested copies still comes
        # first (the tree scan sorts by depth), so a single-skill install
        # keeps working through the compatibility wrapper below.
        fetched: list[tuple[str, str]] = []
        for path in skill_paths:
            try:
                fetched.append(await _fetch_file_markdown(client, location, path))
            except SkillFetchError as error:
                if "not found" not in error.reason:
                    raise
        if fetched:
            return tuple(fetched)

    # No SKILL.md anywhere. An LLM assistant (when configured) may still
    # install the repo: pick the markdown file that actually carries the
    # skill, or synthesise a skill summary from what the repo does contain.
    if llm_assistant is not None:
        files = await _candidate_md_files(client, location)
        if files:
            choice = await llm_assistant(client, location, files)
            selected = getattr(choice, "selected_path", None)
            if isinstance(selected, str) and selected:
                try:
                    return (
                        await _fetch_file_markdown(client, location, selected),
                    )
                except SkillFetchError as error:
                    if "not found" not in error.reason:
                        raise
            generated = getattr(choice, "markdown", None)
            if isinstance(generated, str) and generated.strip():
                name = str(getattr(choice, "name", "") or location.repo)
                if len(generated.encode("utf-8")) > MAX_SKILL_BYTES:
                    raise SkillFetchError("skill exceeds the 2 MB limit")
                return (name, generated),

    raise SkillFetchError(
        "no SKILL.md found in the repository (looked at root, skills/, "
        ".claude/skills/, the whole file tree, and no LLM assistant could "
        "pick one)"
    )


async def fetch_skill_markdown(
    client: httpx.AsyncClient,
    url: str,
    llm_assistant: SkillAssistant | None = None,
) -> tuple[str, str]:
    """Download the *primary* skill of a repository, ``(name, markdown)``.

    Compatibility wrapper over :func:`fetch_skills_from_repo` returning the
    first entry (conventional-path install, the shallowest SKILL.md, or the
    LLM assistant's pick). Used by re-download paths where the skill is
    already known to be single.
    """
    fetched = await fetch_skills_from_repo(client, url, llm_assistant)
    return fetched[0]


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
