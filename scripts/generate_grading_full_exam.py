from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from generate_grading_pilot import QUESTIONS as LONG_QUESTIONS
from generate_grading_pilot import SAMPLES as PILOT_LONG_SAMPLES


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "dataset" / (
    "第1章 静电力与电场强度 单元测评 -2025-2026学年高二上学期物理鲁科版必修第三册 "
    "[55662305].pdf"
)
DEFAULT_OUTPUT = ROOT / "data" / "grading_benchmark" / "physics_unit_55662305_full_v2"


FONTS = (
    Path(r"C:\Windows\Fonts\STXINGKA.TTF"),
    Path(r"C:\Windows\Fonts\FZSTK.TTF"),
    Path(r"C:\Windows\Fonts\FZYTK.TTF"),
    Path(r"C:\Windows\Fonts\STKAITI.TTF"),
)


OBJECTIVE_MAX = {"q1": 4, "q2": 4, "q3": 4, "q4": 4, "q5": 6, "q6": 6, "q7": 6, "q8": 6}
OBJECTIVE_CORRECT = {
    "q1": "D",
    "q2": "C",
    "q3": "A",
    "q4": "C",
    "q5": "AD",
    "q6": "AC",
    "q7": "BD",
    "q8": "BD",
}
OBJECTIVE_POSITIONS = {
    "q1": (1, 848, 493),
    "q2": (1, 680, 880),
    "q3": (1, 1000, 1380),
    "q4": (2, 610, 245),
    "q5": (2, 735, 1020),
    "q6": (2, 1220, 1355),
    "q7": (3, 280, 290),
    "q8": (3, 205, 1085),
}


SHORT_BASE: dict[str, dict[str, Any]] = {
    "q9": {"answers": ["失去", "异种", "吸引"], "scores": [1, 1, 2]},
    "q10": {"answers": ["1×10^-6", "负"], "scores": [2, 2]},
    "q11": {"answers": ["电荷转移", "遵守", "CD"], "scores": [1, 1, 3]},
    "q12": {"answers": ["D", "增大", "2√3/9"], "scores": [2, 2, 3]},
}
SHORT_POSITIONS = {
    "q9": [(3, 650, 1675), (3, 200, 1825), (3, 705, 1825)],
    "q10": [(4, 785, 245), (4, 1000, 245)],
    "q11": [(4, 470, 500), (4, 210, 580), (4, 975, 720)],
    "q12": [(4, 600, 1780), (5, 1060, 255), (5, 890, 675)],
}


OBJECTIVE_ANSWERS = [
    {},
    {"q6": "A"},
    {"q2": "A", "q5": "A"},
    {"q3": "B", "q7": "B"},
    {"q4": "B", "q6": "A", "q7": "B"},
    {"q1": "B", "q3": "C", "q5": "D", "q8": "B"},
    {"q2": "D", "q4": "A", "q6": "C", "q8": "D"},
    {"q1": "A", "q3": "D", "q5": "A", "q6": "B", "q7": "B", "q8": "B"},
    {"q2": "A", "q4": "D", "q5": "D", "q6": "A", "q7": "A", "q8": "D"},
    {"q1": "B", "q2": "D", "q3": "C", "q4": "A", "q5": "A", "q6": "C", "q7": "B", "q8": "A"},
    {"q1": "A", "q2": "B", "q3": "D", "q4": "A", "q5": "C", "q6": "D", "q7": "A", "q8": "C"},
    {"q1": "", "q2": "A", "q3": "", "q4": "D", "q5": "", "q6": "A", "q7": "", "q8": "C"},
]


SHORT_OVERRIDES: list[dict[str, dict[str, list[Any]]]] = [
    {},
    {},
    {"q10": {"answers": ["1×10^-6", "正"], "scores": [2, 0]}},
    {
        "q9": {"answers": ["失去", "同种", "吸引"], "scores": [1, 0, 2]},
        "q12": {"answers": ["D", "增大", ""], "scores": [2, 2, 0]},
    },
    {
        "q11": {"answers": ["电荷转移", "遵守", "D"], "scores": [1, 1, 1]},
        "q12": {"answers": ["D", "增大", "2/3"], "scores": [2, 2, 0]},
    },
    {
        "q9": {"answers": ["失去", "异种", "排斥"], "scores": [1, 1, 0]},
        "q10": {"answers": ["2×10^-6", "负"], "scores": [0, 2]},
        "q11": {"answers": ["电荷转移", "遵守", "D"], "scores": [1, 1, 1]},
        "q12": {"answers": ["D", "增大", ""], "scores": [2, 2, 0]},
    },
    {
        "q9": {"answers": ["得到", "异种", "吸引"], "scores": [0, 1, 2]},
        "q11": {"answers": ["创造新电荷", "遵守", "C"], "scores": [0, 1, 1]},
        "q12": {"answers": ["C", "增大", "2√3/9"], "scores": [0, 2, 3]},
    },
    {
        "q9": {"answers": ["得到", "同种", "排斥"], "scores": [0, 0, 0]},
        "q10": {"answers": ["1×10^-6", "正"], "scores": [2, 0]},
        "q11": {"answers": ["电荷转移", "不遵守", "D"], "scores": [1, 0, 1]},
        "q12": {"answers": ["C", "减小", ""], "scores": [0, 0, 0]},
    },
    {
        "q9": {"answers": ["失去", "同种", "排斥"], "scores": [1, 0, 0]},
        "q10": {"answers": ["5×10^-6", "负"], "scores": [0, 2]},
        "q11": {"answers": ["创造新电荷", "不遵守", "A"], "scores": [0, 0, 0]},
        "q12": {"answers": ["A", "减小", "2/3"], "scores": [0, 0, 0]},
    },
    {
        "q9": {"answers": ["", "异种", ""], "scores": [0, 1, 0]},
        "q10": {"answers": ["1×10^-5", "正"], "scores": [0, 0]},
        "q11": {"answers": ["电荷转移", "", "D"], "scores": [1, 0, 1]},
        "q12": {"answers": ["B", "", ""], "scores": [0, 0, 0]},
    },
    {
        "q9": {"answers": ["得到", "", "排斥"], "scores": [0, 0, 0]},
        "q10": {"answers": ["", "正"], "scores": [0, 0]},
        "q11": {"answers": ["创造新电荷", "不遵守", "A"], "scores": [0, 0, 0]},
        "q12": {"answers": ["A", "减小", "1"], "scores": [0, 0, 0]},
    },
    {
        "q9": {"answers": ["", "", ""], "scores": [0, 0, 0]},
        "q10": {"answers": ["", ""], "scores": [0, 0]},
        "q11": {"answers": ["", "", ""], "scores": [0, 0, 0]},
        "q12": {"answers": ["", "", ""], "scores": [0, 0, 0]},
    },
]


LONG_EXTRA: dict[str, dict[str, dict[str, Any]]] = {
    "q13": {
        "final_only": {
            "lines": [
                "(1) F=√3mg/3",
                "(2) E=3kq/(8x²)，方向向右",
                "(3) q'=8√3mgx²/(9kq)",
            ],
            "rubric_scores": {"q13_r1": 2, "q13_r2": 2, "q13_r3": 2},
            "error_types": ["missing_derivation"],
        },
        "blank": {"lines": [], "rubric_scores": {"q13_r1": 0, "q13_r2": 0, "q13_r3": 0}, "error_types": ["blank"]},
    },
    "q14": {
        "final_only": {
            "lines": ["(1) 5×10^-3 m", "(2) 0.11 m"],
            "rubric_scores": {"q14_r1": 0, "q14_r2": 0, "q14_r3": 2, "q14_r4": 0, "q14_r5": 2},
            "error_types": ["missing_derivation"],
        },
        "blank": {
            "lines": [],
            "rubric_scores": {"q14_r1": 0, "q14_r2": 0, "q14_r3": 0, "q14_r4": 0, "q14_r5": 0},
            "error_types": ["blank"],
        },
    },
    "q15": {
        "partial": {
            "lines": [
                "(1) qB=2×10^-5 C，带正电",
                "(2) 分离时BC=3 m，算得a=2.0 m/s²",
                "(3)(4) 不会",
            ],
            "rubric_scores": {"q15_r1": 5, "q15_r2": 5, "q15_r3": 0, "q15_r4": 0},
            "error_types": ["incomplete_answer"],
        },
        "blank": {"lines": [], "rubric_scores": {"q15_r1": 0, "q15_r2": 0, "q15_r3": 0, "q15_r4": 0}, "error_types": ["blank"]},
    },
}


LONG_ASSIGNMENTS = [
    {"q13": "correct", "q14": "correct", "q15": "correct"},
    {"q13": "correct", "q14": "correct", "q15": "calculation_error"},
    {"q13": "correct", "q14": "calculation_error", "q15": "correct"},
    {"q13": "missing_step", "q14": "correct", "q15": "calculation_error"},
    {"q13": "calculation_error", "q14": "calculation_error", "q15": "missing_step"},
    {"q13": "final_only", "q14": "missing_step", "q15": "calculation_error"},
    {"q13": "missing_step", "q14": "concept_error", "q15": "missing_step"},
    {"q13": "calculation_error", "q14": "final_only", "q15": "concept_error"},
    {"q13": "concept_error", "q14": "calculation_error", "q15": "partial"},
    {"q13": "final_only", "q14": "concept_error", "q15": "concept_error"},
    {"q13": "concept_error", "q14": "final_only", "q15": "blank"},
    {"q13": "blank", "q14": "concept_error", "q15": "blank"},
]


STUDENT_STYLES = [
    {"band": "excellent", "font": 0, "scale": 0.95, "ink": (18, 45, 105), "slant": -0.018, "capture": "scan"},
    {"band": "strong", "font": 1, "scale": 1.00, "ink": (24, 37, 64), "slant": 0.012, "capture": "phone_mild"},
    {"band": "strong", "font": 2, "scale": 0.91, "ink": (20, 58, 128), "slant": -0.010, "capture": "scan"},
    {"band": "upper_middle", "font": 0, "scale": 1.04, "ink": (30, 43, 76), "slant": 0.022, "capture": "phone_mild"},
    {"band": "upper_middle", "font": 3, "scale": 0.96, "ink": (21, 60, 132), "slant": -0.025, "capture": "phone_medium"},
    {"band": "middle", "font": 1, "scale": 1.08, "ink": (20, 35, 56), "slant": 0.030, "capture": "phone_mild"},
    {"band": "middle", "font": 2, "scale": 1.02, "ink": (26, 63, 126), "slant": -0.032, "capture": "phone_medium"},
    {"band": "lower_middle", "font": 3, "scale": 1.12, "ink": (45, 48, 54), "slant": 0.040, "capture": "phone_mild"},
    {"band": "lower_middle", "font": 0, "scale": 1.00, "ink": (18, 55, 122), "slant": -0.040, "capture": "phone_medium"},
    {"band": "weak", "font": 1, "scale": 1.15, "ink": (35, 40, 52), "slant": 0.050, "capture": "phone_medium"},
    {"band": "weak", "font": 2, "scale": 0.98, "ink": (31, 65, 121), "slant": -0.050, "capture": "phone_mild"},
    {"band": "very_weak", "font": 3, "scale": 1.18, "ink": (55, 55, 58), "slant": 0.055, "capture": "phone_medium"},
]


LONG_ORIGINS = {"q13": (178, 1360), "q14": (180, 1190), "q15": (180, 980)}
LONG_PAGES = {"q13": 5, "q14": 6, "q15": 7}


def _objective_score(question_id: str, answer: str) -> int:
    correct = OBJECTIVE_CORRECT[question_id]
    if question_id in {"q1", "q2", "q3", "q4"}:
        return OBJECTIVE_MAX[question_id] if answer == correct else 0
    if answer == correct:
        return 6
    if answer and set(answer) < set(correct):
        return 3
    return 0


def _long_variants() -> dict[str, dict[str, dict[str, Any]]]:
    variants: dict[str, dict[str, dict[str, Any]]] = {"q13": {}, "q14": {}, "q15": {}}
    for sample in PILOT_LONG_SAMPLES:
        variants[sample["question_id"]][sample["variant"]] = {
            "lines": list(sample["lines"]),
            "rubric_scores": dict(sample["rubric_scores"]),
            "error_types": list(sample["error_types"]),
        }
    for question_id, values in LONG_EXTRA.items():
        variants[question_id].update(values)
    return variants


def _render_pdf_pages(source: Path) -> dict[int, Image.Image]:
    document = pdfium.PdfDocument(source)
    try:
        pages: dict[int, Image.Image] = {}
        for number in range(1, 8):
            page = document[number - 1]
            try:
                bitmap = page.render(scale=2.5)
                try:
                    pages[number] = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages
    finally:
        document.close()


def _line_image(
    text: str,
    font_path: Path,
    size: int,
    ink: tuple[int, int, int],
    slant: float,
    rng: random.Random,
) -> Image.Image:
    font = ImageFont.truetype(str(font_path), size)
    bbox = font.getbbox(text or " ")
    width = max(20, bbox[2] - bbox[0] + 34)
    height = max(24, bbox[3] - bbox[1] + 30)
    layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    origin = (15 - bbox[0], 13 - bbox[1])
    draw.text(origin, text, font=font, fill=(*ink, 225))
    if rng.random() < 0.55:
        draw.text((origin[0] + rng.choice((-1, 1)), origin[1]), text, font=font, fill=(*ink, 22))
    shear_px = abs(slant) * height + 3
    transformed = layer.transform(
        (width + math.ceil(shear_px), height),
        Image.Transform.AFFINE,
        (1, slant, 0 if slant >= 0 else shear_px, 0, 1, 0),
        resample=Image.Resampling.BICUBIC,
    )
    stretch = rng.uniform(0.965, 1.035)
    transformed = transformed.resize(
        (max(1, round(transformed.width * stretch)), transformed.height),
        Image.Resampling.BICUBIC,
    )
    return transformed.rotate(
        rng.uniform(-0.45, 0.45),
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )


def _write(
    page: Image.Image,
    text: str,
    x: int,
    y: int,
    style: dict[str, Any],
    rng: random.Random,
    base_size: int,
    strike: bool = False,
    correction: str | None = None,
) -> None:
    if not text:
        return
    font_path = FONTS[int(style["font"])]
    size = max(21, round(base_size * float(style["scale"]) * rng.uniform(0.96, 1.04)))
    px = round(x + rng.uniform(-5, 7))
    py = round(y + rng.uniform(-3, 4))
    start_x = px
    chunks: list[str] = []
    if len(text) <= 8:
        chunks = [text]
    else:
        cursor = 0
        while cursor < len(text):
            width = rng.randint(3, 7)
            chunks.append(text[cursor : cursor + width])
            cursor += width
    max_height = 0
    for chunk in chunks:
        chunk_size = max(20, round(size * rng.uniform(0.965, 1.035)))
        line = _line_image(
            chunk,
            font_path,
            chunk_size,
            style["ink"],
            float(style["slant"]) + rng.uniform(-0.008, 0.008),
            rng,
        )
        chunk_y = py + rng.randint(-2, 2)
        page.paste(line, (px, chunk_y), line)
        px += max(5, line.width - 29 + rng.randint(-1, 2))
        max_height = max(max_height, line.height)
    if strike:
        draw = ImageDraw.Draw(page)
        strike_y = py + max_height // 2
        draw.line((start_x + 8, strike_y, px - 8, strike_y + rng.randint(-2, 2)), fill=style["ink"], width=2)
    if correction:
        correction_line = _line_image(correction, font_path, max(19, size - 5), style["ink"], float(style["slant"]), rng)
        page.paste(correction_line, (start_x + max(20, (px - start_x) // 3), max(0, py - correction_line.height + 8)), correction_line)


def _apply_capture(page: Image.Image, mode: str, seed: int) -> Image.Image:
    if mode == "scan":
        return page
    rng = random.Random(seed)
    width, height = page.size
    border = 55 if mode == "phone_mild" else 80
    canvas = Image.new("RGB", (width + border * 2, height + border * 2), (205, 201, 191))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rectangle((border + 12, border + 14, border + width + 12, border + height + 14), fill=(0, 0, 0, 65))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16 if mode == "phone_mild" else 24))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")
    canvas.paste(page, (border, border))
    angle = rng.uniform(-0.75, 0.75) if mode == "phone_mild" else rng.uniform(-1.6, 1.6)
    canvas = canvas.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(205, 201, 191))
    left = (canvas.width - width) // 2 + rng.randint(-8, 8)
    top = (canvas.height - height) // 2 + rng.randint(-10, 10)
    result = canvas.crop((left, top, left + width, top + height))
    gradient = Image.linear_gradient("L").resize(result.size)
    if rng.random() < 0.5:
        gradient = ImageOps.mirror(gradient)
    strength = 0.10 if mode == "phone_mild" else 0.18
    shade = Image.new("RGB", result.size, (190, 185, 175))
    mask = gradient.point(lambda value: int(value * strength))
    result = Image.composite(shade, result, mask)
    result = ImageEnhance.Contrast(result).enhance(0.98 if mode == "phone_mild" else 0.93)
    if mode == "phone_medium":
        result = result.filter(ImageFilter.GaussianBlur(0.35))
    return result


def _student_responses(index: int, long_variants: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    objective_override = OBJECTIVE_ANSWERS[index]
    for question_id, correct in OBJECTIVE_CORRECT.items():
        answer = objective_override.get(question_id, correct)
        responses[question_id] = {
            "answer": answer,
            "score": _objective_score(question_id, answer),
            "max_score": OBJECTIVE_MAX[question_id],
        }
    for question_id, base in SHORT_BASE.items():
        override = SHORT_OVERRIDES[index].get(question_id, base)
        answers = list(override["answers"])
        scores = [int(value) for value in override["scores"]]
        responses[question_id] = {
            "answers": answers,
            "field_scores": scores,
            "score": sum(scores),
            "max_score": sum(int(value) for value in base["scores"]),
        }
    for question_id, variant_name in LONG_ASSIGNMENTS[index].items():
        variant = long_variants[question_id][variant_name]
        rubric_scores = {key: int(value) for key, value in variant["rubric_scores"].items()}
        responses[question_id] = {
            "variant": variant_name,
            "lines": list(variant["lines"]),
            "transcription": "\n".join(variant["lines"]),
            "rubric_scores": rubric_scores,
            "score": sum(rubric_scores.values()),
            "max_score": int(LONG_QUESTIONS[question_id]["max_score"]),
            "error_types": list(variant["error_types"]),
        }
    return responses


def _draw_student_pages(
    base_pages: dict[int, Image.Image],
    responses: dict[str, Any],
    style: dict[str, Any],
    student_index: int,
) -> dict[int, Image.Image]:
    pages = {number: image.copy() for number, image in base_pages.items()}
    rng = random.Random(55662305 + student_index * 1009)
    for question_id, (page_number, x, y) in OBJECTIVE_POSITIONS.items():
        _write(pages[page_number], responses[question_id]["answer"], x, y, style, rng, 34)
    for question_id, positions in SHORT_POSITIONS.items():
        for answer, (page_number, x, y) in zip(responses[question_id]["answers"], positions, strict=True):
            _write(pages[page_number], answer, x, y, style, rng, 29)
    for question_id in ("q13", "q14", "q15"):
        page_number = LONG_PAGES[question_id]
        x, y = LONG_ORIGINS[question_id]
        lines = responses[question_id]["lines"]
        line_height = round(46 * float(style["scale"]))
        for line_index, line in enumerate(lines):
            strike = student_index in {0, 3, 7} and line_index == 1 and question_id == "q14"
            correction = "改：0.01 s" if strike and "t=" in line else None
            _write(
                pages[page_number],
                line,
                x + rng.randint(-8, 15),
                y + line_index * line_height + rng.randint(-2, 3),
                style,
                rng,
                30,
                strike=strike,
                correction=correction,
            )
    return {
        number: _apply_capture(page, str(style["capture"]), 55662305 + student_index * 1009 + number)
        for number, page in pages.items()
    }


def _write_contact_sheet(student_dirs: list[Path], output: Path) -> None:
    tile_width = 450
    tile_height = 620
    columns = 3
    rows = 4
    sheet = Image.new("RGB", (tile_width * columns, tile_height * rows), (215, 212, 205))
    for index, student_dir in enumerate(student_dirs):
        page = Image.open(student_dir / "page-07.jpg").convert("RGB")
        page.thumbnail((tile_width - 20, tile_height - 42), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_width, tile_height), "white")
        tile.paste(page, ((tile_width - page.width) // 2, 28))
        draw = ImageDraw.Draw(tile)
        draw.text((12, 7), student_dir.name, fill=(30, 30, 30))
        sheet.paste(tile, ((index % columns) * tile_width, (index // columns) * tile_height))
    sheet.save(output / "contact_sheet_page7.jpg", "JPEG", quality=91, optimize=True)


def generate(source: Path, output: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    if any(not font.is_file() for font in FONTS):
        missing = [str(font) for font in FONTS if not font.is_file()]
        raise RuntimeError(f"缺少字体：{missing}")
    output.mkdir(parents=True, exist_ok=True)
    students_root = output / "students"
    labels_root = output / "labels"
    students_root.mkdir(parents=True, exist_ok=True)
    labels_root.mkdir(parents=True, exist_ok=True)
    base_pages = _render_pdf_pages(source)
    long_variants = _long_variants()
    questions_meta: dict[str, Any] = {}
    for question_id in OBJECTIVE_CORRECT:
        questions_meta[question_id] = {
            "number": question_id.removeprefix("q"),
            "type": "single_choice" if question_id in {"q1", "q2", "q3", "q4"} else "multiple_choice",
            "max_score": OBJECTIVE_MAX[question_id],
            "correct_answer": OBJECTIVE_CORRECT[question_id],
        }
    for question_id, value in SHORT_BASE.items():
        questions_meta[question_id] = {
            "number": question_id.removeprefix("q"),
            "type": "fill_blank",
            "max_score": sum(int(score) for score in value["scores"]),
            "correct_answers": value["answers"],
            "field_scores": value["scores"],
        }
    for question_id, value in LONG_QUESTIONS.items():
        questions_meta[question_id] = {
            "number": value["number"],
            "type": "calculation",
            "max_score": value["max_score"],
            "question": value["question"],
            "rubric": value["rubric"],
        }
    with (output / "questions.json").open("w", encoding="utf-8") as stream:
        json.dump(questions_meta, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    summary: list[dict[str, Any]] = []
    student_dirs: list[Path] = []
    for index, style in enumerate(STUDENT_STYLES):
        student_id = f"student_{index + 1:02d}"
        student_dir = students_root / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        responses = _student_responses(index, long_variants)
        pages = _draw_student_pages(base_pages, responses, style, index)
        page_paths: list[str] = []
        for page_number, image in pages.items():
            path = student_dir / f"page-{page_number:02d}.jpg"
            image.save(path, "JPEG", quality=90, optimize=True)
            page_paths.append(path.relative_to(output).as_posix())
        total_score = sum(int(item["score"]) for item in responses.values())
        label = {
            "student_id": student_id,
            "synthetic_profile": {
                "ability_band": style["band"],
                "font": FONTS[int(style["font"])].name,
                "ink_rgb": list(style["ink"]),
                "capture": style["capture"],
            },
            "pages": page_paths,
            "responses": responses,
            "total_score": total_score,
            "max_score": 100,
            "review_status": "synthetic_unreviewed",
        }
        with (labels_root / f"{student_id}.json").open("w", encoding="utf-8") as stream:
            json.dump(label, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        summary.append(
            {
                "student_id": student_id,
                "ability_band": style["band"],
                "total_score": total_score,
                "capture": style["capture"],
                "label": f"labels/{student_id}.json",
                "pages": page_paths,
            }
        )
        student_dirs.append(student_dir)
    with (output / "students.jsonl").open("w", encoding="utf-8") as stream:
        for row in summary:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "dataset_id": "physics_unit_55662305_full_v2",
        "source_pdf": source.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "student_count": len(summary),
        "pages_per_student": 7,
        "image_count": len(summary) * 7,
        "question_count": len(questions_meta),
        "score_range": [min(row["total_score"] for row in summary), max(row["total_score"] for row in summary)],
        "label_status": "synthetic_unreviewed",
        "limitations": [
            "学生身份、作答和分数均为合成，不对应真实个人。",
            "笔迹由本地字体、行级形变、笔色和拍照扰动生成，仍不能替代真实学生笔迹。",
            "人工复核前不得将分数标签当作正式金标准。",
        ],
    }
    with (output / "manifest.json").open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    _write_contact_sheet(student_dirs, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成12份完整的物理学生作答试卷")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
