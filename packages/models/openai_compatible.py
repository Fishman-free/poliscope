"""Model Gateway backed by an OpenAI-compatible chat-completions endpoint.

DeepSeek, LongCat, and the domestic relay/proxy stations (中转站) that front
various backends all speak the same dialect: POST ``/chat/completions`` with
``messages`` and forced ``tools`` function-calling, a bearer token, and a
``usage`` block with prompt/completion token counts. One implementation
against that dialect covers all three vendor choices; only ``base_url``,
model name, and price differ, and those are configuration, not code.

**Credential isolation (CLAUDE.md 8, 16).** This module reads its own,
Poliscope-specific environment variables. It must never read
``ANTHROPIC_AUTH_TOKEN`` or ``ANTHROPIC_BASE_URL`` -- those belong to this
process's own Claude Code session, not to whichever vendor account the
operator configures Poliscope against, and reusing them would silently spend
someone else's credential on the product's behalf. A missing
``POLISCOPE_MODEL_API_KEY`` is a configuration gap to report loudly, per
``packages.kernel.config``'s existing pattern -- never a fallback
opportunity.

**Failure shape.** A transport failure (connection refused, timeout, 5xx/429
exhausted after retry) is raised out of ``invoke`` -- ``GatewayDeliberator``
already treats any exception from the gateway as an absent seat, which is the
correct honest-gap behaviour for "the vendor could not be reached". A response
that *was* received but never produced schema-valid JSON, even after one
repair attempt, is not raised -- it comes back as a normal ``ModelResult``
with ``schema_status=QUARANTINED`` and an empty payload, because CLAUDE.md 10
requires a failed structured-output repair to be isolated rather than crash
the round.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from packages.kernel.contracts import FrozenDict
from packages.kernel.http_retry import send_with_retry
from packages.models.contracts import (
    ModelClass,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)
from packages.models.phase_schemas import PHASE_OUTPUT_JSON_SCHEMAS

API_KEY_ENV = "POLISCOPE_MODEL_API_KEY"
BASE_URL_ENV = "POLISCOPE_MODEL_BASE_URL"
DEFAULT_MODEL_ENV = "POLISCOPE_MODEL_NAME"
STRONG_MODEL_ENV = "POLISCOPE_MODEL_NAME_STRONG_REASONING"
MEDIUM_MODEL_ENV = "POLISCOPE_MODEL_NAME_MEDIUM"
LIGHTWEIGHT_MODEL_ENV = "POLISCOPE_MODEL_NAME_LIGHTWEIGHT"
PRICE_INPUT_ENV = "POLISCOPE_MODEL_PRICE_INPUT_PER_1M_USD"
PRICE_OUTPUT_ENV = "POLISCOPE_MODEL_PRICE_OUTPUT_PER_1M_USD"
TIMEOUT_ENV = "POLISCOPE_MODEL_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_SCHEMA_REPAIR_ATTEMPTS = 1
_PER_MILLION = Decimal(1_000_000)


class ModelGatewayConfigError(ValueError):
    """Raised when the gateway is configured incompletely, not just unset.

    A completely unset ``POLISCOPE_MODEL_API_KEY`` means "no vendor
    connected yet" and is handled by callers via :func:`gateway_from_env`
    returning ``None``. This error is for the case where configuration was
    attempted but is broken -- e.g. a key with no base URL.
    """


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ModelGatewayConfigError(
            f"Missing required environment variable: {name}. Set it to a "
            "Poliscope-specific vendor credential for DeepSeek, LongCat, or "
            "a relay/proxy station -- never reuse this process's own "
            "ANTHROPIC_AUTH_TOKEN."
        )
    return value


def _decimal_env(environ: Mapping[str, str], name: str) -> Decimal:
    value = environ.get(name)
    return Decimal("0") if not value else Decimal(value)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model_names: Mapping[ModelClass, str]
    price_input_per_1m_usd: Decimal = Decimal("0")
    price_output_per_1m_usd: Decimal = Decimal("0")
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> OpenAICompatibleConfig:
        values = os.environ if environ is None else environ
        api_key = _required(values, API_KEY_ENV)
        base_url = _required(values, BASE_URL_ENV)
        default_model = _required(values, DEFAULT_MODEL_ENV)
        model_names: dict[ModelClass, str] = {
            ModelClass.STRONG_REASONING: values.get(STRONG_MODEL_ENV, default_model),
            ModelClass.MEDIUM: values.get(MEDIUM_MODEL_ENV, default_model),
            ModelClass.LIGHTWEIGHT: values.get(LIGHTWEIGHT_MODEL_ENV, default_model),
        }
        timeout = float(values.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_SECONDS)
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model_names=model_names,
            price_input_per_1m_usd=_decimal_env(values, PRICE_INPUT_ENV),
            price_output_per_1m_usd=_decimal_env(values, PRICE_OUTPUT_ENV),
            timeout_seconds=timeout,
        )


class OpenAICompatibleModelGateway:
    """Satisfies ``ModelGateway`` against any OpenAI-compatible provider."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> OpenAICompatibleModelGateway:
        return cls(OpenAICompatibleConfig.from_env(environ))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def invoke(self, request: ModelRequest) -> ModelResult:
        schema = PHASE_OUTPUT_JSON_SCHEMAS.get(request.output_schema)
        if schema is None:
            raise ModelGatewayConfigError(
                f"no JSON schema registered for output_schema "
                f"{request.output_schema!r}"
            )
        model_name = self._config.model_names[request.model_class]
        messages: list[dict[str, str]] = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]

        started = time.monotonic()
        retries = 0
        input_tokens = 0
        output_tokens = 0
        payload: dict[str, object] = {}
        errors: list[str] = []
        repaired = False

        for attempt in range(MAX_SCHEMA_REPAIR_ATTEMPTS + 1):
            if attempt:
                repaired = True
                retries += 1
                messages.append(_repair_message(errors))
            content, usage, transport_retries = await self._call_once(
                model_name, messages, request.output_schema, schema
            )
            retries += transport_retries
            input_tokens += int(usage.get("prompt_tokens", 0) or 0)
            output_tokens += int(usage.get("completion_tokens", 0) or 0)
            payload, errors = _parse_and_validate(content, schema)
            if not errors:
                break

        schema_status = (
            SchemaStatus.QUARANTINED
            if errors
            else SchemaStatus.REPAIRED
            if repaired
            else SchemaStatus.OK
        )
        if schema_status is SchemaStatus.QUARANTINED:
            payload = {}
        latency_ms = int((time.monotonic() - started) * 1000)
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._cost(input_tokens, output_tokens),
            latency_ms=latency_ms,
            retries=retries,
            schema_status=schema_status,
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self._config.price_input_per_1m_usd
            + Decimal(output_tokens) * self._config.price_output_per_1m_usd
        ) / _PER_MILLION

    async def _call_once(
        self,
        model_name: str,
        messages: list[dict[str, str]],
        schema_name: str,
        schema: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any], int]:
        body = {
            "model": model_name,
            "messages": messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": schema_name,
                        "description": f"Emit the {schema_name} structured result.",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": schema_name}},
        }
        response, transport_retries = await send_with_retry(
            lambda: self._client.post("/chat/completions", json=body)
        )
        data = response.json()
        message = data["choices"][0]["message"]
        content = _extract_structured_content(message, schema_name)
        usage = data.get("usage") or {}
        return content, usage, transport_retries


def gateway_from_env(
    environ: Mapping[str, str] | None = None,
) -> OpenAICompatibleModelGateway | None:
    """Build the gateway from environment config, or ``None`` if unconfigured.

    A worker with no ``POLISCOPE_MODEL_API_KEY`` set has simply not been
    connected to a vendor yet -- CLAUDE.md 10's honest-gap behaviour, not an
    error, so every seat is recorded as absent. Once the key is present,
    every other required variable must resolve too, and
    ``OpenAICompatibleConfig.from_env`` raises ``ModelGatewayConfigError``
    clearly if it does not.
    """
    values = os.environ if environ is None else environ
    if not values.get(API_KEY_ENV):
        return None
    return OpenAICompatibleModelGateway.from_env(values)


def _repair_message(errors: list[str]) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "Your previous reply did not satisfy the required schema. "
            "Problems: " + "; ".join(errors) + ". Call the same tool again "
            "with corrected JSON that fixes every listed problem."
        ),
    }


def _extract_structured_content(message: Mapping[str, Any], schema_name: str) -> str:
    tool_calls = message.get("tool_calls") or []
    for call in tool_calls:
        function = call.get("function") or {}
        if function.get("name") == schema_name:
            return str(function.get("arguments", ""))
    # Some relays proxy models that ignore tool_choice and answer in content.
    content = message.get("content")
    return _strip_code_fence(content) if isinstance(content, str) else ""


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_and_validate(
    content: str, schema: Mapping[str, Any]
) -> tuple[dict[str, object], list[str]]:
    if not content:
        return {}, ["empty response"]
    try:
        candidate = json.loads(content)
    except ValueError as error:
        return {}, [f"not valid JSON: {error}"]
    if not isinstance(candidate, dict):
        return {}, ["response was not a JSON object"]
    errors = _validate(candidate, schema, path="$")
    return candidate, errors


def _validate(value: object, schema: Mapping[str, Any], path: str) -> list[str]:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        errors = [
            f"{path}.{key}: missing required field"
            for key in schema.get("required", ())
            if key not in value
        ]
        properties = schema.get("properties", {})
        for key, subschema in properties.items():
            if key in value:
                errors.extend(_validate(value[key], subschema, f"{path}.{key}"))
        return errors
    if schema_type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        item_schema = schema.get("items")
        if not item_schema:
            return []
        errors = []
        for index, item in enumerate(value):
            errors.extend(_validate(item, item_schema, f"{path}[{index}]"))
        return errors
    if schema_type == "string":
        return [] if isinstance(value, str) else [f"{path}: expected string"]
    if schema_type == "number":
        is_number = isinstance(value, int | float) and not isinstance(value, bool)
        return [] if is_number else [f"{path}: expected number"]
    if schema_type == "boolean":
        return [] if isinstance(value, bool) else [f"{path}: expected boolean"]
    return []


__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "ModelGatewayConfigError",
    "OpenAICompatibleConfig",
    "OpenAICompatibleModelGateway",
    "gateway_from_env",
]
