"""Deployment-wide free-trial model configuration (round-7).

One vendor, one model, one quota per account: the deployment operator puts a
DashScope API key in the environment (``DASHSCOPE_API_KEY``), and every
account may save the free-trial endpoint as its model settings twice
(``confirm-claims`` consumes a slot -- see apps/api/routers/tasks.py). The
key lives in the operator's environment and is written into the account's
``app_settings`` row exactly like any other saved key: stored server-side,
never echoed back by any endpoint (CLAUDE.md 16).

``FREE_TRIAL_EXTRA_BODY`` carries the vendor-specific request field
(``enable_thinking``) the worker merges into the chat-completions body when
it builds the task gateway; the field is DashScope's own, not part of the
OpenAI dialect, so the ordinary DeepSeek/LongCat path never sees it.
"""

from __future__ import annotations

from collections.abc import Mapping

FREE_TRIAL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
FREE_TRIAL_MODEL_NAME = "qwen3.8-max"
FREE_TRIAL_LIMIT = 2

# Environment variable holding the deployment's DashScope API key. Named
# DASHSCOPE_API_KEY to match DashScope's own SDK convention (the same key a
# `dashscope` SDK user would export); never a user-supplied value.
FREE_TRIAL_API_KEY_ENV = "DASHSCOPE_API_KEY"

# Vendor-specific request fields merged into the chat-completions body. The
# worker skips its own DeepSeek-style `thinking` field when this is present,
# so the vendor's own toggle drives thinking mode instead (recorded
# assumption, CLAUDE.md 17).
FREE_TRIAL_EXTRA_BODY: Mapping[str, object] = {"enable_thinking": True}

FREE_TRIAL_EXHAUSTED_MESSAGE = "免费额度已用尽，请填写你自己的api-key"
FREE_TRIAL_UNAVAILABLE_MESSAGE = "免费体验暂未开放"

__all__ = [
    "FREE_TRIAL_API_KEY_ENV",
    "FREE_TRIAL_BASE_URL",
    "FREE_TRIAL_EXHAUSTED_MESSAGE",
    "FREE_TRIAL_EXTRA_BODY",
    "FREE_TRIAL_LIMIT",
    "FREE_TRIAL_MODEL_NAME",
    "FREE_TRIAL_UNAVAILABLE_MESSAGE",
]
