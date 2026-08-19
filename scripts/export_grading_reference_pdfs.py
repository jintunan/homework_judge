from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "data"
    / "grading_benchmark"
    / "physics_unit_55662305_reference_layout_v9"
    / "students"
)
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "physics_unit_55662305_reference_layout_v9"


def _write_student_pdf(student_dir: Path, output_pdf: Path) -> None:
    pages = sorted(student_dir.glob("page-*.jpg"))
    if len(pages) != 7:
        raise ValueError(f"{student_dir.name} should contain 7 JPG pages, found {len(pages)}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output_pdf), pagesize=A4, pageCompression=1)
    pdf.setTitle(f"{student_dir.name} - Physics Unit Test")
    pdf.setAuthor("Synthetic Homework Grading Benchmark")
    page_width, page_height = A4
    for page in pages:
        pdf.drawImage(
            str(page),
            0,
            0,
            width=page_width,
            height=page_height,
            preserveAspectRatio=False,
            mask="auto",
        )
        pdf.showPage()
    pdf.save()


def _write_combined_pdf(student_pdfs: list[Path], output_pdf: Path) -> None:
    writer = PdfWriter()
    for student_pdf in student_pdfs:
        reader = PdfReader(student_pdf)
        if len(reader.pages) != 7:
            raise ValueError(f"{student_pdf.name} should contain 7 PDF pages")
        for page in reader.pages:
            writer.add_page(page)
    writer.add_metadata(
        {
            "/Title": "Physics Unit Test - 12 Synthetic Student Papers",
            "/Author": "Synthetic Homework Grading Benchmark",
            "/Subject": "physics_unit_55662305_reference_layout_v9",
        }
    )
    with output_pdf.open("wb") as stream:
        writer.write(stream)


def export(input_root: Path, output_root: Path) -> list[Path]:
    student_dirs = sorted(path for path in input_root.glob("student_*" ) if path.is_dir())
    if len(student_dirs) != 12:
        raise ValueError(f"Expected 12 student directories, found {len(student_dirs)}")

    output_root.mkdir(parents=True, exist_ok=True)
    student_pdfs: list[Path] = []
    for student_dir in student_dirs:
        output_pdf = output_root / f"{student_dir.name}.pdf"
        _write_student_pdf(student_dir, output_pdf)
        student_pdfs.append(output_pdf)

    combined_pdf = output_root / "all_12_students.pdf"
    _write_combined_pdf(student_pdfs, combined_pdf)
    return [*student_pdfs, combined_pdf]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the v9 student papers as A4 PDFs")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in export(args.input.resolve(), args.output.resolve()):
        print(path)


if __name__ == "__main__":
    main()
