from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import generate_grading_casia_exam as math_writer
import generate_grading_full_exam as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "grading_benchmark" / "physics_unit_55662305_full_clean_v5"


CLEAN_PROFILES = (
    {"font": Path(r"C:\Windows\Fonts\simkai.ttf"), "width": 0.96, "slant": -0.018, "weight": 1, "scale": 0.98},
    {"font": Path(r"C:\Windows\Fonts\STKAITI.TTF"), "width": 1.00, "slant": 0.012, "weight": 1, "scale": 1.00},
    {"font": Path(r"C:\Windows\Fonts\FZYTK.TTF"), "width": 0.94, "slant": -0.010, "weight": 0, "scale": 0.96},
    {"font": Path(r"C:\Windows\Fonts\STXINWEI.TTF"), "width": 1.02, "slant": 0.018, "weight": 0, "scale": 1.00},
    {"font": Path(r"C:\Windows\Fonts\simkai.ttf"), "width": 1.03, "slant": 0.024, "weight": 1, "scale": 1.01},
    {"font": Path(r"C:\Windows\Fonts\STKAITI.TTF"), "width": 0.93, "slant": -0.024, "weight": 0, "scale": 0.97},
    {"font": Path(r"C:\Windows\Fonts\FZYTK.TTF"), "width": 1.00, "slant": 0.010, "weight": 1, "scale": 0.99},
    {"font": Path(r"C:\Windows\Fonts\STXINWEI.TTF"), "width": 0.96, "slant": -0.016, "weight": 1, "scale": 0.98},
    {"font": Path(r"C:\Windows\Fonts\simkai.ttf"), "width": 0.91, "slant": -0.030, "weight": 0, "scale": 0.95},
    {"font": Path(r"C:\Windows\Fonts\STKAITI.TTF"), "width": 1.04, "slant": 0.030, "weight": 1, "scale": 1.02},
    {"font": Path(r"C:\Windows\Fonts\FZYTK.TTF"), "width": 0.97, "slant": -0.020, "weight": 0, "scale": 0.97},
    {"font": Path(r"C:\Windows\Fonts\STXINWEI.TTF"), "width": 1.05, "slant": 0.026, "weight": 1, "scale": 1.01},
)


class CleanWriter:
    """A controlled, legible handwriting synthesizer with one stable style per student."""

    def __init__(self, profile: dict[str, Any], writer_id: str) -> None:
        self.profile = profile
        self.writer_id = writer_id
        self.font_path = Path(profile["font"])
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
        font_size = max(15, round(height * scale * 1.08))
        font = ImageFont.truetype(str(self.font_path), font_size)
        bbox = font.getbbox(char or " ")
        padding = max(5, round(font_size * 0.18))
        width = max(8, bbox[2] - bbox[0] + padding * 2)
        canvas_height = max(12, bbox[3] - bbox[1] + padding * 2)
        alpha = Image.new("L", (width, canvas_height), 0)
        draw = ImageDraw.Draw(alpha)
        origin = (padding - bbox[0], padding - bbox[1])
        draw.text(origin, char, font=font, fill=246)
        if int(self.profile["weight"]) > 0:
            draw.text((origin[0] + 1, origin[1]), char, font=font, fill=205)
        if rng.random() < 0.22:
            alpha = alpha.filter(ImageFilter.GaussianBlur(0.18))
        rgba = Image.new("RGBA", alpha.size, (*ink, 0))
        rgba.putalpha(alpha)
        target_width = max(5, round(rgba.width * float(self.profile["width"]) * rng.uniform(0.985, 1.015)))
        target_height = max(5, round(rgba.height * rng.uniform(0.985, 1.015)))
        rgba = rgba.resize((target_width, target_height), Image.Resampling.BICUBIC)
        slant = float(self.profile["slant"]) + rng.uniform(-0.006, 0.006)
        shear_px = abs(slant) * rgba.height + 2
        rgba = rgba.transform(
            (rgba.width + math.ceil(shear_px), rgba.height),
            Image.Transform.AFFINE,
            (1, slant, 0 if slant >= 0 else shear_px, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
        rgba = rgba.rotate(rng.uniform(-0.38, 0.38), resample=Image.Resampling.BICUBIC, expand=True)
        visible = rgba.getbbox()
        if visible:
            rgba = rgba.crop(visible)
        return ImageOps.expand(rgba, border=(1, 1, 2, 1), fill=(255, 255, 255, 0))


def generate(source: Path, output: Path) -> None:
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
    for index, profile in enumerate(CLEAN_PROFILES):
        student_id = f"student_{index + 1:02d}"
        writer = CleanWriter(profile, f"clean_{index + 1:02d}")
        style = dict(base.STUDENT_STYLES[index])
        style["scale"] = float(profile["scale"])
        style["ink"] = (8 + index % 3 * 2, 8 + index % 4 * 2, 8 + index % 5 * 2)
        student_dir = students_root / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        responses = base._student_responses(index, long_variants)
        pages, fallbacks = math_writer._draw_student_pages(base_pages, responses, writer, style, index)
        if fallbacks:
            raise RuntimeError(f"Unexpected fallback characters for {student_id}: {sorted(fallbacks)}")
        page_paths: list[str] = []
        for page_number, image in pages.items():
            page_path = student_dir / f"page-{page_number:02d}.jpg"
            image.save(page_path, "JPEG", quality=92, optimize=True)
            page_paths.append(page_path.relative_to(output).as_posix())
        total_score = sum(int(item["score"]) for item in responses.values())
        label = {
            "student_id": student_id,
            "handwriting_profile": {
                "source": "controlled_synthetic_handwriting",
                "writer_id": writer.writer_id,
                "font_family_file": writer.font_path.name,
                "formula_layout": "structured_handwritten_math_v2",
                "ink": "black_gel_pen_render",
                "capture": style["capture"],
                "fallback_characters": [],
            },
            "pages": page_paths,
            "responses": responses,
            "total_score": total_score,
            "max_score": 100,
            "review_status": "synthetic_clean_handwriting_unreviewed",
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
                "label": f"labels/{student_id}.json",
                "pages": page_paths,
            }
        )
        student_dirs.append(student_dir)
    (output / "students.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    manifest = {
        "dataset_id": "physics_unit_55662305_full_clean_v5",
        "source_pdf": source.relative_to(ROOT).as_posix(),
        "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "handwriting_source": {
            "type": "controlled_synthetic_handwriting",
            "profiles": [
                {
                    "writer_id": f"clean_{index + 1:02d}",
                    "font_family_file": Path(profile["font"]).name,
                    "width": profile["width"],
                    "slant": profile["slant"],
                    "weight": profile["weight"],
                }
                for index, profile in enumerate(CLEAN_PROFILES)
            ],
            "method": "stable student profile with small per-glyph handwriting variation",
        },
        "student_count": len(rows),
        "pages_per_student": 7,
        "image_count": len(rows) * 7,
        "question_count": len(questions),
        "score_range": [min(row["total_score"] for row in rows), max(row["total_score"] for row in rows)],
        "limitations": [
            "字迹为可控合成，不应标注为真实学生笔迹。",
            "每名学生的基础字形保持一致，只加入轻微宽度、倾斜、旋转和落笔变化。",
            "公式采用二维手写布局，保证分式、指数、下标和根式结构清楚。",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base._write_contact_sheet(student_dirs, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成12份字迹清楚、公式规范的黑笔物理答卷")
    parser.add_argument("--source", type=Path, default=base.DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
