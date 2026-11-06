"""Model-endpoint input normalisation and connectivity probing.

Two jobs, one shared by the settings API, task creation, and the worker:

**Normalisation.** Two real incidents each set the whole council absent. A
researcher typed ``https://platform.deepseek.com`` (the console portal) as the
API endpoint; later another typed a relay's bare host, whose site answers the
API path with its HTML dashboard. User input is therefore never stored
verbatim -- ``normalize_base_url`` strips whitespace and trailing slashes,
adds ``https://`` when the scheme is missing, rewrites known console-portal
hosts to their API endpoints, and completes a host-only URL with ``/v1`` (the
path every OpenAI-compatible vendor and relay serves ``chat/completions``
under; DeepSeek accepts it too). The correction is returned as a hint so the
UI can tell the researcher what changed instead of silently munging their
input.

**Probing.** ``probe_endpoint`` performs one minimal chat-completions call
against the endpoint the researcher just typed. The settings API refuses to
save a configuration that has not passed this probe (researcher requirement:
only a connection that actually works may be stored or changed), and the test
endpoint lets the UI run it on demand. A 2xx is not enough: the body must be
a chat-completion object, because a misconfigured path to a relay often
answers 200 with the site's HTML page. The probe is deliberately single-shot
with a short timeout -- no retries, no schema tooling -- because its job is to
fail fast with a reason the researcher can act on, not to be robust.

CLAUDE.md 16 holds here too: neither function ever returns the API key, and
error messages never quote it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

# Console portals that are NOT API endpoints. A researcher who pastes one of
# these into the Base URL field gets it rewritten to the corresponding API
# host, with a hint saying so. The only member today is the DeepSeek console
# (the incident above); other vendors get added here when they bite.
KNOWN_PORTAL_HOSTS: dict[str, str] = {
    "platform.deepseek.com": "api.deepseek.com",
}

# The probe must not hang a settings save. The real council call later runs
# with the full 60 s timeout; this is a liveness check, not a work call.
PROBE_TIMEOUT_SECONDS = 10.0

# What the worker falls back to when a task's config carries no model name.
# Must match apps/worker/jobs.py's `_gateway_for_task_config` default.
DEFAULT_MODEL_NAME = "deepseek-v4-flash"

MAX_DETAIL_CHARS = 200


def normalize_base_url(raw: str) -> tuple[str, str | None]:
    """Return (usable base_url, correction hint or None).

    ``raw`` is never stored or used as typed: whitespace and a trailing slash
    are trimmed, a missing scheme gets ``https://`` (``TaskModelConfig``
    rejects scheme-less values, so this also keeps the contract valid), a
    known console-portal host is rewritten to its API host, and a host-only
    URL gets ``/v1`` appended. That last step is not cosmetic: a relay's bare
    host answers ``/chat/completions`` with its dashboard HTML and a 200, so
    without the suffix every model call would fail *after* a green probe.
    The hint tells the UI what was changed, e.g. ``"已自动纠正
    platform.deepseek.com → api.deepseek.com；已自动补全 /v1"``. An empty
    input returns ``("", None)``.
    """
    cleaned = raw.strip().rstrip("/")
    if not cleaned:
        return "", None
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    hints: list[str] = []
    hostname = urlsplit(cleaned).hostname or ""
    replacement = KNOWN_PORTAL_HOSTS.get(hostname)
    if replacement is not None:
        # Replace the full netloc (host + optional port), not just the hostname.
        netloc = urlsplit(cleaned).netloc
        cleaned = cleaned.replace(f"//{netloc}", f"//{replacement}", 1)
        hints.append(f"已自动纠正 {hostname} → {replacement}")
    if urlsplit(cleaned).path in ("", "/"):
        # Host-only input: the API root. Every OpenAI-compatible vendor and
        # relay this project supports serves chat/completions under /v1
        # (DeepSeek accepts both /v1 and its bare host), while a bare relay
        # host is far more likely to be the site's landing page.
        cleaned = f"{cleaned}/v1"
        hints.append("已自动补全 /v1（OpenAI 兼容端点通常位于 /v1）")
    return cleaned, "；".join(hints) or None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of one connectivity probe. ``message`` is researcher-facing:
    success states the latency, failure states what to fix (bad key, unknown
    model, unreachable host). Never contains the API key.

    ``status_code`` is the vendor's HTTP status when the probe reached it;
    None for transport failures (timeout, DNS, reset). ``config_error`` marks
    a failure the researcher must fix even though the transport succeeded --
    today the 2xx-with-an-HTML-body case, where the URL points at a site or
    console rather than the chat-completions API. Callers use both to tell a
    configuration error from a vendor blip that the run itself can survive
    (5xx/429/timeout).
    """

    ok: bool
    message: str
    latency_ms: int | None = None
    status_code: int | None = None
    config_error: bool = False


# HTTP statuses that mean "this endpoint cannot work as configured" rather
# than "the vendor is having a moment". Used by the pre-start check: a dead
# key or an exhausted balance must stop before the council spends a run on
# absent seats, while a timeout or 5xx must not.
START_BLOCKING_STATUS_CODES = frozenset({401, 402, 403, 404})


def probe_blocks_start(result: ProbeResult) -> bool:
    """Whether a failed probe is a configuration error, not a transient one."""
    return not result.ok and (
        result.config_error or result.status_code in START_BLOCKING_STATUS_CODES
    )


async def probe_endpoint(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    """Verify (base_url, api_key, model_name) with one minimal call.

    ``client`` is injectable for tests (MockTransport); the caller usually
    omits it and this function owns the client it creates. The request is a
    plain ``chat/completions`` ping with a tiny token cap -- no tools, no
    thinking mode, no retries -- so a vendor that works for the council
    answers here, and a broken configuration fails here within 10 seconds
    with a reason.
    """
    normalized, _ = normalize_base_url(base_url)
    owns_client = client is None
    probe = client or httpx.AsyncClient(
        base_url=normalized or base_url,
        timeout=PROBE_TIMEOUT_SECONDS,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    body: dict[str, object] = {
        "model": model_name or DEFAULT_MODEL_NAME,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    started = time.monotonic()
    try:
        response = await probe.post("/chat/completions", json=body)
    except httpx.TimeoutException:
        return ProbeResult(
            False,
            f"连接超时：{PROBE_TIMEOUT_SECONDS:.0f} 秒内无响应，请检查 Base URL 与网络",
        )
    except httpx.HTTPError as error:
        return ProbeResult(False, f"无法连接：{error}")
    except Exception as error:
        # TLS, DNS, or whatever else a raw provider can throw; the class name
        # plus message beats a bare "connection failed" for a researcher
        # debugging a portal-vs-API mistake.
        detail = f"{error.__class__.__name__}: {error}"
        return ProbeResult(False, f"无法连接：{detail[:MAX_DETAIL_CHARS]}")
    finally:
        if owns_client:
            await probe.aclose()

    latency_ms = int((time.monotonic() - started) * 1000)
    if 200 <= response.status_code < 300:
        if not _looks_like_chat_completion(response):
            # A relay's landing page (or a console portal) answers 200 with
            # HTML. Accepting that as "connected" is worse than failing: the
            # settings save would pass, then every seat's call would fail on
            # the first JSON parse.
            return ProbeResult(
                False,
                "端点返回的不是 chat/completions 响应（HTTP 200）：Base URL 可能"
                "指向了站点首页或控制台，而不是 OpenAI 兼容 API。请填写完整的"
                " API 路径，中转站通常以 /v1 结尾",
                latency_ms,
                status_code=response.status_code,
                config_error=True,
            )
        return ProbeResult(
            True,
            f"连接成功（{latency_ms} ms）",
            latency_ms,
            status_code=response.status_code,
        )
    detail = _error_detail(response)
    if response.status_code in (401, 403):
        return ProbeResult(
            False,
            f"API Key 无效或无权访问（HTTP {response.status_code}）：{detail}",
            status_code=response.status_code,
            config_error=True,
        )
    if response.status_code == 402:
        # Vendor-neutral: 402 is Payment Required in the HTTP spec, and every
        # OpenAI-compatible vendor or relay that bills up front uses it.
        return ProbeResult(
            False,
            f"模型账户余额不足（HTTP 402）：{detail or '服务商要求付款'}。"
            "请充值后换用可用的 API Key 再开始研究",
            status_code=response.status_code,
            config_error=True,
        )
    if response.status_code == 404:
        return ProbeResult(
            False,
            f"模型名「{model_name or DEFAULT_MODEL_NAME}」不存在，或 Base URL 路径不对"
            f"（HTTP 404）：{detail}",
            status_code=response.status_code,
            config_error=True,
        )
    return ProbeResult(
        False,
        f"服务端返回错误（HTTP {response.status_code}）：{detail}",
        status_code=response.status_code,
    )


def _looks_like_chat_completion(response: httpx.Response) -> bool:
    """Whether a 2xx body is actually a chat-completion object.

    The one field every OpenAI-compatible answer carries is ``choices``. A
    relay's HTML landing page, a console redirect, or a generic JSON health
    check may all be 200, but none of them can serve the council; treating
    them as success is what lets a broken endpoint slip past the save gate.
    """
    try:
        data = response.json()
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("choices"), list)


def _error_detail(response: httpx.Response) -> str:
    """Extract the vendor's error message, bounded; '' when unparseable."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    return message.strip()[:MAX_DETAIL_CHARS]


__all__ = [
    "DEFAULT_MODEL_NAME",
    "KNOWN_PORTAL_HOSTS",
    "PROBE_TIMEOUT_SECONDS",
    "START_BLOCKING_STATUS_CODES",
    "ProbeResult",
    "normalize_base_url",
    "probe_blocks_start",
    "probe_endpoint",
]
