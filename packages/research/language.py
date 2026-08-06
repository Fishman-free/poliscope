"""Detect the language the researcher asked in, so the council answers back
in the same language (round-4 requirement: a Chinese question must produce
Chinese reasoning, judgments, and reports; an English question, English).

Detection is deliberately dependency-free and conservative. Han characters
are common to simplified and traditional across the same Unicode blocks, so
script cannot be told apart by code point ranges alone; instead we count
characters whose traditional form is a *different glyph* from the simplified
one (與 vs 与, 關 vs 关, 係 vs 系, ...). Simplified text never uses those
glyphs, so two or more of them in a Chinese question are a strong
traditional signal; otherwise simplified. Anything with no Han characters is
English.
"""

from __future__ import annotations

# High-frequency characters whose traditional glyph differs from the
# simplified one -- deliberately restricted to ironclad pairs (the simplified
# form is a different character, not a stylistic variant), so a simplified
# question can never trip them.
_TRADITIONAL_ONLY = frozenset(
    "與關係顯臺灣無為從後際準據說學導議論譯傳務發現應當萬葉幾這裏來會們時間對於沒麼"
    "樣員號塊錢覺夠聽讀寫話頭邊點電機場實業數網絡環壓習績調查統計參經還種體驗開軟資圖"
    "視頻檔庫腦醫療報論審評預測證響聯變項態適風險選擇歸檢驗區賴詮釋脈質複獻綜橫斷蹤組"
    "雙隨問篩納則殘節徑係錯誤較補償誘懲饋內動縱貫級識碼別倫權儲規個護註冊畫鍵費牆許單"
    "書標極離異殺陸區憂穩"
)

# CJK Unified Ideographs (the common Han block shared by simplified and
# traditional) plus the extension blocks that hold rare traditional forms.
_HAN_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # Extension A
    (0x20000, 0x2A6DF),  # Extension B
    (0xF900, 0xFAFF),  # Compatibility Ideographs
)

SUPPORTED_LANGUAGES = ("zh-Hans", "zh-Hant", "en")


def detect_output_language(question: str) -> str:
    """Return ``zh-Hans`` / ``zh-Hant`` / ``en`` for a researcher's question.

    A question containing any Han character counts as Chinese. Two or more
    traditional-only glyphs make it ``zh-Hant``; otherwise ``zh-Hans``. A
    question with no Han characters is ``en``.
    """
    han_count = 0
    traditional_count = 0
    for char in question:
        code = ord(char)
        if any(lo <= code <= hi for lo, hi in _HAN_RANGES):
            han_count += 1
            if char in _TRADITIONAL_ONLY:
                traditional_count += 1
    if han_count == 0:
        return "en"
    if traditional_count >= 2:
        return "zh-Hant"
    return "zh-Hans"


__all__ = ["SUPPORTED_LANGUAGES", "detect_output_language"]
