from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import sympy

from .numeric import VerificationResult, VerificationStatus

_ALLOWED_CHARS_RE = re.compile(r"^[0-9A-Za-z+\-*/^().,\s=]+$")
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_ALLOWED_FUNCTIONS: dict[str, object] = {
    "sqrt": sympy.sqrt,
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    "exp": sympy.exp,
    "log": sympy.log,
    "Abs": sympy.Abs,
}
_CONSTANTS: dict[str, object] = {"pi": sympy.pi, "E": sympy.E}


def _normalize_formula(value: str) -> str:
    normalized = value.strip().replace("−", "-").replace("×", "*").replace("·", "*")
    normalized = normalized.replace("÷", "/").replace("^", "**")
    if normalized.count("=") == 1:
        left, right = normalized.split("=", 1)
        normalized = f"({left})-({right})"
    return normalized


def _parse_formula(value: str, *, max_length: int = 300) -> sympy.Expr:
    normalized = _normalize_formula(value)
    if not normalized or len(normalized) > max_length:
        raise ValueError("formula is empty or too long")
    if "__" in normalized or not _ALLOWED_CHARS_RE.fullmatch(normalized):
        raise ValueError("formula contains unsupported characters")
    names = set(_IDENTIFIER_RE.findall(normalized))
    local_dict: dict[str, object] = {**_ALLOWED_FUNCTIONS, **_CONSTANTS}
    for name in names:
        if name not in local_dict:
            local_dict[name] = sympy.Symbol(name, real=True)
    expression = sympy.sympify(normalized, locals=local_dict, evaluate=True)
    if not isinstance(expression, sympy.Expr):
        raise ValueError("formula did not produce an expression")
    if expression.count_ops() > 200:
        raise ValueError("formula is too complex")
    return expression


def _equivalent(student_answer: str, standard_answer: str) -> bool:
    student = _parse_formula(student_answer)
    standard = _parse_formula(standard_answer)
    return bool(sympy.simplify(student - standard) == 0)


def verify_formula_equivalence(
    student_answer: str,
    standard_answer: str,
    *,
    timeout_ms: int = 1500,
) -> VerificationResult:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="formula-verifier")
    future = executor.submit(_equivalent, student_answer, standard_answer)
    try:
        equivalent = future.result(timeout=timeout_ms / 1000)
    except TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return VerificationResult(VerificationStatus.UNABLE, "公式等价判断超时")
    except (ValueError, TypeError, sympy.SympifyError) as error:
        executor.shutdown(wait=False, cancel_futures=True)
        return VerificationResult(
            VerificationStatus.UNABLE,
            f"公式无法安全解析：{type(error).__name__}",
        )
    executor.shutdown(wait=True)
    return VerificationResult(
        VerificationStatus.EQUIVALENT if equivalent else VerificationStatus.NOT_EQUIVALENT,
        "公式数学等价" if equivalent else "公式不等价",
        _normalize_formula(student_answer),
        _normalize_formula(standard_answer),
    )
