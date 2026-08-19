from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

import generate_grading_casia_exam as math_writer
import generate_grading_full_exam as base


ROOT = Path(__file__).resolve().parents[1]
FONT_ROOT = ROOT / "data" / "external" / "open_handwriting_fonts"
DEFAULT_OUTPUT = ROOT / "data" / "grading_benchmark" / "physics_unit_55662305_clear_webfonts_v6"


FONT_FAMILIES = (
    {
        "family": "Yozai",
        "file": FONT_ROOT / "Yozai-Regular.ttf",
        "source": "https://github.com/lxgw/yozai-font",
        "license": "SIL-OFL-1.1",
    },
    {
        "family": "NaniFont",
        "file": FONT_ROOT / "NaniFont-Regular.ttf",
        "source": "https://github.com/max32002/nanifont",
        "license": "SIL-OFL-1.1",
    },
    {
        "family": "NaikaiFont",
        "file": FONT_ROOT / "NaikaiFont-Regular.ttf",
        "source": "https://github.com/max32002/naikaifont",
        "license": "SIL-OFL-1.1",
    },
)


class WebFontWriter:
    """Render an open-source handwriting font exactly, without style perturbation."""

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.writer_id = str(profile["family"])
        self.font_path = Path(profile["file"])
        if not self.font_path.is_file():
            raise FileNotFoundError(self.font_path)

    def has(self, char: str) -> bool:
        return True

    def render(
        self,
        char: str,
        height: int,
        ink: tuple[int, int, int],
        rng: random.Random,
        scale: float = 1.0,
    ) -> Image.Image | None:
        del rng
        font = ImageFont.truetype(str(self.font_path), max(15, round(height * scale * 1.08)))
        bbox = font.getbbox(char or " ")
        padding = 4
        width = max(8, bbox[2] - bbox[0] + padding * 2)
        canvas_height = max(12, bbox[3] - bbox[1] + padding * 2)
        alpha = Image.new("L", (width, canvas_height), 0)
        ImageDraw.Draw(alpha).text((padding - bbox[0], padding - bbox[1]), char, font=font, fill=255)
        rgba = Image.new("RGBA", alpha.size, (*ink, 0))
        rgba.putalpha(alpha)
        visible = rgba.getbbox()
        if visible:
            rgba = rgba.crop(visible)
        return ImageOps.expand(rgba, border=(1, 1, 2, 1), fill=(255, 255, 255, 0))


def _write_fixed(
    page: Image.Image,
    text: str,
    x: int,
    y: int,
    writer: WebFontWriter,
    size: int,
    seed: int,
) -> int:
    if not text:
        return 0
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
    max_width = max(100, page.width - x - 65)
    if line.width > max_width:
        ratio = max_width / line.width
        line = line.resize((max_width, max(1, round(line.height * ratio))), Image.Resampling.LANCZOS)
    page.paste(line, (x, y), line)
    return line.height


def _visual_sublines(text: str) -> list[str]:
    normalized = text.replace("；", "，").replace(",", "，")
    parts = [part.strip() for part in normalized.split("，") if part.strip()]
    return parts or [text]


def _draw_student_pages(
    base_pages: dict[int, Image.Image],
    responses: dict[str, Any],
    writer: WebFontWriter,
    student_index: int,
) -> dict[int, Image.Image]:
    pages = {number: image.copy() for number, image in base_pages.items()}
    seed = 700000 + student_index * 1000
    for offset, (question_id, (page_number, x, y)) in enumerate(base.OBJECTIVE_POSITIONS.items()):
        _write_fixed(pages[page_number], responses[question_id]["answer"], x, y, writer, 34, seed + offset)
    offset = 20
    for question_id, positions in base.SHORT_POSITIONS.items():
        for answer, (page_number, x, y) in zip(responses[question_id]["answers"], positions, strict=True):
            _write_fixed(pages[page_number], answer, x, y, writer, 29, seed + offset)
            offset += 1
    for question_id in ("q13", "q14", "q15"):
        page_number = base.LONG_PAGES[question_id]
        x, cursor_y = base.LONG_ORIGINS[question_id]
        for line_index, line in enumerate(responses[question_id]["lines"]):
            for subline_index, subline in enumerate(_visual_sublines(line)):
                subline_x = x + (30 if subline_index > 0 else 0)
                line_height = _write_fixed(
                    pages[page_number],
                    subline,
                    subline_x,
                    cursor_y,
                    writer,
                    30,
                    seed + 100 + line_index * 10 + subline_index,
                )
                cursor_y += max(46, line_height + 7)
    return pages


def generate(source: Path, output: Path) -> None:
    for profile in FONT_FAMILIES:
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
        profile = FONT_FAMILIES[index % len(FONT_FAMILIES)]
        writer = WebFontWriter(profile)
        student_id = f"student_{index + 1:02d}"
        student_dir = students_root / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        responses = base._student_responses(index, long_variants)
        pages = _draw_student_pages(base_pages, responses, writer, index)
        page_paths: list[str] = []
        for page_number, image in pages.items():
            page_path = student_dir / f"page-{page_number:02d}.jpg"
            image.save(page_path, "JPEG", quality=93, optimize=True)
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
                "formula_layout": "structured_handwritten_math_v2",
                "ink": "black",
                "capture": "clean_scan",
            },
            "pages": page_paths,
            "responses": responses,
            "total_score": total_score,
            "max_score": 100,
            "review_status": "synthetic_answers_open_handwriting_font_unreviewed",
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
        "dataset_id": "physics_unit_55662305_clear_webfonts_v6",
        "source_pdf": source.relative_to(ROOT).as_posix(),
        "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "handwriting_sources": [
            {
                "family": profile["family"],
                "font_file": Path(profile["file"]).name,
                "project_url": profile["source"],
                "license": profile["license"],
            }
            for profile in FONT_FAMILIES
        ],
        "perturbation": "none",
        "capture_effect": "none",
        "visual_line_layout": "split_on_commas_and_semicolons",
        "student_count": len(rows),
        "pages_per_student": 7,
        "image_count": len(rows) * 7,
        "question_count": len(questions),
        "score_range": [min(row["total_score"] for row in rows), max(row["total_score"] for row in rows)],
        "limitations": [
            "作答内容与分数为合成。",
            "字形来自开放许可的真人手写风格字库，不等同于现场学生连续书写。",
            "本版本不添加旋转、缩放、倾斜、随机落笔或拍照扰动，优先用于清晰批改基线。",
            "计算题按逗号和分号拆分为多行显示，标签中的原始答案文本不变。",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base._write_contact_sheet(student_dirs, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="用开源清晰手写字库生成无扰动的物理答卷")
    parser.add_argument("--source", type=Path, default=base.DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
