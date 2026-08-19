from __future__ import annotations

import math
from io import BytesIO
from typing import Any, cast

from PIL import Image

from ..config import Settings
from ..db.database import Database, json_loads
from ..errors import AppError
from ..files.storage import resolve_data_path
from ..question_frames.service import QuestionFrameService


def current_question_images(
    database: Database,
    settings: Settings,
    task_id: str,
    question_id: str,
) -> tuple[dict[str, object], list[dict[str, Any]]]:
    frame_set = QuestionFrameService(database).get_current(task_id)
    if frame_set is None:
        raise AppError(409, "QUESTION_FRAME_REQUIRED", "请先确认当前题的题框")
    items = cast(list[dict[str, Any]], frame_set["items"])
    item = next(
        (value for value in items if value["questionId"] == question_id),
        None,
    )
    if (
        frame_set["status"] != "confirmed"
        or item is None
        or item["status"] != "confirmed"
        or not item["fragments"]
    ):
        raise AppError(409, "QUESTION_FRAME_NOT_CONFIRMED", "请先确认当前题的题框")
    fragments = sorted(
        item["fragments"],
        key=lambda value: (value["pageNumber"], value["sortOrder"], value["regionKey"]),
    )
    page_ids = [str(value["templatePageId"]) for value in fragments]
    placeholders = ",".join("?" for _value in page_ids)
    pages = database.fetchall(
        f"""SELECT p.id,p.page_number,p.image_path,p.width,p.height
            FROM pages p JOIN documents d ON d.id=p.document_id
            WHERE d.task_id=? AND d.role='exam' AND p.id IN ({placeholders})""",
        (task_id, *page_ids),
    )
    by_id = {str(page["id"]): page for page in pages}
    output: list[dict[str, Any]] = []
    for fragment in fragments:
        page = by_id.get(str(fragment["templatePageId"]))
        if page is None:
            raise AppError(422, "QUESTION_FRAME_PAGE_MISSING", "题框引用的模板页面不存在")
        path = resolve_data_path(settings, str(page["image_path"]))
        try:
            with Image.open(path) as opened:
                if opened.size != (int(page["width"]), int(page["height"])):
                    raise AppError(422, "QUESTION_PAGE_SIZE_MISMATCH", "模板原图尺寸与记录不一致")
                left = math.floor(float(fragment["x"]) * opened.width)
                top = math.floor(float(fragment["y"]) * opened.height)
                right = math.ceil((float(fragment["x"]) + float(fragment["width"])) * opened.width)
                bottom = math.ceil(
                    (float(fragment["y"]) + float(fragment["height"])) * opened.height
                )
                if right <= left or bottom <= top:
                    raise AppError(422, "QUESTION_FRAME_CROP_INVALID", "当前题框没有有效面积")
                image = opened.convert("RGB").crop((left, top, right, bottom))
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=95)
        except AppError:
            raise
        except (OSError, ValueError) as error:
            raise AppError(422, "QUESTION_PAGE_IMAGE_INVALID", "当前题原图无法读取") from error
        output.append(
            {
                "label": f"题目原图 第{fragment['pageNumber']}页 片段{fragment['sortOrder'] + 1}",
                "image": buffer.getvalue(),
            }
        )
    return frame_set, output


def reference_answer_images(
    database: Database,
    settings: Settings,
    task_id: str,
    answer_entry_id: str | None,
) -> list[dict[str, Any]]:
    if not answer_entry_id:
        return []
    entry = database.fetchone(
        "SELECT source_pages_json FROM answer_entries WHERE id=? AND task_id=?",
        (answer_entry_id, task_id),
    )
    if entry is None:
        return []
    page_numbers = sorted({int(value) for value in json_loads(entry["source_pages_json"], [])})
    if not page_numbers:
        return []
    placeholders = ",".join("?" for _value in page_numbers)
    pages = database.fetchall(
        f"""SELECT p.page_number,p.image_path FROM pages p
            JOIN documents d ON d.id=p.document_id
            WHERE d.task_id=? AND d.role='answer' AND p.page_number IN ({placeholders})
            ORDER BY p.page_number""",
        (task_id, *page_numbers),
    )
    output: list[dict[str, Any]] = []
    for page in pages:
        path = resolve_data_path(settings, str(page["image_path"]))
        try:
            with Image.open(path) as opened:
                buffer = BytesIO()
                opened.convert("RGB").save(buffer, format="JPEG", quality=95)
                data = buffer.getvalue()
        except OSError as error:
            raise AppError(422, "ANSWER_PAGE_IMAGE_INVALID", "参考答案原图无法读取") from error
        output.append({"label": f"参考答案原页 第{page['page_number']}页", "image": data})
    return output
