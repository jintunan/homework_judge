from __future__ import annotations

import argparse
import hashlib
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

import generate_grading_casia_exam as math_writer
import generate_grading_full_exam as base
import generate_grading_webfont_exam as webfont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "grading_benchmark"
    / "physics_unit_55662305_reference_layout_v9"
)


# Multiple-choice answers use the visual center of the printed parentheses.
# Coordinates are measured on the 1489 x 2105 render of the original PDF.
OBJECTIVE_SLOTS: dict[str, tuple[int, int, int, int, int]] = {
    "q1": (1, 837, 519, 56, 31),
    "q2": (1, 692, 937, 56, 31),
    "q3": (1, 1013, 1432, 56, 31),
    "q4": (2, 609, 285, 56, 31),
    "q5": (2, 724, 1050, 56, 30),
    "q6": (2, 1234, 1387, 56, 29),
    "q7": (3, 280, 352, 56, 29),
    "q8": (3, 220, 1140, 56, 29),
}


# Fill-in answers use the printed underline span and its baseline.  This keeps
# short text centered on the line and scales long formulas to the available gap.
SHORT_SLOTS: dict[str, list[tuple[int, int, int, int, int]]] = {
    "q9": [
        (3, 671, 790, 1737, 28),
        (3, 180, 299, 1872, 27),
        (3, 711, 830, 1872, 27),
    ],
    "q10": [
        (4, 805, 924, 296, 24),
        (4, 989, 1108, 296, 28),
    ],
    "q11": [
        (4, 455, 694, 566, 27),
        (4, 210, 389, 634, 27),
        (4, 968, 1117, 769, 27),
    ],
    "q12": [
        (4, 545, 664, 1827, 28),
        (5, 1063, 1152, 296, 27),
        (5, 955, 1074, 708, 23),
    ],
}


# These are display transcriptions, not comments about the grading result.  Wrong
# variants keep the student's wrong expression/result but never print labels such
# as "calculation error" or "decimal point error" beside the work.
REFERENCE_LINES: dict[str, dict[str, list[str]]] = {
    "q13": {
        "correct": [
            "解：",
            "(1) 平衡时有",
            "tan30°=F/(mg)",
            "F=mg tan30°=√3mg/3",
            "(2) A、B处电场方向相同",
            "E=kq/(2x)²+2kq/(4x)²",
            "E=3kq/(8x²)",
            "方向水平向右",
            "(3) 由F=q'E得",
            "q'=F/E=8√3mgx²/(9kq)",
        ],
        "missing_step": [
            "解：",
            "(1) F=mg tan30°=√3mg/3",
            "(2) E=3kq/(8x²)",
            "方向向右",
        ],
        "calculation_error": [
            "解：",
            "(1) 平衡时有",
            "F=mg tan30°=√3mg/3",
            "(2) E=kq/(2x)²+2kq/(4x)²",
            "E=5kq/(8x²)",
            "方向向右",
            "(3) q'=F/E",
            "q'=8√3mgx²/(15kq)",
        ],
        "concept_error": [
            "解：",
            "(1) F=mg",
            "(2) E=0",
            "(3) q'=0",
        ],
        "final_only": [
            "解：",
            "(1) F=√3mg/3",
            "(2) E=3kq/(8x²)",
            "方向向右",
            "(3) q'=8√3mgx²/(9kq)",
        ],
        "blank": [],
    },
    "q14": {
        "correct": [
            "解：",
            "t=l/v=1.0×10^-2 s",
            "由qE=ma得",
            "a=100 m/s²",
            "(1) s=at²/2=5.0×10^-3 m",
            "出场时 vx=at",
            "s'=h·vx/v=0.05 m",
            "(2) d=2(s+s')=0.11 m",
        ],
        "missing_step": [
            "解：",
            "t=l/v=0.01 s",
            "a=qE/m=100 m/s²",
            "(1) s=at²/2=5×10^-3 m",
            "(2) d=0.11 m",
        ],
        "calculation_error": [
            "解：",
            "t=l/v=0.01 s",
            "qE=ma",
            "a=100 m/s²",
            "(1) s=at²/2=5×10^-2 m",
            "s'=h·at/v=0.05 m",
            "(2) d=2(s+s')=0.20 m",
        ],
        "concept_error": [
            "解：",
            "(1) s=vt=2×0.01=0.02 m",
            "(2) d=2s=0.04 m",
        ],
        "final_only": [
            "(1) s=5×10^-3 m",
            "(2) d=0.11 m",
        ],
        "blank": [],
    },
    "q15": {
        "correct": [
            "解：",
            "(1) 初始平衡时",
            "(mA+mB)g sin30°=kqBqC/L²",
            "qB=2.0×10^-5 C",
            "B带正电",
            "(2) A、B分离时 BC=3 m",
            "对B列牛顿第二定律",
            "kqBqC/3²-mB g sin30°=mB a",
            "a=2.0 m/s²",
            "(3) 1=at²/2",
            "t=1.0 s",
            "(4) 对A有",
            "F-mA g sin30°=mA a",
            "F=3.01 N",
        ],
        "missing_step": [
            "解：",
            "(1) qB=2×10^-5 C",
            "B带正电",
            "(2) a=2.0 m/s²",
            "(3) 1=at²/2",
            "t=1.0 s",
            "(4) F=3.01 N",
        ],
        "calculation_error": [
            "解：",
            "(1) 由平衡条件得",
            "qB=2×10^-5 C",
            "B带正电",
            "(2) 分离时有",
            "a=2.0 m/s²",
            "(3) 1=at²/2",
            "t=2.0 s",
            "(4) F-mA g sin30°=mA a",
            "F=3.01 N",
        ],
        "concept_error": [
            "解：",
            "(1) B带负电",
            "qB=2×10^-5 C",
            "(2) a=g sin30°=5 m/s²",
            "(3) t=√(2/5) s",
            "(4) F=mA a=2.15 N",
        ],
        "partial": [
            "解：",
            "(1) qB=2×10^-5 C",
            "B带正电",
            "(2) 分离时 BC=3 m",
            "a=2.0 m/s²",
        ],
        "blank": [],
    },
}


# Scores are derived from the visible work in REFERENCE_LINES.  Identical
# visible evidence receives identical credit, regardless of variant name.
REFERENCE_RUBRIC_SCORES: dict[str, dict[str, dict[str, int]]] = {
    "q13": {
        "correct": {"q13_r1": 3, "q13_r2": 4, "q13_r3": 3},
        "missing_step": {"q13_r1": 3, "q13_r2": 2, "q13_r3": 0},
        "calculation_error": {"q13_r1": 3, "q13_r2": 2, "q13_r3": 1},
        "concept_error": {"q13_r1": 0, "q13_r2": 0, "q13_r3": 0},
        "final_only": {"q13_r1": 2, "q13_r2": 2, "q13_r3": 2},
        "blank": {"q13_r1": 0, "q13_r2": 0, "q13_r3": 0},
    },
    "q14": {
        "correct": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 2, "q14_r4": 3, "q14_r5": 3},
        "missing_step": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 2, "q14_r4": 0, "q14_r5": 1},
        "calculation_error": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 1, "q14_r4": 3, "q14_r5": 1},
        "concept_error": {"q14_r1": 1, "q14_r2": 0, "q14_r3": 0, "q14_r4": 0, "q14_r5": 0},
        "final_only": {"q14_r1": 0, "q14_r2": 0, "q14_r3": 1, "q14_r4": 0, "q14_r5": 1},
        "blank": {"q14_r1": 0, "q14_r2": 0, "q14_r3": 0, "q14_r4": 0, "q14_r5": 0},
    },
    "q15": {
        "correct": {"q15_r1": 5, "q15_r2": 5, "q15_r3": 3, "q15_r4": 5},
        "missing_step": {"q15_r1": 3, "q15_r2": 2, "q15_r3": 3, "q15_r4": 2},
        "calculation_error": {"q15_r1": 3, "q15_r2": 2, "q15_r3": 1, "q15_r4": 5},
        "concept_error": {"q15_r1": 2, "q15_r2": 0, "q15_r3": 1, "q15_r4": 0},
        "partial": {"q15_r1": 3, "q15_r2": 3, "q15_r3": 0, "q15_r4": 0},
        "blank": {"q15_r1": 0, "q15_r2": 0, "q15_r3": 0, "q15_r4": 0},
    },
}


def _reference_responses(
    responses: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(responses)
    for question_id in ("q13", "q14", "q15"):
        variant = str(result[question_id]["variant"])
        lines = list(REFERENCE_LINES[question_id][variant])
        rubric_scores = dict(REFERENCE_RUBRIC_SCORES[question_id][variant])
        result[question_id]["lines"] = lines
        result[question_id]["transcription"] = "\n".join(lines)
        result[question_id]["rubric_scores"] = rubric_scores
        result[question_id]["score"] = sum(rubric_scores.values())
    return result


def _is_formula_line(text: str) -> bool:
    if not text:
        return False
    if text.startswith("(") and len(text) >= 3 and text[1].isdigit():
        remainder = text[3:].lstrip()
        if remainder and remainder[0].isascii() and not remainder[0].isdigit():
            return "=" in remainder
        return False
    return "=" in text and (text[0].isascii() or text[0] in "√θ")


def _render_answer(
    text: str,
    writer: webfont.WebFontWriter,
    size: int,
    seed: int,
) -> Image.Image:
    fallback_chars: set[str] = set()
    line = math_writer._render_mixed_line(
        text,
        writer,
        size,
        (8, 8, 8),
        random.Random(seed),
        fallback_chars,
    )
    if fallback_chars:
        raise RuntimeError(f"Missing glyphs in {writer.writer_id}: {sorted(fallback_chars)}")
    return line


def _fit_line(line: Image.Image, max_width: int, max_height: int | None = None) -> Image.Image:
    width_ratio = max_width / line.width if line.width > max_width else 1.0
    height_ratio = (
        max_height / line.height if max_height is not None and line.height > max_height else 1.0
    )
    ratio = min(width_ratio, height_ratio)
    if ratio >= 1.0:
        return line
    return line.resize(
        (max(1, round(line.width * ratio)), max(1, round(line.height * ratio))),
        Image.Resampling.LANCZOS,
    )


def _write_centered(
    page: Image.Image,
    text: str,
    center_x: int,
    center_y: int,
    max_width: int,
    writer: webfont.WebFontWriter,
    size: int,
    seed: int,
) -> None:
    if not text:
        return
    line = _fit_line(_render_answer(text, writer, size, seed), max_width, 42)
    page.paste(
        line,
        (round(center_x - line.width / 2), round(center_y - line.height / 2)),
        line,
    )


def _write_on_blank(
    page: Image.Image,
    text: str,
    left: int,
    right: int,
    baseline_y: int,
    writer: webfont.WebFontWriter,
    size: int,
    seed: int,
) -> None:
    if not text:
        return
    line = _fit_line(_render_answer(text, writer, size, seed), max(18, right - left - 4), 46)
    paste_x = round((left + right - line.width) / 2)
    paste_y = round(baseline_y - line.height + 1)
    page.paste(line, (paste_x, paste_y), line)


def _line_indent(text: str) -> int:
    if text == "解：" or (text.startswith("(") and len(text) > 2 and text[1].isdigit()):
        return 0
    if _is_formula_line(text):
        return 44
    return 28


def _draw_student_pages(
    base_pages: dict[int, Image.Image],
    responses: dict[str, Any],
    writer: webfont.WebFontWriter,
    student_index: int,
) -> dict[int, Image.Image]:
    pages = {number: image.copy() for number, image in base_pages.items()}
    seed = 800000 + student_index * 1000

    for offset, (question_id, (page_number, center_x, center_y, width, size)) in enumerate(
        OBJECTIVE_SLOTS.items()
    ):
        _write_centered(
            pages[page_number],
            responses[question_id]["answer"],
            center_x,
            center_y,
            width,
            writer,
            size,
            seed + offset,
        )

    offset = 20
    for question_id, positions in SHORT_SLOTS.items():
        for answer, (page_number, left, right, baseline_y, size) in zip(
            responses[question_id]["answers"], positions, strict=True
        ):
            _write_on_blank(
                pages[page_number],
                answer,
                left,
                right,
                baseline_y,
                writer,
                size,
                seed + offset,
            )
            offset += 1

    long_sizes = {"q13": 32, "q14": 32, "q15": 32}
    for question_id in ("q13", "q14", "q15"):
        page_number = base.LONG_PAGES[question_id]
        x, cursor_y = base.LONG_ORIGINS[question_id]
        x -= 12
        previous_was_subquestion = False
        for line_index, line in enumerate(responses[question_id]["lines"]):
            is_subquestion = line.startswith("(") and len(line) > 2 and line[1].isdigit()
            if is_subquestion and line_index > 1 and not previous_was_subquestion:
                cursor_y += 9
            line_height = webfont._write_fixed(
                pages[page_number],
                line,
                x + _line_indent(line),
                cursor_y,
                writer,
                long_sizes[question_id],
                seed + 100 + line_index,
            )
            cursor_y += max(47, line_height + 7)
            previous_was_subquestion = is_subquestion
    return pages


def generate(source: Path, output: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    for profile in webfont.FONT_FAMILIES:
        if not Path(profile["file"]).is_file():
            raise FileNotFoundError(profile["file"])

    output.mkdir(parents=True, exist_ok=True)
    students_root = output / "students"
    labels_root = output / "labels"
    students_root.mkdir(parents=True, exist_ok=True)
    labels_root.mkdir(parents=True, exist_ok=True)

    base_pages = base._render_pdf_pages(source)
    long_variants = base._long_variants()
    questions = math_writer._question_metadata()
    (output / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rows: list[dict[str, Any]] = []
    student_dirs: list[Path] = []
    for index in range(12):
        profile = webfont.FONT_FAMILIES[index % len(webfont.FONT_FAMILIES)]
        writer = webfont.WebFontWriter(profile)
        student_id = f"student_{index + 1:02d}"
        student_dir = students_root / student_id
        student_dir.mkdir(parents=True, exist_ok=True)

        original_responses = base._student_responses(index, long_variants)
        responses = _reference_responses(original_responses)
        pages = _draw_student_pages(base_pages, responses, writer, index)

        page_paths: list[str] = []
        for page_number, image in pages.items():
            page_path = student_dir / f"page-{page_number:02d}.jpg"
            image.save(page_path, "JPEG", quality=94, optimize=True)
            page_paths.append(page_path.relative_to(output).as_posix())

        total_score = sum(int(item["score"]) for item in responses.values())
        label = {
            "student_id": student_id,
            "handwriting_profile": {
                "source": "open_source_human_handwriting_font",
                "family": profile["family"],
                "font_file": Path(profile["file"]).name,
                "project_url": profile["source"],
                "license": profile["license"],
                "perturbation": "none",
                "formula_layout": "structured_handwritten_math_v5_units_and_groups",
                "answer_layout": "reference_step_by_step",
                "answer_positioning": "per_item_parenthesis_center_and_blank_baseline",
                "scoring_policy": "visible_evidence_consistent_v2",
                "visible_error_annotations": False,
                "ink": "black",
                "capture": "clean_scan",
            },
            "pages": page_paths,
            "responses": responses,
            "total_score": total_score,
            "max_score": 100,
            "review_status": "synthetic_answers_reference_layout_unreviewed",
        }
        (labels_root / f"{student_id}.json").write_text(
            json.dumps(label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        rows.append(
            {
                "student_id": student_id,
                "handwriting_family": profile["family"],
                "total_score": total_score,
                "label": f"labels/{student_id}.json",
                "pages": page_paths,
            }
        )
        student_dirs.append(student_dir)

    (output / "students.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    manifest = {
        "dataset_id": "physics_unit_55662305_reference_layout_v9",
        "source_pdf": source.relative_to(ROOT).as_posix(),
        "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "reference_style": {
            "ink": "black",
            "answer_organization": "short explanation followed by a separate formula line",
            "formula_layout": "clean radicals, inline compound units, scoped fractions, superscripts and variable subscripts",
            "answer_positioning": "per-item parenthesis centers and underline baselines",
            "scoring_policy": "identical visible work receives identical rubric credit",
            "visible_error_annotations": False,
            "capture_effect": "none",
        },
        "handwriting_sources": [
            {
                "family": profile["family"],
                "font_file": Path(profile["file"]).name,
                "project_url": profile["source"],
                "license": profile["license"],
            }
            for profile in webfont.FONT_FAMILIES
        ],
        "student_count": len(rows),
        "pages_per_student": 7,
        "image_count": len(rows) * 7,
        "question_count": len(questions),
        "score_range": [
            min(row["total_score"] for row in rows),
            max(row["total_score"] for row in rows),
        ],
        "limitations": [
            "作答内容与分数为合成。",
            "参考图片只用于归纳黑笔、分步书写和公式分行等版式特点，未复制其中的笔迹图块。",
            "错误样本只呈现错误公式或结果，不在答卷旁标注错误原因。",
            "选择题按括号中心定位，填空题按横线中心和基线定位。",
            "复合单位中的斜杠按行内单位符号显示，不作为上下分式。",
            "上下分式仅移除完全冗余的外层括号，保留控制指数范围的必要括号。",
            "本版本不添加旋转、倾斜、随机落笔或拍照扰动。",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base._write_contact_sheet(student_dirs, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="按参考答题版式生成清晰物理学生答卷")
    parser.add_argument("--source", type=Path, default=base.DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
