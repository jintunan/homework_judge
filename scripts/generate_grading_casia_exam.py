from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import generate_grading_full_exam as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POT_DIR = ROOT / "data" / "external" / "CASIA-OLHWDB" / "Pot1.1Test"
DEFAULT_OUTPUT = ROOT / "data" / "grading_benchmark" / "physics_unit_55662305_full_casia_math_v4"
SOURCE_URL = "https://nlpr.ia.ac.cn/databases/Download/Online/CharData/Pot1.1Test.zip"
PREFERRED_WRITER_IDS = (
    "1243",
    "1246",
    "1247",
    "1248",
    "1249",
    "1250",
    "1251",
    "1252",
    "1254",
    "1255",
    "1257",
    "1258",
)


def _decode_tag(raw: bytes) -> str | None:
    """Decode the two meaningful bytes in a CASIA POT tag."""
    if len(raw) != 4:
        return None
    pair = raw[:2]
    if pair[0] == 0 and pair[1] != 0:
        return chr(pair[1])
    try:
        return bytes((pair[1], pair[0])).decode("gbk")
    except UnicodeDecodeError:
        return None


class PotWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.writer_id = path.stem
        self.glyphs = self._load(path)

    @staticmethod
    def _load(path: Path) -> dict[str, list[list[tuple[int, int]]]]:
        data = path.read_bytes()
        glyphs: dict[str, list[list[tuple[int, int]]]] = {}
        cursor = 0
        while cursor + 8 <= len(data):
            sample_size = struct.unpack_from("<H", data, cursor)[0]
            if sample_size < 12 or cursor + sample_size > len(data):
                raise ValueError(f"Invalid POT record at {cursor} in {path}")
            char = _decode_tag(data[cursor + 2 : cursor + 6])
            strokes: list[list[tuple[int, int]]] = []
            stroke: list[tuple[int, int]] = []
            point_cursor = cursor + 8
            record_end = cursor + sample_size
            while point_cursor + 4 <= record_end:
                x, y = struct.unpack_from("<hh", data, point_cursor)
                point_cursor += 4
                if (x, y) == (-1, -1):
                    if stroke:
                        strokes.append(stroke)
                    break
                if (x, y) == (-1, 0):
                    if stroke:
                        strokes.append(stroke)
                        stroke = []
                    continue
                stroke.append((x, y))
            if char and strokes and char not in glyphs:
                glyphs[char] = strokes
            cursor += sample_size
        if cursor != len(data):
            raise ValueError(f"Trailing bytes in {path}: {len(data) - cursor}")
        return glyphs

    def has(self, char: str) -> bool:
        return char in self.glyphs

    def render(
        self,
        char: str,
        height: int,
        ink: tuple[int, int, int],
        rng: random.Random,
        scale: float = 1.0,
    ) -> Image.Image | None:
        strokes = self.glyphs.get(char)
        if not strokes:
            return None
        points = [point for stroke in strokes for point in stroke]
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        source_height = max(1, max_y - min_y)
        source_width = max(1, max_x - min_x)
        target_height = max(12, round(height * scale * rng.uniform(0.965, 1.035)))
        factor = target_height / source_height
        padding = max(5, round(target_height * 0.16))
        target_width = max(7, round(source_width * factor))
        layer = Image.new("RGBA", (target_width + padding * 2, target_height + padding * 2), (255, 255, 255, 0))
        draw = ImageDraw.Draw(layer)
        line_width = max(2, round(target_height / 12.5))
        alpha = rng.randint(246, 255)
        for source_stroke in strokes:
            transformed = [
                (
                    padding + (x - min_x) * factor,
                    padding + (y - min_y) * factor,
                )
                for x, y in source_stroke
            ]
            if len(transformed) == 1:
                x, y = transformed[0]
                radius = line_width / 2
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*ink, alpha))
            else:
                # A faint sub-pixel edge makes black gel ink sit naturally on the scan.
                edge = [(x + 0.45, y + 0.2) for x, y in transformed]
                draw.line(edge, fill=(*ink, 48), width=line_width + 1, joint="curve")
                draw.line(transformed, fill=(*ink, alpha), width=line_width, joint="curve")
        angle = rng.uniform(-0.7, 0.7)
        return layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)


SPECIAL_ALIASES = {
    "−": "-",
    "—": "-",
    "×": "x",
    "·": ".",
    "²": "2",
    "³": "3",
    "′": "'",
    "'": ",",
    "°": "o",
    "“": '"',
    "”": '"',
    "（": "(",
    "）": ")",
    "，": ",",
    "。": ".",
    "：": ":",
}


def _fallback_char(char: str, height: int, ink: tuple[int, int, int]) -> Image.Image:
    font = ImageFont.truetype(str(base.FONTS[0]), max(16, round(height * 0.9)))
    bbox = font.getbbox(char)
    width = max(8, bbox[2] - bbox[0] + 10)
    canvas_height = max(12, bbox[3] - bbox[1] + 10)
    layer = Image.new("RGBA", (width, canvas_height), (255, 255, 255, 0))
    ImageDraw.Draw(layer).text((5 - bbox[0], 5 - bbox[1]), char, font=font, fill=(*ink, 232))
    return layer


def _hstack(images: list[Image.Image], gap: int = 0, center: bool = True) -> Image.Image:
    images = [image for image in images if image.width > 0 and image.height > 0]
    if not images:
        return Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    width = sum(image.width for image in images) + gap * max(0, len(images) - 1)
    height = max(image.height for image in images)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    cursor = 0
    for image in images:
        paste_y = (height - image.height) // 2 if center else height - image.height
        canvas.alpha_composite(image, (cursor, paste_y))
        cursor += image.width + gap
    return canvas


def _ink_line(
    image: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    ink: tuple[int, int, int],
    width: int,
    rng: random.Random,
) -> None:
    draw = ImageDraw.Draw(image)
    x1, y1 = start
    x2, y2 = end
    middle_x = (x1 + x2) // 2
    middle_y = (y1 + y2) // 2 + rng.choice((-1, 0, 0, 1))
    draw.line([(x1, y1), (middle_x, middle_y), (x2, y2)], fill=(*ink, 250), width=width, joint="curve")


def _matching_paren(text: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _render_sequence(
    text: str,
    writer: PotWriter,
    size: int,
    ink: tuple[int, int, int],
    rng: random.Random,
    fallback_chars: set[str],
    math_semantics: bool,
) -> Image.Image:
    height = max(30, round(size * 1.75))
    baseline = round(height * 0.68)
    items: list[tuple[Image.Image, int]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == " ":
            items.append((Image.new("RGBA", (max(4, round(size * 0.38)), 1), (255, 255, 255, 0)), baseline))
            index += 1
            continue
        if math_semantics and char == "√" and index + 1 < len(text):
            if text[index + 1] == "(":
                close = _matching_paren(text, index + 1)
                if close is not None:
                    radicand_text = text[index + 2 : close]
                    index = close + 1
                else:
                    radicand_text = text[index + 1]
                    index += 2
            else:
                radicand_text = text[index + 1]
                index += 2
            radicand = _render_formula(radicand_text, writer, max(17, round(size * 0.82)), ink, rng, fallback_chars)
            stroke_width = max(2, round(size / 13))
            hook_width = max(14, round(size * 0.52))
            top_padding = max(5, stroke_width + 2)
            bottom_padding = max(3, stroke_width)
            radical_height = radicand.height + top_padding + bottom_padding
            radical = Image.new(
                "RGBA",
                (hook_width + radicand.width + stroke_width * 2, radical_height),
                (255, 255, 255, 0),
            )
            rad_x = hook_width + stroke_width
            rad_y = top_padding
            radical.alpha_composite(radicand, (rad_x, rad_y))
            draw = ImageDraw.Draw(radical)
            top_y = max(2, top_padding - stroke_width)
            bottom_y = radical_height - bottom_padding
            hook_y = round(radical_height * 0.62)
            points = [
                (1, hook_y),
                (max(3, round(hook_width * 0.28)), hook_y - max(2, round(size * 0.08))),
                (max(6, round(hook_width * 0.48)), bottom_y),
                (hook_width, top_y),
                (radical.width - 2, top_y),
            ]
            draw.line(points, fill=(*ink, 255), width=stroke_width, joint="curve")
            items.append((radical, baseline - round(radical.height * 0.72)))
            continue
        superscript_text = ""
        if math_semantics and char == "^":
            index += 1
            while index < len(text) and (text[index].isdigit() or text[index] in "+-"):
                superscript_text += text[index]
                index += 1
            if not superscript_text:
                superscript_text = "^"
        elif math_semantics and char in {"²", "³", "°", "'", "′"}:
            superscript_text = SPECIAL_ALIASES.get(char, char)
            index += 1
        if superscript_text:
            superscript = _render_sequence(
                superscript_text,
                writer,
                max(14, round(size * 0.58)),
                ink,
                rng,
                fallback_chars,
                False,
            )
            items.append((superscript, max(0, round(size * 0.02))))
            continue
        render_char = SPECIAL_ALIASES.get(char, char) if not writer.has(char) else char
        is_subscript = False
        if math_semantics and index > 0:
            pair = text[index - 1 : index + 1]
            is_subscript = pair in {"qB", "qC", "mA", "mB", "mC", "vx", "vy"}
        char_size = max(14, round(size * 0.63)) if is_subscript else size
        glyph = writer.render(render_char, char_size, ink, rng)
        if glyph is None:
            fallback_chars.add(char)
            glyph = _fallback_char(char, char_size, ink)
        if is_subscript:
            paste_y = baseline - round(glyph.height * 0.28)
        else:
            paste_y = baseline - round(glyph.height * 0.72)
        items.append((glyph, paste_y))
        index += 1
    width = sum(image.width for image, _ in items)
    canvas = Image.new("RGBA", (max(1, width), height), (255, 255, 255, 0))
    cursor = 0
    for image, paste_y in items:
        canvas.alpha_composite(image, (cursor, max(0, paste_y)))
        cursor += image.width
    bbox = canvas.getbbox()
    return canvas.crop(bbox) if bbox else Image.new("RGBA", (1, 1), (255, 255, 255, 0))


def _split_top_level_operators(text: str) -> list[str]:
    tokens: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and char in "=+-" and index > start:
            if index > 0 and text[index - 1] == "^":
                continue
            tokens.append(text[start:index])
            tokens.append(char)
            start = index + 1
    tokens.append(text[start:])
    return [token for token in tokens if token]


def _top_level_slash(text: str) -> int | None:
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "/" and depth == 0:
            return index
    return None


def _is_inline_unit_fraction(text: str, slash: int) -> bool:
    """Keep common compound units such as m/s² on the writing baseline."""
    numerator = text[:slash].rstrip()
    denominator = text[slash + 1 :].strip()
    if not numerator or not denominator:
        return False
    unit_numerators = ("m", "N", "J", "C", "V", "W", "Pa", "kg")
    if not any(numerator.endswith(unit) for unit in unit_numerators):
        return False
    unit_denominators = {
        "s",
        "s²",
        "s³",
        "m",
        "m²",
        "m³",
        "C",
        "kg",
    }
    return denominator in unit_denominators


def _strip_redundant_fraction_group(text: str) -> str:
    """Remove only a full outer pair; keep groups whose exponent needs it."""
    value = text.strip()
    if len(value) < 2 or value[0] != "(" or value[-1] != ")":
        return value
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return value
    return value[1:-1].strip() if depth == 0 else value


def _render_formula_term(
    text: str,
    writer: PotWriter,
    size: int,
    ink: tuple[int, int, int],
    rng: random.Random,
    fallback_chars: set[str],
) -> Image.Image:
    slash = _top_level_slash(text)
    if slash is None or slash == 0 or slash == len(text) - 1:
        return _render_sequence(text, writer, size, ink, rng, fallback_chars, True)
    if _is_inline_unit_fraction(text, slash):
        return _render_sequence(text, writer, size, ink, rng, fallback_chars, True)
    numerator_text = text[:slash].strip()
    denominator_text = _strip_redundant_fraction_group(text[slash + 1 :])
    numerator = _render_formula(
        numerator_text, writer, max(16, round(size * 0.80)), ink, rng, fallback_chars
    )
    denominator = _render_formula(
        denominator_text, writer, max(16, round(size * 0.80)), ink, rng, fallback_chars
    )
    padding = max(4, round(size * 0.18))
    bar_gap = max(3, round(size * 0.10))
    width = max(numerator.width, denominator.width) + padding * 2
    height = numerator.height + denominator.height + bar_gap * 2 + 3
    fraction = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    fraction.alpha_composite(numerator, ((width - numerator.width) // 2, 0))
    bar_y = numerator.height + bar_gap
    _ink_line(fraction, (2, bar_y), (width - 2, bar_y + rng.choice((-1, 0, 0, 1))), ink, max(2, round(size / 14)), rng)
    denominator_y = bar_y + bar_gap + 2
    fraction.alpha_composite(denominator, ((width - denominator.width) // 2, denominator_y))
    return fraction


def _render_formula(
    text: str,
    writer: PotWriter,
    size: int,
    ink: tuple[int, int, int],
    rng: random.Random,
    fallback_chars: set[str],
) -> Image.Image:
    tokens = _split_top_level_operators(text)
    rendered: list[Image.Image] = []
    for token in tokens:
        if token in {"=", "+", "-"}:
            rendered.append(_render_sequence(token, writer, size, ink, rng, fallback_chars, True))
        else:
            rendered.append(_render_formula_term(token, writer, size, ink, rng, fallback_chars))
    return _hstack(rendered, gap=max(1, round(size * 0.05)), center=True)


def _is_math_char(char: str) -> bool:
    return char.isspace() or char.isascii() or char in "×√²³°·′−—θ"


def _render_mixed_line(
    text: str,
    writer: PotWriter,
    size: int,
    ink: tuple[int, int, int],
    rng: random.Random,
    fallback_chars: set[str],
) -> Image.Image:
    if not text:
        return Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    runs: list[tuple[bool, str]] = []
    start = 0
    current_is_math = _is_math_char(text[0])
    for index in range(1, len(text)):
        value = _is_math_char(text[index])
        if value != current_is_math:
            runs.append((current_is_math, text[start:index]))
            start = index
            current_is_math = value
    runs.append((current_is_math, text[start:]))
    images: list[Image.Image] = []
    for is_math, run in runs:
        if is_math:
            images.append(_render_formula(run, writer, size, ink, rng, fallback_chars))
        else:
            images.append(_render_sequence(run, writer, size, ink, rng, fallback_chars, False))
    return _hstack(images, gap=max(1, round(size * 0.02)), center=True)


def _write_formula_line(
    page: Image.Image,
    text: str,
    x: int,
    y: int,
    writer: PotWriter,
    style: dict[str, Any],
    rng: random.Random,
    base_size: int,
    fallback_chars: set[str],
    strike: bool = False,
    correction: str | None = None,
) -> int:
    if not text:
        return 0
    size = max(21, round(base_size * float(style["scale"])))
    ink = tuple(style["ink"])
    line = _render_mixed_line(text, writer, size, ink, rng, fallback_chars)
    max_width = max(100, page.width - x - 65)
    if line.width > max_width:
        ratio = max_width / line.width
        line = line.resize((max_width, max(1, round(line.height * ratio))), Image.Resampling.LANCZOS)
    paste_x = round(x + rng.uniform(-5, 7))
    paste_y = round(y + rng.uniform(-3, 4))
    page.paste(line, (paste_x, paste_y), line)
    if strike:
        strike_y = paste_y + line.height // 2
        _ink_line(page, (paste_x + 5, strike_y), (paste_x + line.width - 5, strike_y + rng.randint(-2, 2)), ink, 2, rng)
    if correction:
        correction_line = _render_mixed_line(
            correction, writer, max(19, size - 5), ink, rng, fallback_chars
        )
        correction_x = paste_x + max(20, line.width // 3)
        correction_y = max(0, paste_y - correction_line.height + 5)
        page.paste(correction_line, (correction_x, correction_y), correction_line)
    return line.height


def _write_real(
    page: Image.Image,
    text: str,
    x: int,
    y: int,
    writer: PotWriter,
    style: dict[str, Any],
    rng: random.Random,
    base_size: int,
    fallback_chars: set[str],
    strike: bool = False,
    correction: str | None = None,
) -> None:
    if not text:
        return
    size = max(21, round(base_size * float(style["scale"])))
    ink = tuple(style["ink"])
    cursor_x = round(x + rng.uniform(-5, 7))
    baseline_y = round(y + rng.uniform(-3, 4))
    start_x = cursor_x
    top = baseline_y
    bottom = baseline_y
    for char in text:
        if char == " ":
            cursor_x += round(size * rng.uniform(0.34, 0.48))
            continue
        render_char = char
        superscript = char in {"²", "³", "°", "'", "′"}
        if not writer.has(render_char):
            render_char = SPECIAL_ALIASES.get(char, char)
        char_height = round(size * (0.66 if superscript else 1.0))
        glyph = writer.render(render_char, char_height, ink, rng)
        if glyph is None:
            fallback_chars.add(char)
            glyph = _fallback_char(char, char_height, ink)
        jitter_y = rng.randint(-2, 2) - (round(size * 0.30) if superscript else 0)
        paste_y = baseline_y + jitter_y
        page.paste(glyph, (cursor_x, paste_y), glyph)
        cursor_x += max(4, glyph.width - round(size * 0.18) + rng.randint(-1, 2))
        top = min(top, paste_y)
        bottom = max(bottom, paste_y + glyph.height)
    if strike:
        draw = ImageDraw.Draw(page)
        strike_y = round((top + bottom) / 2)
        draw.line((start_x + 5, strike_y, cursor_x - 5, strike_y + rng.randint(-2, 2)), fill=ink, width=2)
    if correction:
        _write_real(
            page,
            correction,
            start_x + max(20, (cursor_x - start_x) // 3),
            max(0, top - size),
            writer,
            style,
            rng,
            max(19, size - 5),
            fallback_chars,
        )


def _draw_student_pages(
    base_pages: dict[int, Image.Image],
    responses: dict[str, Any],
    writer: PotWriter,
    style: dict[str, Any],
    student_index: int,
) -> tuple[dict[int, Image.Image], set[str]]:
    pages = {number: image.copy() for number, image in base_pages.items()}
    rng = random.Random(55662305 + student_index * 1009)
    fallback_chars: set[str] = set()
    for question_id, (page_number, x, y) in base.OBJECTIVE_POSITIONS.items():
        _write_real(
            pages[page_number], responses[question_id]["answer"], x, y, writer, style, rng, 34, fallback_chars
        )
    for question_id, positions in base.SHORT_POSITIONS.items():
        for answer, (page_number, x, y) in zip(responses[question_id]["answers"], positions, strict=True):
            _write_formula_line(pages[page_number], answer, x, y, writer, style, rng, 29, fallback_chars)
    for question_id in ("q13", "q14", "q15"):
        page_number = base.LONG_PAGES[question_id]
        x, y = base.LONG_ORIGINS[question_id]
        lines = responses[question_id]["lines"]
        cursor_y = y
        for line_index, line in enumerate(lines):
            strike = student_index in {0, 3, 7} and line_index == 1 and question_id == "q14"
            correction = "改：0.01 s" if strike and "t=" in line else None
            rendered_height = _write_formula_line(
                pages[page_number],
                line,
                x + rng.randint(-8, 15),
                cursor_y + rng.randint(-2, 3),
                writer,
                style,
                rng,
                30,
                fallback_chars,
                strike=strike,
                correction=correction,
            )
            cursor_y += max(round(46 * float(style["scale"])), rendered_height + 7)
    captured = {
        number: base._apply_capture(page, str(style["capture"]), 55662305 + student_index * 1009 + number)
        for number, page in pages.items()
    }
    return captured, fallback_chars


def _question_metadata() -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for question_id in base.OBJECTIVE_CORRECT:
        questions[question_id] = {
            "number": question_id.removeprefix("q"),
            "type": "single_choice" if question_id in {"q1", "q2", "q3", "q4"} else "multiple_choice",
            "max_score": base.OBJECTIVE_MAX[question_id],
            "correct_answer": base.OBJECTIVE_CORRECT[question_id],
        }
    for question_id, value in base.SHORT_BASE.items():
        questions[question_id] = {
            "number": question_id.removeprefix("q"),
            "type": "fill_blank",
            "max_score": sum(int(score) for score in value["scores"]),
            "correct_answers": value["answers"],
            "field_scores": value["scores"],
        }
    for question_id, value in base.LONG_QUESTIONS.items():
        questions[question_id] = {
            "number": value["number"],
            "type": "calculation",
            "max_score": value["max_score"],
            "question": value["question"],
            "rubric": value["rubric"],
        }
    return questions


def generate(source: Path, pot_dir: Path, output: Path) -> None:
    pot_paths = [pot_dir / f"{writer_id}.pot" for writer_id in PREFERRED_WRITER_IDS]
    if len(pot_paths) < 12:
        raise RuntimeError(f"Need 12 POT writer files, found {len(pot_paths)} in {pot_dir}")
    output.mkdir(parents=True, exist_ok=True)
    students_root = output / "students"
    labels_root = output / "labels"
    students_root.mkdir(parents=True, exist_ok=True)
    labels_root.mkdir(parents=True, exist_ok=True)
    base_pages = base._render_pdf_pages(source)
    long_variants = base._long_variants()
    questions = _question_metadata()
    (output / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows: list[dict[str, Any]] = []
    student_dirs: list[Path] = []
    all_fallbacks: dict[str, list[str]] = {}
    for index, pot_path in enumerate(pot_paths):
        writer = PotWriter(pot_path)
        style = dict(base.STUDENT_STYLES[index])
        style["ink"] = (10 + index % 4 * 3, 10 + index % 3 * 2, 10 + index % 5 * 2)
        student_id = f"student_{index + 1:02d}"
        student_dir = students_root / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        responses = base._student_responses(index, long_variants)
        pages, fallbacks = _draw_student_pages(base_pages, responses, writer, style, index)
        page_paths: list[str] = []
        for page_number, image in pages.items():
            page_path = student_dir / f"page-{page_number:02d}.jpg"
            image.save(page_path, "JPEG", quality=92, optimize=True)
            page_paths.append(page_path.relative_to(output).as_posix())
        total_score = sum(int(item["score"]) for item in responses.values())
        fallback_list = sorted(fallbacks)
        all_fallbacks[student_id] = fallback_list
        label = {
            "student_id": student_id,
            "handwriting_profile": {
                "source": "CASIA-OLHWDB1.1-Test",
                "writer_id": writer.writer_id,
                "trajectory_type": "real_online_pen_strokes",
                "ink": "black_gel_pen_render",
                "capture": style["capture"],
                "fallback_characters": fallback_list,
            },
            "pages": page_paths,
            "responses": responses,
            "total_score": total_score,
            "max_score": 100,
            "review_status": "synthetic_answers_real_handwriting_trajectories_unreviewed",
        }
        (labels_root / f"{student_id}.json").write_text(
            json.dumps(label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        rows.append(
            {
                "student_id": student_id,
                "writer_id": writer.writer_id,
                "ability_band": style["band"],
                "total_score": total_score,
                "capture": style["capture"],
                "fallback_characters": fallback_list,
                "label": f"labels/{student_id}.json",
                "pages": page_paths,
            }
        )
        student_dirs.append(student_dir)
    (output / "students.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    source_zip = pot_dir.parent / "Pot1.1Test.zip"
    manifest = {
        "dataset_id": "physics_unit_55662305_full_casia_math_v4",
        "source_pdf": source.relative_to(ROOT).as_posix(),
        "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "handwriting_source": {
            "dataset": "CASIA-OLHWDB1.1-Test",
            "official_url": SOURCE_URL,
            "archive_sha256": hashlib.sha256(source_zip.read_bytes()).hexdigest() if source_zip.is_file() else None,
            "writer_ids": [path.stem for path in pot_paths],
            "method": "real writer-specific pen trajectories rendered as black ink",
        },
        "student_count": len(rows),
        "pages_per_student": 7,
        "image_count": len(rows) * 7,
        "question_count": len(questions),
        "score_range": [min(row["total_score"] for row in rows), max(row["total_score"] for row in rows)],
        "fallback_characters_by_student": all_fallbacks,
        "limitations": [
            "作答内容和分数为合成，不对应CASIA数据中的真实答题内容。",
            "汉字与常见符号优先使用同一书写者的真实逐笔轨迹；清单中的缺失符号使用字体后备。",
            "公式采用二维手写布局：分数、指数、下标和根号按数学结构组合。",
            "真实逐笔轨迹来自孤立字符采集，因此字符间连写不等同于自然连续书写。",
            "CASIA公开数据仅按其学术研究条件使用；商业使用需向数据提供方申请。",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base._write_contact_sheet(student_dirs, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="用CASIA真实书写者轨迹生成12份完整黑笔物理答卷")
    parser.add_argument("--source", type=Path, default=base.DEFAULT_SOURCE)
    parser.add_argument("--pot-dir", type=Path, default=DEFAULT_POT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.pot_dir.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
