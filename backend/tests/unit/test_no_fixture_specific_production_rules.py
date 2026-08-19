from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_ROOTS = (
    ROOT / "backend" / "homework_judge" / "question_frames",
    ROOT / "backend" / "homework_judge" / "alignment",
    ROOT / "backend" / "homework_judge" / "recognition",
    ROOT / "backend" / "homework_judge" / "grading",
    ROOT / "backend" / "homework_judge" / "jobs",
)
FORBIDDEN_SAMPLE_MARKERS = (
    "q8_full_frame_oracle",
    "q11_three_blanks_oracle",
    "generic_blank_layout_cases.json",
    "电荷转移",
    "SECRET_STANDARD_ANSWER",
    "55621245",
)
QUESTION_NUMBER_FIELDS = {
    "detected_number",
    "normalized_number",
    "numberHint",
    "number_hint",
    "questionNumber",
    "question_number",
}


def _production_files() -> list[Path]:
    return sorted(path for root in PRODUCTION_ROOTS for path in root.rglob("*.py"))


@pytest.mark.parametrize("path", _production_files(), ids=lambda path: str(path.relative_to(ROOT)))
def test_production_code_never_imports_or_embeds_sample_oracles(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    lowered = source.casefold()
    hits = [marker for marker in FORBIDDEN_SAMPLE_MARKERS if marker.casefold() in lowered]
    assert hits == [], f"{path.relative_to(ROOT)} embeds sample-specific markers: {hits}"


def _references_question_number(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in QUESTION_NUMBER_FIELDS:
            return True
        if isinstance(child, ast.Attribute) and child.attr in QUESTION_NUMBER_FIELDS:
            return True
        if isinstance(child, ast.Subscript):
            key = child.slice
            if isinstance(key, ast.Constant) and key.value in QUESTION_NUMBER_FIELDS:
                return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "get" and child.args:
                key = child.args[0]
                if isinstance(key, ast.Constant) and key.value in QUESTION_NUMBER_FIELDS:
                    return True
    return False


def _numeric_literals(node: ast.AST) -> list[str]:
    values: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or isinstance(child.value, bool):
            continue
        value = child.value
        if isinstance(value, int) or (
            isinstance(value, str) and re.fullmatch(r"[（(]?\d+[）)]?", value.strip())
        ):
            values.append(str(value))
    return values


def test_question_number_fields_are_never_compared_with_sample_constants() -> None:
    violations: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if not any(_references_question_number(item) for item in operands):
                    continue
                literals = [
                    literal
                    for item in operands
                    if not _references_question_number(item)
                    for literal in _numeric_literals(item)
                ]
                if literals:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} compares a question "
                        f"number with {literals}"
                    )
            elif isinstance(node, ast.Match) and _references_question_number(node.subject):
                literals = [
                    literal for case in node.cases for literal in _numeric_literals(case.pattern)
                ]
                if literals:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} matches a question "
                        f"number against {literals}"
                    )
    assert violations == []
