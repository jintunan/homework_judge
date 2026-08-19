from __future__ import annotations

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from homework_judge.db.database import now_iso
from homework_judge.main import app
from homework_judge.question_frames.service import QuestionFrameService


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, "PNG")
    return output.getvalue()


def test_upload_reaches_safe_model_configuration_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    # Keep the test isolated from a developer's real .env without reading or
    # overwriting the configured key. Empty process variables take precedence.
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["data"]["model"]["configured"] is False

        response = client.post(
            "/api/tasks",
            data={"title": "API 流程测试"},
            files={
                "exam": ("exam.png", png_bytes(), "image/png"),
                "answer": ("answer.png", png_bytes(), "image/png"),
            },
        )
        assert response.status_code == 201
        task_id = response.json()["data"]["taskId"]

        progress = None
        for _ in range(100):
            progress = client.get(f"/api/tasks/{task_id}/progress").json()["data"]
            if progress["status"] == "failed":
                break
            time.sleep(0.02)
        assert progress is not None
        assert progress["status"] == "failed"
        assert progress["errorCode"] == "MODEL_NOT_CONFIGURED"

        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        assert {item["role"] for item in detail["documents"]} == {"exam", "answer"}
        assert all(item["page_count"] == 1 for item in detail["documents"])


def test_review_can_mark_and_restore_duplicate_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database = app.state.database
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks(id,title,status,created_at,updated_at)
                   VALUES('duplicate-task','重复题测试','review_pending',?,?)""",
                (timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
                   VALUES('duplicate-run','duplicate-task','exam_recognition',
                   'succeeded','exam_recognition',?)""",
                (timestamp,),
            )
            connection.execute(
                """INSERT INTO questions(id,task_id,source_run_id,sort_order,
                   detected_number,normalized_number,stem,options_json,question_type,
                   score,source_pages_json,confidence,issues_json,confirmation_status)
                   VALUES('duplicate-q','duplicate-task','duplicate-run',0,'10','10',
                   '重复题干','[]','fill_blank',4,'[1]',1,'[]','pending')"""
            )
            connection.execute(
                """INSERT INTO answer_entries(id,task_id,source_run_id,sort_order,
                   number_hint,normalized_number,stem_hint,answer,explanation,
                   source_pages_json,confidence,issues_json)
                   VALUES('duplicate-a','duplicate-task','duplicate-run',0,'10','10','',
                   '答案','','[1]',1,'[]')"""
            )
            connection.execute(
                """INSERT INTO matches(id,task_id,question_id,answer_entry_id,method,
                   number_score,stem_score,order_score,total_score,reasons_json,status,updated_at)
                   VALUES('duplicate-m','duplicate-task','duplicate-q','duplicate-a',
                   'number_exact',1,0,1,1,'[]','suggested',?)""",
                (timestamp,),
            )

        marked = client.post("/api/questions/duplicate-q/mark-duplicate")
        assert marked.status_code == 200
        assert marked.json()["data"]["isDuplicate"] is True
        review = client.get("/api/tasks/duplicate-task/review").json()["data"]
        assert review["questions"][0]["isDuplicate"] is True
        assert review["answerEntries"][0]["questionId"] is None
        summary = client.get("/api/tasks/duplicate-task").json()["data"]
        assert summary["questionCount"] == 0

        restored = client.post("/api/questions/duplicate-q/restore")
        assert restored.status_code == 200
        assert restored.json()["data"]["isDuplicate"] is False
        review = client.get("/api/tasks/duplicate-task/review").json()["data"]
        assert review["questions"][0]["confirmationStatus"] == "pending"
        assert review["questions"][0]["match"]["answerEntryId"] == "duplicate-a"


def test_task_completion_requires_confirmed_question_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database = app.state.database
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks(id,title,status,created_at,updated_at)
                   VALUES('no-frame-task','无题框任务','review_pending',?,?)""",
                (timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
                   VALUES('no-frame-run','no-frame-task','exam_recognition','succeeded','done',?)""",
                (timestamp,),
            )
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                     stem,options_json,question_type,score,source_pages_json,confidence,
                     issues_json,confirmation_status
                   ) VALUES('no-frame-q','no-frame-task','no-frame-run',0,'1','1','题干',
                     '["A","B"]','single_choice',2,'[1]',1,'[]','confirmed')"""
            )
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,teacher_answer,updated_at
                   ) VALUES('no-frame-m','no-frame-task','no-frame-q','manual',
                     'confirmed','A',?)""",
                (timestamp,),
            )

        response = client.post("/api/tasks/no-frame-task/complete")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "QUESTION_FRAMES_NOT_CONFIRMED"
        assert database.fetchone("SELECT status FROM tasks WHERE id='no-frame-task'")["status"] == (
            "review_pending"
        )


def test_task_completion_never_creates_or_confirms_fill_configs_implicitly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database = app.state.database
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks(id,title,status,created_at,updated_at)
                   VALUES('fill-task','填空事务测试','review_pending',?,?)""",
                (timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
                   VALUES('fill-run','fill-task','exam_recognition','succeeded','done',?)""",
                (timestamp,),
            )
            connection.execute(
                """INSERT INTO documents(
                     id,task_id,role,original_name,stored_name,mime_type,extension,
                     size_bytes,sha256,page_count,relative_path,created_at
                   ) VALUES('fill-exam','fill-task','exam','exam.pdf','exam.pdf',
                     'application/pdf','.pdf',1,'fill-sha',1,'exam.pdf',?)""",
                (timestamp,),
            )
            connection.execute(
                """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
                   VALUES('fill-page','fill-exam',1,'fill-page.jpg',1000,1400,'fill-page-sha')"""
            )
            answers = ("甲 乙 丙", "丁 戊 己", "复杂 答案")
            for index, answer in enumerate(answers, start=1):
                connection.execute(
                    """INSERT INTO questions(
                         id,task_id,source_run_id,sort_order,detected_number,
                         normalized_number,stem,question_type,score,source_pages_json,
                         confidence,issues_json,confirmation_status
                       ) VALUES(?, 'fill-task','fill-run',?,?,?,?,'fill_blank',3,
                         '[1]',1,'[]','confirmed')""",
                    (
                        f"fill-q{index}",
                        index,
                        str(index),
                        str(index),
                        "甲______乙______丙______",
                    ),
                )
                connection.execute(
                    """INSERT INTO matches(
                         id,task_id,question_id,method,status,teacher_answer,updated_at
                       ) VALUES(?, 'fill-task',?,'manual','confirmed',?,?)""",
                    (f"fill-m{index}", f"fill-q{index}", answer, timestamp),
                )

        frame_service = QuestionFrameService(database)
        frame_set = frame_service.create_draft(
            "fill-task",
            [
                {
                    "questionId": f"fill-q{index}",
                    "fragments": [
                        {
                            "regionKey": f"fill-q{index}:frame:1",
                            "templatePageId": "fill-page",
                            "pageNumber": 1,
                            "x": 0.05,
                            "y": 0.05 + ((index - 1) * 0.3),
                            "width": 0.9,
                            "height": 0.2,
                            "sortOrder": 0,
                            "source": "teacher",
                            "confidence": 1.0,
                            "issues": [],
                        }
                    ],
                }
                for index in range(1, 4)
            ],
            source="teacher",
            actor="teacher:test",
        )
        for index in range(1, 4):
            frame_set = frame_service.confirm_item(
                str(frame_set["id"]),
                f"fill-q{index}",
                expected_revision=int(frame_set["revision"]),
                actor="teacher:test",
            )
        frame_service.confirm_set(
            str(frame_set["id"]),
            expected_revision=int(frame_set["revision"]),
            actor="teacher:test",
        )

        blocked = client.post("/api/tasks/fill-task/complete")

        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "FILL_BLANK_CONFIG_REVIEW_REQUIRED"
        assert {
            item["questionNumber"]
            for item in blocked.json()["error"]["details"]["questions"]
        } == {"3"}
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_grading_configs")["count"]
            == 0
        )
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_blank_definitions")["count"]
            == 0
        )

        still_blocked = client.post("/api/tasks/fill-task/complete")

        assert still_blocked.status_code == 409
        assert still_blocked.json()["error"]["code"] == "FILL_BLANK_CONFIG_REVIEW_REQUIRED"
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_grading_configs")["count"]
            == 0
        )
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_blank_definitions")["count"]
            == 0
        )
        assert (
            database.fetchone(
                """SELECT COUNT(*) AS count FROM audit_events
               WHERE event_type='fill_blank_config_auto_confirmed'"""
            )["count"]
            == 0
        )
