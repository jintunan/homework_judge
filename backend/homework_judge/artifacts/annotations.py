from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..db.database import json_dumps
from ..errors import AppError
from ..files.storage import resolve_data_path
from .annotation_layout import AnnotationMark, AnnotationMarkType
from .fonts import pil_font


@dataclass(frozen=True, slots=True)
class AnnotationArtifact:
    pdf_path: Path
    marks_path: Path
    page_paths: tuple[Path, ...]
    content_hash: str
    preview: dict[str, object]


def _read_image(path: Path) -> NDArray[np.uint8]:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AppError(422, "STUDENT_PAGE_UNREADABLE", "学生试卷页面无法读取")
    return cast(NDArray[np.uint8], image)


def _write_image(path: Path, image: NDArray[np.uint8]) -> None:
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not success:
        raise AppError(500, "ANNOTATION_IMAGE_FAILED", "批注页面保存失败")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(path)


def _point(value: float) -> int:
    return round(value)


def _draw_shape(image: NDArray[np.uint8], mark: AnnotationMark) -> None:
    x = _point(mark.box.x)
    y = _point(mark.box.y)
    width = _point(mark.box.width)
    height = _point(mark.box.height)
    thickness = max(4, _point(min(image.shape[:2]) * 0.004))
    if mark.mark_type is AnnotationMarkType.CHECK:
        points = np.array(
            [
                [x + width * 0.08, y + height * 0.52],
                [x + width * 0.38, y + height * 0.82],
                [x + width * 0.94, y + height * 0.12],
            ],
            dtype=np.int32,
        )
        cv2.polylines(image, [points], False, (129, 185, 16), thickness, cv2.LINE_AA)
    elif mark.mark_type is AnnotationMarkType.ERROR_CIRCLE:
        center = (x + width // 2, y + height // 2)
        axes = (max(3, width // 2), max(3, height // 2))
        cv2.ellipse(image, center, axes, -8, 0, 360, (38, 38, 220), thickness, cv2.LINE_AA)
    elif mark.mark_type is AnnotationMarkType.PARTIAL_SCORE:
        triangle = np.array(
            [[x + width // 2, y], [x + width, y + height], [x, y + height]],
            dtype=np.int32,
        )
        overlay = image.copy()
        cv2.fillPoly(overlay, [triangle], (11, 158, 245), cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.25, image, 0.75, 0, image)
        cv2.polylines(image, [triangle], True, (11, 158, 245), thickness, cv2.LINE_AA)


def _draw_labels(image: NDArray[np.uint8], marks: list[AnnotationMark]) -> NDArray[np.uint8]:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    for mark in marks:
        if mark.mark_type is not AnnotationMarkType.PARTIAL_SCORE:
            continue
        size = max(14, _point(mark.box.height * 0.23))
        font = pil_font(size)
        label = mark.label
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        x = mark.box.x + (mark.box.width - text_width) / 2
        y = mark.box.y + mark.box.height * 0.55
        draw.text((x, y), label, fill=(139, 72, 0), font=font, anchor="lm")
    return cast(NDArray[np.uint8], cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR))


def _write_pdf(paths: list[Path], output: Path) -> None:
    if not paths:
        raise AppError(409, "ANNOTATION_PAGES_EMPTY", "没有可生成批注的学生页面")
    pdf = canvas.Canvas(str(output), pageCompression=1)
    for path in paths:
        with Image.open(path) as image:
            width, height = image.size
        pdf.setPageSize((width, height))
        pdf.drawImage(ImageReader(str(path)), 0, 0, width=width, height=height)
        pdf.showPage()
    pdf.save()


def render_annotation_artifact(
    *,
    settings: object,
    pages: list[dict[str, object]],
    marks: list[AnnotationMark],
    output_dir: Path,
) -> AnnotationArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_page: dict[str, list[AnnotationMark]] = {}
    for mark in marks:
        by_page.setdefault(mark.page_id, []).append(mark)
    page_paths: list[Path] = []
    page_preview: list[dict[str, object]] = []
    digest = hashlib.sha256()
    for page in pages:
        page_id = str(page["id"])
        original = resolve_data_path(settings, str(page["original_image_path"]))  # type: ignore[arg-type]
        image = _read_image(original)
        page_marks = by_page.get(page_id, [])
        for mark in page_marks:
            _draw_shape(image, mark)
        image = _draw_labels(image, page_marks)
        output = output_dir / f"page-{int(str(page['page_number'])):04d}.jpg"
        _write_image(output, image)
        digest.update(output.read_bytes())
        page_paths.append(output)
        page_preview.append(
            {
                "pageId": page_id,
                "pageNumber": page["page_number"],
                "image": output.name,
                "markCount": len(page_marks),
            }
        )
    marks_path = output_dir / "marks.json"
    marks_payload = [mark.model_dump(mode="json") for mark in marks]
    marks_path.write_text(json_dumps(marks_payload), encoding="utf-8")
    digest.update(marks_path.read_bytes())
    pdf_path = output_dir / "annotated-paper.pdf"
    _write_pdf(page_paths, pdf_path)
    digest.update(pdf_path.read_bytes())
    return AnnotationArtifact(
        pdf_path=pdf_path,
        marks_path=marks_path,
        page_paths=tuple(page_paths),
        content_hash=digest.hexdigest(),
        preview={"pages": page_preview, "marks": marks_payload},
    )
