from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

import pint


class VerificationStatus(StrEnum):
    EQUIVALENT = "equivalent"
    NOT_EQUIVALENT = "not_equivalent"
    UNABLE = "unable"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    detail: str
    normalized_student: str = ""
    normalized_standard: str = ""


_UNIT_REGISTRY: pint.UnitRegistry[Any] = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
_VALUE_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<unit>.*)$"
)
_SCIENTIFIC_RE = re.compile(r"(?:×|x|X|\*)10\^?\(?([+-]?\d+)\)?")
_UNIT_ALIASES = {
    "米": "m",
    "厘米": "cm",
    "毫米": "mm",
    "千米": "km",
    "秒": "s",
    "毫秒": "ms",
    "千克": "kg",
    "克": "g",
    "牛顿": "N",
    "牛": "N",
    "焦耳": "J",
    "库仑": "C",
    "伏特": "V",
    "伏": "V",
    "安培": "A",
    "安": "A",
    "欧姆": "ohm",
    "欧": "ohm",
}


def _normalize_numeric_text(value: str) -> str:
    compact = value.strip().replace(" ", "").replace("，", ",")
    compact = compact.replace("−", "-").replace("＋", "+")
    compact = _SCIENTIFIC_RE.sub(lambda match: f"e{match.group(1)}", compact)
    for source in sorted(_UNIT_ALIASES, key=len, reverse=True):
        compact = compact.replace(source, _UNIT_ALIASES[source])
    return compact


def _split_value(value: str) -> tuple[Decimal, str] | None:
    match = _VALUE_RE.fullmatch(_normalize_numeric_text(value))
    if not match:
        return None
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number, match.group("unit").strip()


def verify_numeric_equivalence(
    student_answer: str,
    standard_answer: str,
    *,
    relative_tolerance: Decimal = Decimal("1e-9"),
    absolute_tolerance: Decimal = Decimal("1e-12"),
) -> VerificationResult:
    student = _split_value(student_answer)
    standard = _split_value(standard_answer)
    if student is None or standard is None:
        return VerificationResult(VerificationStatus.UNABLE, "无法解析数值或单位")
    student_value, student_unit = student
    standard_value, standard_unit = standard
    try:
        if bool(student_unit) != bool(standard_unit):
            return VerificationResult(
                VerificationStatus.UNABLE,
                "一个答案包含单位而另一个没有，无法自动确认",
                _normalize_numeric_text(student_answer),
                _normalize_numeric_text(standard_answer),
            )
        if student_unit:
            student_quantity = _UNIT_REGISTRY.Quantity(float(student_value), student_unit)
            converted = student_quantity.to(standard_unit)
            comparable_student = Decimal(str(converted.magnitude))
        else:
            comparable_student = student_value
        difference = abs(comparable_student - standard_value)
        scale = max(abs(comparable_student), abs(standard_value), Decimal(1))
        equivalent = difference <= max(absolute_tolerance, relative_tolerance * scale)
    except (pint.errors.PintError, ValueError, TypeError, OverflowError) as error:
        return VerificationResult(
            VerificationStatus.UNABLE,
            f"单位无法换算：{type(error).__name__}",
            _normalize_numeric_text(student_answer),
            _normalize_numeric_text(standard_answer),
        )
    return VerificationResult(
        VerificationStatus.EQUIVALENT if equivalent else VerificationStatus.NOT_EQUIVALENT,
        "数值和单位等价" if equivalent else "数值不等价",
        _normalize_numeric_text(student_answer),
        _normalize_numeric_text(standard_answer),
    )
