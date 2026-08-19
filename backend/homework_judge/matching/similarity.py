from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz


def normalize_stem(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"^\s*(?:第\s*)?[\d一二三四五六七八九十百]+(?:\s*题)?[、.．:：]?\s*", "", text)
    text = re.sub(r"[\s，。！？、；;：“”\"'（）()\[\]【】]", "", text)
    return text


def stem_similarity(left: str, right: str) -> float:
    first, second = normalize_stem(left), normalize_stem(right)
    if not first or not second:
        return 0.0
    return round(
        (fuzz.ratio(first, second) * 0.55 + fuzz.token_set_ratio(first, second) * 0.45) / 100,
        4,
    )


def order_similarity(
    question_index: int,
    question_total: int,
    answer_index: int,
    answer_total: int,
) -> float:
    q_pos = question_index / max(1, question_total - 1)
    a_pos = answer_index / max(1, answer_total - 1)
    return round(max(0.0, 1.0 - abs(q_pos - a_pos)), 4)
