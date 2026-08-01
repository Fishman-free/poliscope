from __future__ import annotations

import re

SAFETY_HEADER = (
    "## 安全与局限声明\n\n"
    "1. 本系统为 AI 辅助科研工具，输出不构成医学诊断或医疗建议。\n"
    "2. 模型置信度不替代统计不确定性或专家判断。\n"
    "3. 证据覆盖与系统局限并排呈现，请结合原始文献审慎解读。\n"
)


def apply_safety_notice(content: str, is_mental_health: bool = False) -> str:
    if not is_mental_health:
        return content
    return SAFETY_HEADER + "\n" + content


_SIGNED_URL_PATTERN = re.compile(
    r"https?://[^\s\"']+?(?:X-Amz-Signature|Signature|AWSAccessKeyId)=[^\s\"']+",
    re.IGNORECASE,
)
_LOCAL_PATH_PATTERN = re.compile(r"file://[^\s\"']+|/[A-Za-z]:\[^\s\"']+")


def sanitize_export(text: str) -> str:
    text = _SIGNED_URL_PATTERN.sub("[REDACTED_SIGNED_URL]", text)
    text = _LOCAL_PATH_PATTERN.sub("[REDACTED_LOCAL_PATH]", text)
    return text
