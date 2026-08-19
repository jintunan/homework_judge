from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..config import Settings
from ..errors import AppError


@dataclass(frozen=True, slots=True)
class SavedUpload:
    original_name: str
    stored_name: str
    mime_type: str
    extension: str
    size_bytes: int
    sha256: str
    relative_path: str


@dataclass(slots=True)
class StagedDeletion:
    staging_root: Path
    moved: list[tuple[Path, Path]]

    def rollback(self) -> None:
        for source, staged in reversed(self.moved):
            if staged.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(source)
        if self.staging_root.exists():
            shutil.rmtree(self.staging_root)

    def commit(self) -> bool:
        if not self.staging_root.exists():
            return False
        try:
            shutil.rmtree(self.staging_root)
        except OSError:
            return True
        return False


def resolve_data_path(settings: Settings, relative_path: str) -> Path:
    candidate = (settings.data_dir / relative_path).resolve()
    root = settings.data_dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise AppError(403, "FILE_PATH_FORBIDDEN", "文件路径不在允许的数据目录内")
    return candidate


async def save_upload(
    settings: Settings,
    task_id: str,
    role: str,
    upload: UploadFile,
) -> SavedUpload:
    original_name = Path(upload.filename or "").name
    extension = Path(original_name).suffix.lower()
    if not original_name:
        raise AppError(422, "FILE_NAME_MISSING", "上传文件缺少文件名")
    tmp_dir = settings.data_dir / "tmp" / task_id
    final_dir = settings.data_dir / "uploads" / task_id / role
    tmp_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    temporary = tmp_dir / f"{stored_name}.part"
    final = final_dir / stored_name
    digest = hashlib.sha256()
    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    try:
        with temporary.open("xb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise AppError(
                        413,
                        "FILE_TOO_LARGE",
                        f"单文件不能超过 {settings.max_upload_mb} MB",
                    )
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise AppError(422, "FILE_EMPTY", "不能上传空文件")
        os.replace(temporary, final)
    except Exception:
        temporary.unlink(missing_ok=True)
        if final.exists():
            final.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    relative = final.relative_to(settings.data_dir).as_posix()
    return SavedUpload(
        original_name=original_name,
        stored_name=stored_name,
        mime_type=upload.content_type or "application/octet-stream",
        extension=extension,
        size_bytes=size,
        sha256=digest.hexdigest(),
        relative_path=relative,
    )


def remove_task_files(settings: Settings, task_id: str) -> None:
    if (
        not task_id
        or task_id in {".", ".."}
        or Path(task_id).name != task_id
        or "/" in task_id
        or "\\" in task_id
    ):
        raise AppError(400, "TASK_ID_INVALID", "任务标识不安全，拒绝删除文件")
    for base in ("uploads", "pages", "tmp"):
        path = (settings.data_dir / base / task_id).resolve()
        root = (settings.data_dir / base).resolve()
        if root not in path.parents:
            raise AppError(403, "FILE_PATH_FORBIDDEN", "任务目录不在允许的数据目录内")
        if path.exists():
            shutil.rmtree(path)


def remove_submission_files(settings: Settings, task_id: str, submission_id: str) -> None:
    candidates = (
        (settings.data_dir / "uploads" / task_id / "students" / submission_id),
        (settings.data_dir / "pages" / task_id / f"student-{submission_id}"),
    )
    for candidate in candidates:
        path = candidate.resolve()
        root = settings.data_dir.resolve()
        if root in path.parents and path.exists():
            shutil.rmtree(path, ignore_errors=True)


def _safe_identifier(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise AppError(400, f"{label.upper()}_INVALID", f"{label}标识不安全，拒绝删除文件")
    return value


def stage_submission_deletion(
    settings: Settings,
    task_id: str,
    submission_id: str,
    grading_run_ids: list[str],
) -> StagedDeletion:
    task_id = _safe_identifier(task_id, "task")
    submission_id = _safe_identifier(submission_id, "submission")
    run_ids = [_safe_identifier(value, "grading_run") for value in grading_run_ids]
    candidates = [
        settings.data_dir / "uploads" / task_id / "students" / submission_id,
        settings.data_dir / "pages" / task_id / f"student-{submission_id}",
        *(settings.data_dir / "artifacts" / run_id for run_id in run_ids),
    ]
    data_root = settings.data_dir.resolve()
    resolved: list[Path] = []
    for candidate in candidates:
        path = candidate.resolve()
        if data_root not in path.parents:
            raise AppError(403, "FILE_PATH_FORBIDDEN", "答卷文件不在允许的数据目录内")
        if path.exists():
            resolved.append(path)
    staging_root = (
        settings.data_dir / "tmp" / "deletions" / f"submission-{uuid.uuid4().hex}"
    ).resolve()
    if data_root not in staging_root.parents:
        raise AppError(403, "FILE_PATH_FORBIDDEN", "删除暂存目录不在允许的数据目录内")
    staging_root.mkdir(parents=True, exist_ok=False)
    staged = StagedDeletion(staging_root, [])
    try:
        for index, source in enumerate(resolved):
            target = staging_root / f"item-{index}"
            source.replace(target)
            staged.moved.append((source, target))
    except OSError as error:
        try:
            staged.rollback()
        except OSError:
            pass
        raise AppError(
            500,
            "STUDENT_SUBMISSION_FILE_DELETE_FAILED",
            "学生答卷文件暂时无法删除，数据记录已保留，请重试",
            {"reason": str(error)},
        ) from error
    return staged
