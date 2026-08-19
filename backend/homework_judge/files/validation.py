from __future__ import annotations

from zipfile import BadZipFile, ZipFile

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from ..config import Settings
from ..errors import AppError
from .storage import SavedUpload, resolve_data_path

ALLOWED = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}


def validate_upload(settings: Settings, saved: SavedUpload) -> str:
    path = resolve_data_path(settings, saved.relative_path)
    extension = saved.extension
    if extension not in ALLOWED:
        raise AppError(
            415,
            "UNSUPPORTED_FILE",
            "仅支持 PDF、DOCX、JPG、PNG 文件",
        )
    header = path.read_bytes()[:16]
    if extension == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise AppError(422, "PDF_SIGNATURE_INVALID", "文件扩展名是 PDF，但内容不是 PDF")
        try:
            document = pdfium.PdfDocument(path)
            try:
                if len(document) == 0:
                    raise AppError(422, "PDF_EMPTY", "PDF 不包含页面")
                if len(document) > settings.max_document_pages:
                    raise AppError(
                        422,
                        "DOCUMENT_TOO_MANY_PAGES",
                        f"单文件最多处理 {settings.max_document_pages} 页",
                    )
            finally:
                document.close()
        except AppError:
            raise
        except Exception as error:
            raise AppError(422, "PDF_UNREADABLE", "PDF 无法读取或已加密") from error
        return "application/pdf"
    if extension == ".docx":
        if not header.startswith(b"PK"):
            raise AppError(422, "DOCX_SIGNATURE_INVALID", "文件扩展名是 DOCX，但内容不是 DOCX")
        try:
            with ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise AppError(422, "DOCX_STRUCTURE_INVALID", "DOCX 结构不完整")
        except BadZipFile as error:
            raise AppError(422, "DOCX_UNREADABLE", "DOCX 无法读取") from error
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    try:
        with Image.open(path) as image:
            image.verify()
            detected = image.format
    except (OSError, UnidentifiedImageError) as error:
        raise AppError(422, "IMAGE_UNREADABLE", "图片无法读取") from error
    expected = "JPEG" if extension in {".jpg", ".jpeg"} else "PNG"
    if detected != expected:
        raise AppError(422, "IMAGE_SIGNATURE_INVALID", "图片扩展名与实际格式不一致")
    return "image/jpeg" if expected == "JPEG" else "image/png"
