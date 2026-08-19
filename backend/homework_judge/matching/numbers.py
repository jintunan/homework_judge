from __future__ import annotations

import re
import unicodedata

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_integer(value: str) -> int | None:
    if not value or any(char not in CHINESE_DIGITS and char not in {"十", "百"} for char in value):
        return None
    if value in CHINESE_DIGITS:
        return CHINESE_DIGITS[value]
    total = 0
    current = 0
    for char in value:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
    return total + current


def normalize_question_number(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"^\s*第\s*", "", text)
    text = re.sub(r"\s*题\s*$", "", text)
    text = re.sub(r"[\s、,:：．。]+$", "", text)
    text = text.strip()
    chinese = _chinese_integer(text)
    if chinese is not None:
        return str(chinese)
    text = text.replace("（", "(").replace("）", ")")
    match = re.fullmatch(r"(\d+)\s*[\(\-\.]\s*(\d+)\)?", text)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    return ""
