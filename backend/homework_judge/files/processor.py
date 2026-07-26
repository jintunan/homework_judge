from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import Settings
from ..errors import AppError
from .storage import resolve_data_path

_TARGET_WIDTH = 1800
_TARGET_HEIGHT = 2400
_JPEG_QUALITY = 86


@dataclass(frozen=True, slots=True)
class PreparedPage:
    page_number: int
    mime_type: str
    data_url: str
    byte_length: int


def _encode_page(image: Image.Image, page_number: int) -> PreparedPage:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    width, height = normalized.size
    scale = min(_TARGET_WIDTH / width, _TARGET_HEIGHT / height)
    target = (
        max(1, round(width * scale)),
        max(1, round(height * scale)),
    )
    if target != normalized.size:
        normalized = normalized.resize(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", normalized.size, "white")
    canvas.paste(normalized)
    output = io.BytesIO()
    canvas.save(
        output,
        format="JPEG",
        quality=_JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )
    data = output.getvalue()
    encoded = base64.b64encode(data).decode("ascii")
    return PreparedPage(
        page_number=page_number,
        mime_type="image/jpeg",
        data_url=f"data:image/jpeg;base64,{encoded}",
        byte_length=len(data),
    )


def _render_image(path: Path) -> list[PreparedPage]:
    try:
        with Image.open(path) as image:
            return [_encode_page(image, 1)]
    except (OSError, UnidentifiedImageError) as error:
        raise AppError(422, "IMAGE_UNREADABLE", "试卷图像无法读取") from error


def _render_pdf(path: Path, max_pages: int) -> list[PreparedPage]:
    try:
        document = pdfium.PdfDocument(path)
    except Exception as error:
        raise AppError(422, "PDF_UNREADABLE", f"PDF 无法读取：{error}") from error
    try:
        page_count = len(document)
        if page_count == 0:
            raise AppError(422, "PDF_EMPTY", "PDF 不包含可读取页面")
        if page_count > max_pages:
            raise AppError(422, "PDF_TOO_MANY_PAGES", f"首版最多处理 {max_pages} 页 PDF")

        pages: list[PreparedPage] = []
        for index in range(page_count):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = min(_TARGET_WIDTH / width, _TARGET_HEIGHT / height)
                bitmap = page.render(scale=max(scale, 1.0))
                try:
                    image = bitmap.to_pil()
                    pages.append(_encode_page(image, index + 1))
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages
    except AppError:
        raise
    except Exception as error:
        raise AppError(422, "PDF_RENDER_FAILED", f"PDF 页面渲染失败：{error}") from error
    finally:
        document.close()


def _prepare(settings: Settings, relative_path: str, mime_type: str) -> list[PreparedPage]:
    path = resolve_data_path(settings, relative_path)
    if not path.is_file():
        raise AppError(404, "FILE_MISSING", "文件记录存在，但原始文件已丢失")
    if mime_type == "application/pdf":
        return _render_pdf(path, settings.max_pdf_pages)
    if mime_type in {"image/jpeg", "image/png"}:
        return _render_image(path)
    raise AppError(415, "UNSUPPORTED_FILE", f"不支持处理 {path.suffix} 文件")


async def prepare_model_pages(
    settings: Settings,
    relative_path: str,
    mime_type: str,
) -> list[PreparedPage]:
    try:
        return await asyncio.to_thread(_prepare, settings, relative_path, mime_type)
    except AppError:
        raise
    except Exception as error:
        raise AppError(422, "IMAGE_PROCESSING_FAILED", f"试卷图像处理失败：{error}") from error
