from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from starlette.datastructures import UploadFile

from ..config import Settings
from ..errors import AppError

FileKind = Literal["template", "reference_answer", "submission"]

_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_MIME_EXTENSION = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
_KIND_FOLDER = {
    "template": "templates",
    "reference_answer": "reference-answers",
    "submission": "submissions",
}


@dataclass(frozen=True, slots=True)
class PersistedFile:
    id: str
    kind: FileKind
    original_name: str
    stored_name: str
    mime_type: str
    size: int
    relative_path: str


def normalize_original_name(original_name: str) -> str:
    name = Path(original_name.replace("\\", "/")).name.strip()
    if not name:
        return "未命名文件"
    if any("\u4e00" <= character <= "\u9fff" for character in name):
        return name[:255]
    try:
        decoded = name.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name[:255]
    return decoded[:255] if "\ufffd" not in decoded else name[:255]


def detect_mime(header: bytes) -> str | None:
    if header.startswith(b"%PDF"):
        return "application/pdf"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def resolve_data_path(settings: Settings, relative_path: str) -> Path:
    root = settings.app_data_dir.resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise AppError(400, "INVALID_FILE_PATH", "文件路径不合法")
    return target


async def _read_limited(upload: UploadFile, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise AppError(
                413,
                "FILE_TOO_LARGE",
                f"单个文件不能超过 {maximum // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def persist_upload(
    settings: Settings,
    upload: UploadFile,
    kind: FileKind,
) -> PersistedFile:
    settings.ensure_directories()
    original_name = normalize_original_name(upload.filename or "")
    extension = Path(original_name).suffix.lower()
    declared_mime = (upload.content_type or "").lower()
    expected_mime = _EXTENSION_MIME.get(extension)
    if expected_mime is None or declared_mime != expected_mime:
        raise AppError(
            415,
            "UNSUPPORTED_FILE",
            f"不支持文件“{original_name}”，请上传 PDF、JPG、JPEG 或 PNG",
        )

    try:
        content = await _read_limited(upload, settings.max_upload_bytes)
    finally:
        await upload.close()
    detected_mime = detect_mime(content[:16])
    if detected_mime is None or detected_mime != expected_mime:
        raise AppError(
            415,
            "FILE_SIGNATURE_MISMATCH",
            f"文件“{original_name}”的内容与扩展名不一致",
        )

    file_id = str(uuid4())
    stored_name = f"{file_id}{_MIME_EXTENSION[detected_mime]}"
    relative_path = (Path("uploads") / _KIND_FOLDER[kind] / stored_name).as_posix()
    destination = resolve_data_path(settings, relative_path)
    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(destination.write_bytes, content)
    except OSError as error:
        raise AppError(500, "FILE_SAVE_FAILED", "文件保存失败") from error
    return PersistedFile(
        id=file_id,
        kind=kind,
        original_name=original_name,
        stored_name=stored_name,
        mime_type=detected_mime,
        size=len(content),
        relative_path=relative_path,
    )


async def remove_persisted_file(settings: Settings, file: PersistedFile) -> None:
    target = resolve_data_path(settings, file.relative_path)
    try:
        await asyncio.to_thread(target.unlink, missing_ok=True)
    except OSError:
        return
