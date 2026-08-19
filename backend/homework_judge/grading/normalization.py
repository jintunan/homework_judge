from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SCORE_QUANTUM = Decimal("0.01")
_OPTION_SEPARATOR_RE = re.compile(r"[\s,，、;；/|]+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[。．.，,；;：:！？!?]+$")


def parse_decimal(value: Decimal | str | int) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError("invalid decimal score") from error
    if not parsed.is_finite():
        raise ValueError("score must be finite")
    return parsed


def quantize_score(value: Decimal | str | int) -> Decimal:
    parsed = parse_decimal(value)
    if parsed < 0:
        raise ValueError("score cannot be negative")
    return parsed.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def decimal_string(value: Decimal | str | int) -> str:
    return format(quantize_score(value), ".2f")


def normalize_text(
    value: str,
    *,
    ignore_case: bool = True,
    ignore_trailing_punctuation: bool = True,
) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = "".join(normalized.split())
    if ignore_trailing_punctuation:
        normalized = _TRAILING_PUNCTUATION_RE.sub("", normalized)
    return normalized.casefold() if ignore_case else normalized


@dataclass(frozen=True, slots=True)
class NormalizedOptions:
    options: tuple[str, ...]
    issues: tuple[str, ...] = ()


def normalize_options(value: str | list[str] | tuple[str, ...]) -> NormalizedOptions:
    if isinstance(value, str):
        compact = unicodedata.normalize("NFKC", value).strip().upper()
        option_only = re.sub(r"[\s,，、;；/|()（）\[\]【】{}]+", "", compact)
        if re.fullmatch(r"[A-H]+", option_only):
            parts = list(option_only)
        else:
            parts = [part for part in _OPTION_SEPARATOR_RE.split(compact) if part]
            if len(parts) == 1 and re.fullmatch(r"[A-H]+", parts[0]):
                parts = list(parts[0])
            else:
                # OCR may retain the printed text following a selected option,
                # such as "A.库仑力". Preserve the leading option but keep an
                # issue so the question is still sent to teacher review.
                leading = re.match(
                    r"^[\s(（\[【]*([A-H])[\s)）\]】.．、:：]+\S+",
                    compact,
                )
                if leading:
                    parts = [leading.group(1)]
                    trailing = compact[leading.end(1) :].strip()
                    return NormalizedOptions(
                        (leading.group(1),),
                        (f"OPTION_WITH_TRAILING_TEXT:{trailing}",),
                    )
    else:
        parts = [unicodedata.normalize("NFKC", str(part)).strip().upper() for part in value]

    issues: list[str] = []
    options: set[str] = set()
    for part in parts:
        if re.fullmatch(r"[A-H]", part):
            options.add(part)
        elif part:
            issues.append(f"UNRECOGNIZED_OPTION:{part}")
    return NormalizedOptions(tuple(sorted(options)), tuple(issues))


def matches_exact_or_synonym(
    student_answer: str,
    standard_answers: list[str] | tuple[str, ...],
    synonyms: list[str] | tuple[str, ...] = (),
) -> bool:
    normalized_student = normalize_text(student_answer)
    if not normalized_student:
        return False
    accepted = {
        normalize_text(answer)
        for answer in (*standard_answers, *synonyms)
        if normalize_text(answer)
    }
    return normalized_student in accepted
