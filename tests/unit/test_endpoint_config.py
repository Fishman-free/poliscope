"""模型端点输入规范化与连接探测（连接测试按钮的后端核心）。

Regression story: a researcher saved the DeepSeek *console portal* as the API
endpoint, every seat's model call failed instantly, and the whole council ran
absent. Two rules came out of that incident, both covered here: user input is
never stored verbatim (``normalize_base_url`` rewrites portal hosts, adds
schemes, trims slashes), and a configuration must pass ``probe_endpoint``
before it may be saved or used.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from packages.models.endpoint_config import (
    normalize_base_url,
    probe_endpoint,
)


class TestNormalizeBaseUrl:
    def test_keeps_plain_api_url_untouched(self) -> None:
        assert normalize_base_url("https://api.deepseek.com") == (
            "https://api.deepseek.com",
            None,
        )

    def test_rewrites_console_portal_to_api_endpoint(self) -> None:
        normalized, hint = normalize_base_url("https://platform.deepseek.com")
        assert normalized == "https://api.deepseek.com"
        assert hint is not None
        assert "platform.deepseek.com" in hint and "api.deepseek.com" in hint

    def test_portal_rewrite_works_without_scheme(self) -> None:
        normalized, hint = normalize_base_url("platform.deepseek.com")
        assert normalized == "https://api.deepseek.com"
        assert hint is not None

    def test_adds_scheme_and_trims_trailing_slash(self) -> None:
        assert normalize_base_url("api.deepseek.com/") == (
            "https://api.deepseek.com",
            None,
        )

    def test_empty_input_stays_empty(self) -> None:
        assert normalize_base_url("   ") == ("", None)

    def test_preserves_custom_paths_and_ports(self) -> None:
        assert normalize_base_url("http://relay.example:8080/v1/") == (
            "http://relay.example:8080/v1",
            None,
        )


class _MockProbeClient:
    """Build an httpx client whose transport answers like the vendor."""

    @staticmethod
    def of(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.AsyncClient:
        # An injected client is fully caller-owned (the probe only adds the
        # Authorization header to clients it creates itself), so tests that
        # care about the header set it on the client, mirroring production.
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://mock",
            headers=headers,
        )


async def test_probe_success_reports_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers.get("authorization") == "Bearer sk-secret-123"
        body = request.read().decode()
        assert '"max_tokens":8' in body and '"ping"' in body
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-x",
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    result = await probe_endpoint(
        base_url="https://api.deepseek.com",
        api_key="sk-secret-123",
        model_name="deepseek-v4-flash",
        client=_MockProbeClient.of(
            handler,
            headers={"Authorization": "Bearer sk-secret-123"},
        ),
    )
    assert result.ok
    assert "连接成功" in result.message
    assert result.latency_ms is not None


async def test_probe_401_means_bad_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {"message": "Authentication Fails, Your api key is invalid"}
            },
        )

    result = await probe_endpoint(
        base_url="https://api.deepseek.com",
        api_key="sk-secret-123",
        model_name="m",
        client=_MockProbeClient.of(handler),
    )
    assert not result.ok
    assert "API Key 无效" in result.message
    # 回显红线：错误信息绝不能泄露 key 本身。
    assert "sk-secret-123" not in result.message


async def test_probe_404_means_unknown_model_or_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"message": "Model Not Exist"}},
        )

    result = await probe_endpoint(
        base_url="https://api.deepseek.com",
        api_key="sk-x",
        model_name="no-such-model",
        client=_MockProbeClient.of(handler),
    )
    assert not result.ok
    assert "no-such-model" in result.message
    assert "Model Not Exist" in result.message


async def test_probe_timeout_is_reported_quickly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    result = await probe_endpoint(
        base_url="https://api.deepseek.com",
        api_key="sk-x",
        model_name="m",
        client=_MockProbeClient.of(handler),
    )
    assert not result.ok
    assert "超时" in result.message


async def test_probe_connection_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await probe_endpoint(
        base_url="https://api.deepseek.com",
        api_key="sk-x",
        model_name="m",
        client=_MockProbeClient.of(handler),
    )
    assert not result.ok
    assert "无法连接" in result.message
    assert "sk-x" not in result.message
