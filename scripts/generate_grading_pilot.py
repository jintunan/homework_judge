from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "dataset" / (
    "第1章 静电力与电场强度 单元测评 -2025-2026学年高二上学期物理鲁科版必修第三册 "
    "[55662305].pdf"
)
DEFAULT_OUTPUT = ROOT / "data" / "grading_benchmark" / "physics_unit_55662305"

FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\STXINGKA.TTF"),
    Path(r"C:\Windows\Fonts\FZSTK.TTF"),
    Path(r"C:\Windows\Fonts\FZYTK.TTF"),
    Path(r"C:\Windows\Fonts\simkai.ttf"),
)


QUESTIONS: dict[str, dict[str, Any]] = {
    "q13": {
        "number": "13",
        "source_page": 5,
        "max_score": 10,
        "question": (
            "电荷量分别为+q、-2q的异种电荷A、B固定在同一水平线上、相距6x，"
            "悬点O用长2x的绝缘细绳悬挂质量为m的带电小球C，绳与OP夹角为30°，"
            "平衡时C位于两电荷连线上。求静电力大小、A和B产生的合场强以及C的电荷量大小。"
        ),
        "rubric": [
            {"id": "q13_r1", "criterion": "由平衡关系求得F=mg·tan30°=√3mg/3", "score": 3},
            {
                "id": "q13_r2",
                "criterion": "正确叠加两点电荷场强，得到E=3kq/(8x²)，方向水平向右",
                "score": 4,
            },
            {"id": "q13_r3", "criterion": "由q'=F/E求得q'=8√3mgx²/(9kq)", "score": 3},
        ],
        "answer_origin": (178, 1360),
        "font_size": 32,
    },
    "q14": {
        "number": "14",
        "source_page": 6,
        "max_score": 12,
        "question": (
            "质量m=2.0×10^-10 kg、电荷量q=1.0×10^-13 C的液滴以v=2.0 m/s竖直进入"
            "长度l=2.0×10^-2 m、电场强度E=2.0×10^5 N/C的水平匀强电场，"
            "收集管位于极板下方h=0.1 m处。求液滴离开电场时的偏转距离和两收集管间距。"
        ),
        "rubric": [
            {"id": "q14_r1", "criterion": "求得板内运动时间t=l/v=1.0×10^-2 s", "score": 2},
            {"id": "q14_r2", "criterion": "由qE=ma求得水平加速度a=100 m/s²", "score": 2},
            {"id": "q14_r3", "criterion": "求得板内偏转s=at²/2=5.0×10^-3 m", "score": 2},
            {"id": "q14_r4", "criterion": "正确处理出场后的水平位移s'=h·at/v=0.05 m", "score": 3},
            {"id": "q14_r5", "criterion": "利用对称性求得管间距d=2(s+s')=0.11 m", "score": 3},
        ],
        "answer_origin": (180, 1190),
        "font_size": 31,
    },
    "q15": {
        "number": "15",
        "source_page": 7,
        "max_score": 18,
        "question": (
            "A、B、C位于30°光滑绝缘斜面，mA=0.43 kg、mB=0.20 kg、mC=0.50 kg，"
            "C带+7×10^-5 C，B、C初距2 m。对A施加沿斜面向上的力，A、B先共同匀加速，"
            "向上运动1 m后F变为恒力。求B的电荷量和电性、加速度、所需时间及恒力大小。"
        ),
        "rubric": [
            {
                "id": "q15_r1",
                "criterion": "由初始平衡求得qB=2.0×10^-5 C且B带正电",
                "score": 5,
            },
            {"id": "q15_r2", "criterion": "在A、B恰好分离条件下求得a=2.0 m/s²", "score": 5},
            {"id": "q15_r3", "criterion": "由1=at²/2求得t=1.0 s", "score": 3},
            {"id": "q15_r4", "criterion": "对A列牛顿第二定律并求得F=3.01 N", "score": 5},
        ],
        "answer_origin": (180, 980),
        "font_size": 30,
    },
}


SAMPLES: list[dict[str, Any]] = [
    {
        "sample_id": "q13_correct",
        "question_id": "q13",
        "variant": "correct",
        "capture": "scan_clean",
        "lines": [
            "解：(1) 平衡时 tan30°=F/mg",
            "所以 F=mg tan30°=√3mg/3。",
            "(2) E=kq/(2x)²+2kq/(4x)²",
            "=3kq/(8x²)，方向水平向右。",
            "(3) q'=F/E=8√3mgx²/(9kq)。",
        ],
        "rubric_scores": {"q13_r1": 3, "q13_r2": 4, "q13_r3": 3},
        "error_types": [],
    },
    {
        "sample_id": "q13_missing_step",
        "question_id": "q13",
        "variant": "missing_step",
        "capture": "phone_mild",
        "lines": [
            "(1) F=mg tan30°=√3mg/3",
            "(2) E=3kq/(8x²)，方向向右。",
            "(3) 没来得及写。",
        ],
        "rubric_scores": {"q13_r1": 3, "q13_r2": 4, "q13_r3": 0},
        "error_types": ["missing_step", "incomplete_answer"],
    },
    {
        "sample_id": "q13_calculation_error",
        "question_id": "q13",
        "variant": "calculation_error",
        "capture": "phone_medium",
        "lines": [
            "(1) F=mg tan30°=√3mg/3",
            "(2) E=kq/(2x)²+2kq/(4x)²",
            "我算得 E=5kq/(8x²)，方向向右。",
            "(3) q'=F/E=8√3mgx²/(15kq)。",
        ],
        "rubric_scores": {"q13_r1": 3, "q13_r2": 2, "q13_r3": 1},
        "error_types": ["calculation_error", "follow_through_error"],
    },
    {
        "sample_id": "q13_concept_error",
        "question_id": "q13",
        "variant": "concept_error",
        "capture": "phone_mild",
        "lines": [
            "(1) 小球平衡，所以静电力F=mg。",
            "(2) 两个电荷异号，场强互相抵消，E=0。",
            "(3) 因为E=0，所以小球不带电。",
        ],
        "rubric_scores": {"q13_r1": 0, "q13_r2": 0, "q13_r3": 0},
        "error_types": ["force_analysis_error", "field_superposition_error"],
    },
    {
        "sample_id": "q14_correct",
        "question_id": "q14",
        "variant": "correct",
        "capture": "scan_clean",
        "lines": [
            "解：t=l/v=1.0×10^-2 s",
            "qE=ma，a=100 m/s²",
            "(1) s=at²/2=5.0×10^-3 m",
            "出场时 vx=at，s'=h·vx/v=0.05 m",
            "(2) d=2(s+s')=0.11 m。",
        ],
        "rubric_scores": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 2, "q14_r4": 3, "q14_r5": 3},
        "error_types": [],
    },
    {
        "sample_id": "q14_missing_step",
        "question_id": "q14",
        "variant": "missing_step",
        "capture": "phone_mild",
        "lines": [
            "(1) t=l/v=0.01 s，a=qE/m=100 m/s²",
            "s=at²/2=5×10^-3 m",
            "(2) A、B收集管间距是0.11 m。",
        ],
        "rubric_scores": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 2, "q14_r4": 0, "q14_r5": 1},
        "error_types": ["missing_step"],
    },
    {
        "sample_id": "q14_calculation_error",
        "question_id": "q14",
        "variant": "calculation_error",
        "capture": "phone_medium",
        "lines": [
            "t=l/v=0.01 s，qE=ma，a=100 m/s²",
            "(1) s=at²/2=5×10^-2 m（小数点算错）",
            "s'=h·at/v=0.05 m",
            "(2) d=2(s+s')=0.20 m。",
        ],
        "rubric_scores": {"q14_r1": 2, "q14_r2": 2, "q14_r3": 1, "q14_r4": 3, "q14_r5": 1},
        "error_types": ["decimal_error", "follow_through_error"],
    },
    {
        "sample_id": "q14_concept_error",
        "question_id": "q14",
        "variant": "concept_error",
        "capture": "phone_mild",
        "lines": [
            "液滴在电场中做匀速直线运动。",
            "(1) 偏转距离 s=vt=2×0.01=0.02 m",
            "(2) 两管间距 d=2s=0.04 m。",
        ],
        "rubric_scores": {"q14_r1": 1, "q14_r2": 0, "q14_r3": 0, "q14_r4": 0, "q14_r5": 0},
        "error_types": ["motion_model_error"],
    },
    {
        "sample_id": "q15_correct",
        "question_id": "q15",
        "variant": "correct",
        "capture": "scan_clean",
        "lines": [
            "解：(1) (mA+mB)g sin30°=kqBqC/L²",
            "得 qB=2.0×10^-5 C，B带正电。",
            "(2) 分离时BC=3 m，对B：",
            "kqBqC/3²-mB g sin30°=mB a，a=2.0 m/s²",
            "(3) 1=at²/2，t=1.0 s",
            "(4) F-mA g sin30°=mA a，F=3.01 N。",
        ],
        "rubric_scores": {"q15_r1": 5, "q15_r2": 5, "q15_r3": 3, "q15_r4": 5},
        "error_types": [],
    },
    {
        "sample_id": "q15_missing_step",
        "question_id": "q15",
        "variant": "missing_step",
        "capture": "phone_mild",
        "lines": [
            "(1) qB=2×10^-5 C，正电",
            "(2) a=2.0 m/s²",
            "(3) 1=at²/2，所以t=1.0 s",
            "(4) F=3.01 N",
        ],
        "rubric_scores": {"q15_r1": 3, "q15_r2": 2, "q15_r3": 3, "q15_r4": 2},
        "error_types": ["missing_derivation"],
    },
    {
        "sample_id": "q15_calculation_error",
        "question_id": "q15",
        "variant": "calculation_error",
        "capture": "phone_medium",
        "lines": [
            "(1) 由平衡得qB=2×10^-5 C，带正电。",
            "(2) 分离时列式得a=2.0 m/s²",
            "(3) 1=at²/2，错算成t=2.0 s",
            "(4) F-mA g sin30°=mA a，F=3.01 N。",
        ],
        "rubric_scores": {"q15_r1": 5, "q15_r2": 5, "q15_r3": 1, "q15_r4": 5},
        "error_types": ["square_root_error"],
    },
    {
        "sample_id": "q15_concept_error",
        "question_id": "q15",
        "variant": "concept_error",
        "capture": "phone_mild",
        "lines": [
            "(1) B、C应异号相吸，所以B带负电。",
            "qB=2×10^-5 C。",
            "(2) 斜面光滑，所以a=g sin30°=5 m/s²",
            "(3) t=√(2/5) s",
            "(4) F=mA a=2.15 N。",
        ],
        "rubric_scores": {"q15_r1": 2, "q15_r2": 0, "q15_r3": 1, "q15_r4": 0},
        "error_types": ["charge_sign_error", "force_analysis_error"],
    },
]


def _font_paths() -> list[Path]:
    fonts = [path for path in FONT_CANDIDATES if path.is_file()]
    if not fonts:
        raise RuntimeError("未找到可用的中文手写字体")
    return fonts


def _render_pdf_pages(source: Path, page_numbers: set[int]) -> dict[int, Image.Image]:
    document = pdfium.PdfDocument(source)
    try:
        result: dict[int, Image.Image] = {}
        for number in sorted(page_numbers):
            page = document[number - 1]
            try:
                bitmap = page.render(scale=2.5)
                try:
                    result[number] = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
        return result
    finally:
        document.close()


def _draw_handwritten_line(
    image: Image.Image,
    text: str,
    origin: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    rng: random.Random,
) -> None:
    x, y = origin
    for char in text:
        advance = max(7.0, float(font.getlength(char)))
        if char.isspace():
            x += advance * rng.uniform(0.8, 1.2)
            continue
        bbox = font.getbbox(char)
        width = max(10, bbox[2] - bbox[0] + 12)
        height = max(16, bbox[3] - bbox[1] + 18)
        glyph = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        glyph_draw = ImageDraw.Draw(glyph)
        glyph_draw.text((6 - bbox[0], 6 - bbox[1]), char, font=font, fill=(*color, 235))
        angle = rng.uniform(-2.2, 2.2)
        glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        image.paste(glyph, (round(x), round(y + rng.uniform(-2.5, 2.5))), glyph)
        x += advance * rng.uniform(0.93, 1.08)


def _draw_answer(
    page: Image.Image,
    sample: dict[str, Any],
    question: dict[str, Any],
    font_path: Path,
    seed: int,
) -> Image.Image:
    rng = random.Random(seed)
    result = page.copy()
    font = ImageFont.truetype(str(font_path), int(question["font_size"]))
    colors = ((15, 45, 112), (25, 35, 55), (20, 61, 132))
    color = colors[seed % len(colors)]
    x, y = question["answer_origin"]
    line_height = int(question["font_size"] * 1.55)
    for index, line in enumerate(sample["lines"]):
        _draw_handwritten_line(
            result,
            line,
            (x + rng.randint(-5, 8), y + index * line_height + rng.randint(-2, 3)),
            font,
            color,
            rng,
        )
    return result


def _capture_effect(image: Image.Image, mode: str, seed: int) -> Image.Image:
    rng = random.Random(seed + 7919)
    if mode == "scan_clean":
        return image
    angle = rng.uniform(-0.7, 0.7) if mode == "phone_mild" else rng.uniform(-1.4, 1.4)
    result = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(244, 243, 239))
    overlay = Image.new("L", result.size)
    pixels = overlay.load()
    width, height = result.size
    direction = rng.choice((-1, 1))
    strength = 18 if mode == "phone_mild" else 32
    for x in range(width):
        shade = int(255 - strength * ((x / max(1, width - 1)) if direction > 0 else (1 - x / max(1, width - 1))))
        for y in range(height):
            pixels[x, y] = shade
    result = Image.composite(result, Image.new("RGB", result.size, (225, 222, 214)), overlay)
    result = ImageEnhance.Contrast(result).enhance(0.97 if mode == "phone_mild" else 0.92)
    if mode == "phone_medium":
        result = result.filter(ImageFilter.GaussianBlur(radius=0.45))
    return result


def _validate() -> None:
    ids: set[str] = set()
    for question_id, question in QUESTIONS.items():
        rubric_ids = {item["id"] for item in question["rubric"]}
        if sum(int(item["score"]) for item in question["rubric"]) != question["max_score"]:
            raise ValueError(f"{question_id} 的评分点总和与满分不一致")
        if len(rubric_ids) != len(question["rubric"]):
            raise ValueError(f"{question_id} 存在重复评分点")
    for sample in SAMPLES:
        if sample["sample_id"] in ids:
            raise ValueError(f"重复样本ID：{sample['sample_id']}")
        ids.add(sample["sample_id"])
        question = QUESTIONS[sample["question_id"]]
        allowed = {item["id"]: int(item["score"]) for item in question["rubric"]}
        if set(sample["rubric_scores"]) != set(allowed):
            raise ValueError(f"{sample['sample_id']} 的评分点不完整")
        for rubric_id, score in sample["rubric_scores"].items():
            if not 0 <= int(score) <= allowed[rubric_id]:
                raise ValueError(f"{sample['sample_id']} 的评分点得分越界")


def generate(source: Path, output: Path) -> None:
    _validate()
    if not source.is_file():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    images_dir = output / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    fonts = _font_paths()
    pages = _render_pdf_pages(source, {int(item["source_page"]) for item in QUESTIONS.values()})

    labels: list[dict[str, Any]] = []
    rendered: list[Image.Image] = []
    for index, sample in enumerate(SAMPLES):
        question = QUESTIONS[sample["question_id"]]
        seed = 55662305 + index * 101
        font_path = fonts[index % len(fonts)]
        image = _draw_answer(pages[int(question["source_page"])], sample, question, font_path, seed)
        image = _capture_effect(image, sample["capture"], seed)
        filename = f"{sample['sample_id']}.jpg"
        image.save(images_dir / filename, "JPEG", quality=90, optimize=True)
        rendered.append(image.copy())
        rubric_scores = {key: int(value) for key, value in sample["rubric_scores"].items()}
        labels.append(
            {
                "sample_id": sample["sample_id"],
                "question_id": sample["question_id"],
                "image": f"images/{filename}",
                "variant": sample["variant"],
                "capture": sample["capture"],
                "transcription": "\n".join(sample["lines"]),
                "expected_rubric_scores": rubric_scores,
                "expected_total_score": sum(rubric_scores.values()),
                "error_types": sample["error_types"],
                "render_font": font_path.name,
                "review_status": "synthetic_unreviewed",
            }
        )

    with (output / "questions.json").open("w", encoding="utf-8") as stream:
        json.dump(QUESTIONS, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with (output / "samples.jsonl").open("w", encoding="utf-8") as stream:
        for label in labels:
            stream.write(json.dumps(label, ensure_ascii=False) + "\n")

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "dataset_id": "physics_unit_55662305_pilot_v1",
        "source_pdf": str(source.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": source_hash,
        "question_ids": sorted(QUESTIONS),
        "sample_count": len(labels),
        "variants": sorted({item["variant"] for item in labels}),
        "capture_conditions": sorted({item["capture"] for item in labels}),
        "seed": 55662305,
        "label_status": "synthetic_unreviewed",
        "limitations": [
            "作答内容与分数为人工设计的合成标签，不是真实学生作答。",
            "手写效果来自字体和图像扰动，不能替代真实笔迹测试。",
            "人工确认前不得把样本当作正式金标准。",
        ],
    }
    with (output / "manifest.json").open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    thumb_width = 360
    thumbs: list[Image.Image] = []
    for image in rendered:
        thumb = image.copy()
        thumb.thumbnail((thumb_width, 520), Image.Resampling.LANCZOS)
        thumbs.append(thumb)
    columns = 4
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_width, rows * 520), "#d9d7d1")
    for index, thumb in enumerate(thumbs):
        x = (index % columns) * thumb_width + (thumb_width - thumb.width) // 2
        y = (index // columns) * 520 + 6
        sheet.paste(thumb, (x, y))
    sheet.save(output / "contact_sheet.jpg", "JPEG", quality=90, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成物理作业批改合成图片试点集")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
