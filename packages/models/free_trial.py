"""Deployment-wide free-trial model configuration (round-7).

One vendor, one model, one quota per account: the deployment operator puts a
free-trial API key in the environment (``FREE_TRIAL_API_KEY``), and every
account may run the free-trial endpoint as its model settings exactly once
(``confirm-claims`` consumes a slot -- see apps/api/routers/tasks.py). The
key lives in the operator's environment and is written into the account's
``app_settings`` row exactly like any other saved key: stored server-side,
never echoed back by any endpoint (CLAUDE.md 16).

The trial vendor is **DeepSeek** (``deepseek-v4-flash``). It needs no
vendor-specific request fields: the Model Gateway already speaks DeepSeek's
own API, including its thinking-mode handling, so a free-trial task is
configured exactly like a researcher-owned DeepSeek task and takes the
ordinary gateway path. (Round-7 shipped a DashScope/qwen3.8-max trial, which
did need an extra ``enable_thinking`` body field -- that field has been
dropped along with the vendor. ``OpenAICompatibleConfig.extra_body`` remains
available on the gateway for endpoints that require non-OpenAI fields.)

The env var is deliberately vendor-neutral rather than vendor-named: the
round-7 name ``DASHSCOPE_API_KEY`` was tied to one vendor's SDK convention and
had to be renamed the moment the vendor changed.
"""

from __future__ import annotations

FREE_TRIAL_BASE_URL = "https://api.deepseek.com"
FREE_TRIAL_MODEL_NAME = "deepseek-v4-flash"
FREE_TRIAL_LIMIT = 1

# Environment variable holding the deployment's free-trial API key. Neutral by
# design (see module docstring); never a user-supplied value.
FREE_TRIAL_API_KEY_ENV = "FREE_TRIAL_API_KEY"

FREE_TRIAL_EXHAUSTED_MESSAGE = "免费额度已用尽，请填写你自己的api-key"
FREE_TRIAL_UNAVAILABLE_MESSAGE = "免费体验暂未开放"

__all__ = [
    "FREE_TRIAL_API_KEY_ENV",
    "FREE_TRIAL_BASE_URL",
    "FREE_TRIAL_EXHAUSTED_MESSAGE",
    "FREE_TRIAL_LIMIT",
    "FREE_TRIAL_MODEL_NAME",
    "FREE_TRIAL_UNAVAILABLE_MESSAGE",
]
