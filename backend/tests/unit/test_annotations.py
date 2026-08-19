from __future__ import annotations

import hashlib
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from homework_judge.artifacts.annotation_layout import AnnotationMark
from homework_judge.artifacts.annotations import render_annotation_artifact

from .test_grading_pipeline import grading_settings


def test_annotation_renderer_preserves_original_and_page_geometry(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    source = tmp_path / "student-page.jpg"
    Image.new("RGB", (1000, 1400), "white").save(source, "JPEG")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    marks = [
        AnnotationMark.model_validate(
            {
                "mark_type": "check",
                "page_id": "page",
                "question_result_id": "result-1",
                "question_id": "question-1",
                "box": {"x": 700, "y": 150, "width": 70, "height": 70},
                "label": "正确",
                "color": "#10B981",
            }
        ),
        AnnotationMark.model_validate(
            {
                "mark_type": "partial_score",
                "page_id": "page",
                "question_result_id": "result-2",
                "question_id": "question-2",
                "box": {"x": 700, "y": 400, "width": 100, "height": 100},
                "label": "4.00/6.00",
                "color": "#F59E0B",
            }
        ),
    ]
    artifact = render_annotation_artifact(
        settings=settings,
        pages=[
            {
                "id": "page",
                "page_number": 1,
                "original_image_path": "student-page.jpg",
            }
        ],
        marks=marks,
        output_dir=tmp_path / "artifact",
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    with Image.open(artifact.page_paths[0]) as rendered:
        assert rendered.size == (1000, 1400)
        assert rendered.getbbox() is not None
    document = pdfium.PdfDocument(artifact.pdf_path)
    try:
        assert len(document) == 1
    finally:
        document.close()
    assert artifact.marks_path.is_file()
    assert len(artifact.content_hash) == 64
