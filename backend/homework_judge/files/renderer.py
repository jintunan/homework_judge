from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from ..config import Settings
from ..errors import AppError
from .storage import resolve_data_path

TARGET_WIDTH = 1800
TARGET_HEIGHT = 2400


@dataclass(frozen=True, slots=True)
class PreparedPage:
    id: str
    page_number: int
    relative_path: str
    width: int
    height: int
    sha256: str


def _save_image(image: Image.Image, output: Path, page_number: int) -> PreparedPage:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    scale = min(TARGET_WIDTH / normalized.width, TARGET_HEIGHT / normalized.height, 1.0)
    if scale < 1:
        normalized = normalized.resize(
            (max(1, round(normalized.width * scale)), max(1, round(normalized.height * scale))),
            Image.Resampling.LANCZOS,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(output, "JPEG", quality=88, optimize=True, progressive=True)
    data = output.read_bytes()
    return PreparedPage(
        id=uuid.uuid4().hex,
        page_number=page_number,
        relative_path="",
        width=normalized.width,
        height=normalized.height,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _render_pdf(
    settings: Settings,
    source: Path,
    output_dir: Path,
) -> list[PreparedPage]:
    try:
        document = pdfium.PdfDocument(source)
    except Exception as error:
        raise AppError(422, "PDF_UNREADABLE", "PDF 无法读取") from error
    try:
        count = len(document)
        if count == 0:
            raise AppError(422, "PDF_EMPTY", "PDF 不包含页面")
        if count > settings.max_document_pages:
            raise AppError(
                422,
                "DOCUMENT_TOO_MANY_PAGES",
                f"单文件最多处理 {settings.max_document_pages} 页",
            )
        pages: list[PreparedPage] = []
        for index in range(count):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = max(1.0, min(TARGET_WIDTH / width, TARGET_HEIGHT / height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    output = output_dir / f"page-{index + 1:04d}.jpg"
                    item = _save_image(image, output, index + 1)
                    pages.append(
                        PreparedPage(
                            id=item.id,
                            page_number=item.page_number,
                            relative_path=output.relative_to(settings.data_dir).as_posix(),
                            width=item.width,
                            height=item.height,
                            sha256=item.sha256,
                        )
                    )
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages
    finally:
        document.close()


def _find_soffice(settings: Settings) -> Path | None:
    candidates = [
        settings.soffice_path,
        shutil.which("soffice") or "",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    return next((Path(value) for value in candidates if value and Path(value).is_file()), None)


def _convert_docx(settings: Settings, source: Path, temp_dir: Path) -> Path:
    soffice = _find_soffice(settings)
    if soffice is None:
        return _convert_docx_with_word(source, temp_dir)
    profile = temp_dir / "lo-profile"
    profile.mkdir()
    command = [
        str(soffice),
        "--headless",
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(temp_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise AppError(504, "DOCX_CONVERSION_TIMEOUT", "DOCX 转换超时") from error
    pdf = temp_dir / f"{source.stem}.pdf"
    if completed.returncode != 0 or not pdf.is_file() or pdf.stat().st_size == 0:
        raise AppError(422, "DOCX_CONVERSION_FAILED", "DOCX 无法转换为页面")
    return pdf


def _convert_docx_with_word(source: Path, temp_dir: Path) -> Path:
    word_candidates = (
        Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
        Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE"),
    )
    if os.name != "nt" or not any(path.is_file() for path in word_candidates):
        raise AppError(
            503,
            "DOCX_CONVERTER_MISSING",
            "处理 DOCX 需要安装 LibreOffice 或 Microsoft Word",
        )
    pdf = temp_dir / f"{source.stem}.pdf"
    script = (
        "& { param([string]$inputPath,[string]$outputPath) "
        "$word=$null; $doc=$null; "
        "try { "
        "$word=New-Object -ComObject Word.Application; "
        "$word.Visible=$false; $word.DisplayAlerts=0; "
        "$doc=$word.Documents.Open($inputPath,$false,$true); "
        "$doc.SaveAs2($outputPath,17); "
        "} finally { "
        "if($doc -ne $null){$doc.Close($false)}; "
        "if($word -ne $null){$word.Quit()}; "
        "} }"
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
                str(source.resolve()),
                str(pdf.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise AppError(504, "DOCX_CONVERSION_TIMEOUT", "DOCX 转换超时") from error
    if completed.returncode != 0 or not pdf.is_file() or pdf.stat().st_size == 0:
        raise AppError(422, "DOCX_CONVERSION_FAILED", "DOCX 无法转换为页面")
    return pdf


def _prepare(
    settings: Settings,
    task_id: str,
    document_id: str,
    relative_path: str,
    mime_type: str,
) -> list[PreparedPage]:
    source = resolve_data_path(settings, relative_path)
    output_dir = settings.data_dir / "pages" / task_id / document_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    if mime_type == "application/pdf":
        return _render_pdf(settings, source, output_dir)
    if mime_type.startswith("image/"):
        try:
            with Image.open(source) as image:
                output = output_dir / "page-0001.jpg"
                item = _save_image(image, output, 1)
        except OSError as error:
            raise AppError(422, "IMAGE_UNREADABLE", "图片无法读取") from error
        return [
            PreparedPage(
                id=item.id,
                page_number=1,
                relative_path=output.relative_to(settings.data_dir).as_posix(),
                width=item.width,
                height=item.height,
                sha256=item.sha256,
            )
        ]
    if "wordprocessingml" in mime_type:
        with tempfile.TemporaryDirectory(dir=settings.data_dir / "tmp") as temp:
            pdf = _convert_docx(settings, source, Path(temp))
            return _render_pdf(settings, pdf, output_dir)
    raise AppError(415, "UNSUPPORTED_FILE", "无法处理该文件格式")


async def prepare_document_pages(
    settings: Settings,
    task_id: str,
    document_id: str,
    relative_path: str,
    mime_type: str,
) -> list[PreparedPage]:
    return await asyncio.to_thread(
        _prepare,
        settings,
        task_id,
        document_id,
        relative_path,
        mime_type,
    )
