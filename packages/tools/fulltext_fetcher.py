"""Downloads raw full-text PDF bytes directly from an open-access URL.

**Why this bypasses ``ToolGateway.execute()``.** ``HttpToolGateway``
(``packages/tools/http_gateway.py``) exists to move structured, JSON-shaped
vendor responses through an audited payload store -- the right shape for a
metadata lookup, the wrong shape for a user's raw PDF bytes. CLAUDE.md 16
requires upload/log/export handling that avoids leaking a user's source
material; routing multi-megabyte PDF bytes through the same JSON-payload
audit trail as a metadata lookup would risk exactly that leak. This is a
deliberate CLAUDE.md 17 deviation, recorded here rather than assumed: this
class talks HTTP directly (reusing the shared retry policy in
``packages.kernel.http_retry``, the same one ``HttpToolGateway`` uses), and
only ever exposes metadata -- URL, byte count, SHA-256 -- for the
ledger-visible process event. The bytes themselves are returned to the
caller in-memory and are never logged or persisted through an audited path.

**Why it needs no vendor credential.** Unlike the Model Gateway and Tool
Gateway, this class makes unauthenticated GET requests to publisher/repository
URLs already resolved by Unpaywall -- there is no API key of its own to read.
Its ``from_env`` therefore only recognizes ``POLISCOPE_FULLTEXT_*`` names and
must never fall back to this process's own ``ANTHROPIC_AUTH_TOKEN`` /
``ANTHROPIC_BASE_URL`` -- those belong to this Claude Code session's own
communication with Anthropic, not to any Poliscope vendor call.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from packages.kernel.http_retry import send_with_retry

TIMEOUT_ENV = "POLISCOPE_FULLTEXT_TIMEOUT_SECONDS"
MAX_BYTES_ENV = "POLISCOPE_FULLTEXT_MAX_BYTES"

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB safety cap against runaway downloads.

_PDF_MAGIC = b"%PDF"


class FullTextFetchError(RuntimeError):
    """The URL was reachable but its content is not a usable PDF.

    Distinct from a transport error: a wrong-content-type response or a file
    over the size cap means "this source is not fetchable," which callers
    should record as a gap rather than retry.
    """


@dataclass(frozen=True, slots=True)
class FullTextFetchResult:
    url: str
    content: bytes
    content_sha256: str
    size_bytes: int
    retries: int


@dataclass(frozen=True, slots=True)
class FullTextFetcherConfig:
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> FullTextFetcherConfig:
        """Build config from Poliscope-specific env vars only.

        Deliberately reads only the ``POLISCOPE_FULLTEXT_*`` names above. See
        the module docstring: this must never read ``ANTHROPIC_AUTH_TOKEN``
        or ``ANTHROPIC_BASE_URL`` as a fallback credential.
        """
        values = os.environ if environ is None else environ
        timeout = float(values.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_SECONDS)
        max_bytes = int(values.get(MAX_BYTES_ENV) or DEFAULT_MAX_BYTES)
        return cls(timeout_seconds=timeout, max_bytes=max_bytes)


class FullTextFetcher:
    """Downloads raw PDF bytes for :mod:`packages.papers.parser` to consume."""

    def __init__(
        self,
        config: FullTextFetcherConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or FullTextFetcherConfig()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=self._config.timeout_seconds
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FullTextFetcher:
        return cls(FullTextFetcherConfig.from_env(environ))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FullTextFetchResult:
        """Download ``url`` and return its raw bytes plus integrity metadata.

        Raises :class:`FullTextFetchError` if the response is over the
        configured size cap or does not look like a PDF. Transport errors and
        429/5xx are retried by :func:`send_with_retry` and propagate as-is
        once exhausted, matching ``HttpToolGateway``'s failure shape.
        """
        response, retries = await send_with_retry(lambda: self._client.get(url))
        content = response.content
        if len(content) > self._config.max_bytes:
            raise FullTextFetchError(
                f"response from {url} exceeds max_bytes="
                f"{self._config.max_bytes} ({len(content)} bytes)"
            )
        if not content.startswith(_PDF_MAGIC):
            raise FullTextFetchError(f"response from {url} is not a PDF")
        return FullTextFetchResult(
            url=url,
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            retries=retries,
        )


def fulltext_fetcher_from_env(
    environ: Mapping[str, str] | None = None,
) -> FullTextFetcher:
    """Build the fetcher from environment config.

    Unlike the Model Gateway, there is no "unset" state -- this always
    returns a working fetcher, since no credential is required to attempt an
    unauthenticated GET.
    """
    return FullTextFetcher.from_env(environ)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_BYTES_ENV",
    "TIMEOUT_ENV",
    "FullTextFetchError",
    "FullTextFetchResult",
    "FullTextFetcher",
    "FullTextFetcherConfig",
    "fulltext_fetcher_from_env",
]
