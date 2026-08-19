from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from homework_judge.db.database import Database, json_dumps, json_loads, now_iso
from homework_judge.errors import AppError
from homework_judge.main import app


class _NoopStudentPipeline:
    async def run(self, _submission_id: str) -> None:
        return


class _SingleQuestionRecognition:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[dict[str, object], list[dict[str, object]]]] = []

    @staticmethod
    def prompt_version(role: str) -> str:
        assert role == "single_question"
        return "single-question-test-v1"

    async def recognize_single_question(
        self,
        question: dict[str, object],
        fragments: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, int]]:
        self.calls.append((question, fragments))
        if self.fail:
            raise AppError(502, "MODEL_TIMEOUT", "模拟单题模型超时")
        return (
            {
                "detected_number": "12",
                "normalized_number": "12",
                "stem": "第一页题干，第二页跨页续文",
                "options": [],
                "question_type": "calculation",
                "score": 10.0,
                "source_pages": [1, 2],
                "confidence": 0.97,
                "issues": [],
            },
            {"id": "single-question-model-response"},
            {"promptTokens": 3, "completionTokens": 4, "totalTokens": 7},
        )


class _SupersedingSingleQuestionRecognition(_SingleQuestionRecognition):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def recognize_single_question(
        self,
        question: dict[str, object],
        fragments: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, int]]:
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE question_frame_sets SET revision=revision+1
                   WHERE id=(SELECT current_question_frame_set_id FROM tasks WHERE id='task')"""
            )
        return await super().recognize_single_question(question, fragments)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, "PNG")
    return output.getvalue()


def _save_image(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, "JPEG")


def _seed(database: Database) -> None:
    timestamp = now_iso()
    run_id = uuid.uuid4().hex
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','题框 API','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,
                 size_bytes,sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf',
                 '.pdf',1,'sha',1,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('page','exam',1,'page.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES(?,'task','exam_recognition','succeeded','done',?)""",
            (run_id, timestamp),
        )
        for sort_order, question_id, number in ((0, "q-one", "任意甲"), (1, "q-two", "任意乙")):
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                     stem,options_json,question_type,score,source_pages_json,confidence,
                     issues_json,confirmation_status
                   ) VALUES(?,?,?,?,?,?,?,'[]','fill_blank',4,'[1]',1,'[]','pending')""",
                (question_id, "task", run_id, sort_order, number, number, f"题干 {number}"),
            )


def _fragment(question_id: str, y: float, source: str = "model") -> dict[str, object]:
    return {
        "regionKey": f"{question_id}:part:1",
        "templatePageId": "page",
        "pageNumber": 1,
        "x": 0.05,
        "y": y,
        "width": 0.9,
        "height": 0.25,
        "sortOrder": 0,
        "source": source,
        "confidence": 0.92,
        "issues": [],
    }


def _blank_config(frame_set_id: str, *, anchor_y: float) -> dict[str, object]:
    return {
        "questionType": "fill_blank",
        "maxScore": "4.00",
        "frameSetId": frame_set_id,
        "expectedConfigVersion": 0,
        "confirm": True,
        "blanks": [
            {
                "blankKey": "B1",
                "sortOrder": 0,
                "maxScore": "4.00",
                "answerKind": "text",
                "standardAnswers": ["任意答案"],
                "synonyms": [],
                "anchor": {
                    "templatePageId": "page",
                    "pageNumber": 1,
                    "coordinateSpace": "template_page_normalized",
                    "box": {"x": 0.2, "y": anchor_y, "width": 0.3, "height": 0.05},
                    "source": "teacher",
                    "confidence": 1,
                    "issues": [],
                },
            }
        ],
    }


def test_question_frame_api_gates_upload_and_preserves_versions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        app.state.student_pipeline = _NoopStudentPipeline()

        current = client.get("/api/tasks/task/question-frame-sets/current")
        assert current.status_code == 200
        assert current.json()["data"]["frameSet"] is None
        assert current.json()["data"]["studentProcessingGate"]["ready"] is False

        blocked_upload = client.post(
            "/api/tasks/task/student-submissions",
            files={"file": ("student.png", _png_bytes(), "image/png")},
        )
        assert blocked_upload.status_code == 409
        assert blocked_upload.json()["error"]["code"] == "QUESTION_FRAMES_NOT_CONFIRMED"
        assert not (runtime / "uploads" / "task" / "students").exists()

        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        )
        assert created.status_code == 201
        draft = created.json()["data"]
        assert draft["status"] == "draft"
        assert draft["revision"] == 0

        normalized = client.post(
            f"/api/question-frame-sets/{draft['id']}/normalize-model-draft",
            json={"expectedRevision": 0},
        )
        assert normalized.status_code == 200
        assert normalized.json()["data"]["revision"] == 0

        first = client.post(
            f"/api/question-frame-sets/{draft['id']}/questions/q-one/confirm",
            json={"expectedRevision": 0},
        )
        assert first.status_code == 200
        assert first.json()["data"]["revision"] == 1
        partial_gate = client.get("/api/tasks/task/student-processing-gate").json()["data"]
        assert partial_gate["ready"] is False
        assert partial_gate["unconfirmedQuestionIds"] == ["q-two"]

        second = client.post(
            f"/api/question-frame-sets/{draft['id']}/questions/q-two/confirm",
            json={"expectedRevision": 1},
        )
        assert second.status_code == 200
        assert second.json()["data"]["revision"] == 2

        conflict = client.post(
            f"/api/question-frame-sets/{draft['id']}/confirm",
            json={"expectedRevision": 1},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "FRAME_SET_REVISION_CONFLICT"
        assert conflict.json()["error"]["details"]["currentRevision"] == 2

        frozen_response = client.post(
            f"/api/question-frame-sets/{draft['id']}/confirm",
            json={"expectedRevision": 2},
        )
        assert frozen_response.status_code == 200
        frozen = frozen_response.json()["data"]
        assert frozen["status"] == "confirmed"
        assert frozen["revision"] == 3

        review = client.get("/api/tasks/task/review")
        assert review.status_code == 200
        review_data = review.json()["data"]
        assert review_data["questionFrameSet"]["id"] == frozen["id"]
        assert review_data["studentUploadGate"]["ready"] is False
        assert {
            issue["code"] for issue in review_data["studentUploadGate"]["blankConfigIssues"]
        } == {"BLANK_CONFIG_MISSING"}
        assert review_data["pages"][0] == {
            "id": "page",
            "document_id": "exam",
            "page_number": 1,
            "width": 1000,
            "height": 1400,
            "role": "exam",
            "imageUrl": "/api/pages/page",
        }

        for question_id, anchor_y in (("q-one", 0.12), ("q-two", 0.62)):
            configured = client.put(
                f"/api/questions/{question_id}/grading-config",
                json=_blank_config(frozen["id"], anchor_y=anchor_y),
            )
            assert configured.status_code == 200
            assert configured.json()["data"]["status"] == "teacher_confirmed"

        ready_gate = client.get("/api/tasks/task/student-processing-gate").json()["data"]
        assert ready_gate["ready"] is True

        uploaded = client.post(
            "/api/tasks/task/student-submissions",
            data={"studentName": "框后上传"},
            files={"file": ("student.png", _png_bytes(), "image/png")},
        )
        assert uploaded.status_code == 202

        forked_response = client.patch(
            f"/api/question-frame-sets/{frozen['id']}/questions/q-one",
            json={
                "expectedRevision": 3,
                "regions": [_fragment("q-one", 0.08, source="teacher")],
            },
        )
        assert forked_response.status_code == 200
        forked = forked_response.json()["data"]
        assert forked["versionNumber"] == 2
        assert forked["baseFrameSetId"] == frozen["id"]
        assert {item["questionId"]: item["status"] for item in forked["items"]} == {
            "q-one": "pending",
            "q-two": "confirmed",
        }
        forked_gate = client.get("/api/tasks/task/student-processing-gate").json()["data"]
        assert forked_gate["ready"] is False


def test_single_question_rerecognition_updates_only_target_and_preserves_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        _save_image(runtime / "page.jpg", (1000, 1400))
        _save_image(runtime / "page-2.jpg", (800, 1200))
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
                   VALUES('page-2','exam',2,'page-2.jpg',800,1200,'page-2-sha')"""
            )
            connection.execute("UPDATE documents SET page_count=2 WHERE id='exam'")
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,answer_entry_id,method,number_score,stem_score,
                     order_score,total_score,reasons_json,status,teacher_answer,
                     teacher_explanation,updated_at
                   ) VALUES('match-one','task','q-one',NULL,'direct_entry',1,1,1,1,'[]',
                     'confirmed','教师答案','教师解析',?)""",
                (timestamp,),
            )
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,reasons_json,updated_at
                   ) VALUES('match-two','task','q-two','unmatched','suggested','[]',?)""",
                (timestamp,),
            )
            connection.execute(
                """UPDATE questions SET detected_number='12',normalized_number='12',
                   stem='第一页旧题干',teacher_override_json=? WHERE id='q-one'""",
                (json_dumps({"number": "12", "stem": "教师修正题干", "options": [],
                             "type": "calculation", "score": 10}),),
            )
        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        recognition = _SingleQuestionRecognition()
        app.state.recognition_service = recognition
        regions = [
            _fragment("q-one", 0.08, source="teacher"),
            {
                "regionKey": "q-one:part:2",
                "templatePageId": "page-2",
                "pageNumber": 2,
                "x": 0.1,
                "y": 0.1,
                "width": 0.5,
                "height": 0.3,
                "sortOrder": 1,
                "source": "teacher",
                "confidence": None,
                "issues": [],
            },
        ]

        response = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/rerecognize",
            json={"expectedRevision": 0, "regions": regions},
        )

        assert response.status_code == 200
        result = response.json()["data"]
        assert result["questionId"] == "q-one"
        assert result["teacherOverridePreserved"] is True
        saved_page_numbers = [
            item["pageNumber"]
            for item in result["frameSet"]["items"][0]["fragments"]
        ]
        assert saved_page_numbers == [1, 2]
        assert len(recognition.calls) == 1
        sent_fragments = recognition.calls[0][1]
        assert [(item["page_number"], item["sort_order"]) for item in sent_fragments] == [
            (1, 0),
            (2, 1),
        ]
        assert all(bytes(item["image"]).startswith(b"\xff\xd8") for item in sent_fragments)
        crop_sizes: list[tuple[int, int]] = []
        for item in sent_fragments:
            with Image.open(io.BytesIO(bytes(item["image"]))) as crop:
                crop_sizes.append(crop.size)
        assert crop_sizes == [(900, 350), (400, 360)]
        question = database.fetchone("SELECT * FROM questions WHERE id='q-one'")
        assert question is not None
        assert question["id"] == "q-one"
        assert question["stem"] == "第一页题干，第二页跨页续文"
        assert json_loads(question["source_pages_json"], []) == [1, 2]
        assert json_loads(question["teacher_override_json"], {})["stem"] == "教师修正题干"
        assert question["confirmation_status"] == "pending"
        assert database.fetchone("SELECT stem FROM questions WHERE id='q-two'") == {
            "stem": "题干 任意乙"
        }
        match = database.fetchone("SELECT * FROM matches WHERE id='match-one'")
        assert match is not None
        assert match["teacher_answer"] == "教师答案"
        assert match["teacher_explanation"] == "教师解析"
        assert match["status"] == "suggested"
        assert "原答案关联已保留" in "；".join(json_loads(match["reasons_json"], []))
        run = database.fetchone("SELECT * FROM runs WHERE id=?", (result["runId"],))
        assert run is not None
        assert run["kind"] == "single_question_recognition"
        assert run["status"] == "succeeded"
        assert json_loads(run["usage_json"], {})["totalTokens"] == 7


def test_single_question_rerecognition_failure_keeps_saved_frame_and_old_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        _save_image(runtime / "page.jpg", (1000, 1400))
        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        recognition = _SingleQuestionRecognition(fail=True)
        app.state.recognition_service = recognition
        changed = _fragment("q-one", 0.12, source="teacher")

        response = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/rerecognize",
            json={"expectedRevision": 0, "regions": [changed]},
        )

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "MODEL_TIMEOUT"
        assert response.json()["error"]["details"]["questionContentUnchanged"] is True
        assert response.json()["error"]["details"]["savedFrameSet"]["revision"] == 1
        assert database.fetchone("SELECT stem FROM questions WHERE id='q-one'") == {
            "stem": "题干 任意甲"
        }
        saved = client.get("/api/tasks/task/question-frame-sets/current").json()["data"][
            "frameSet"
        ]
        fragment = next(item for item in saved["items"] if item["questionId"] == "q-one")[
            "fragments"
        ][0]
        assert fragment["y"] == 0.12
        run = database.fetchone(
            "SELECT * FROM runs WHERE kind='single_question_recognition'"
        )
        assert run is not None
        assert run["status"] == "failed"
        assert run["error_code"] == "MODEL_TIMEOUT"


def test_single_question_rerecognition_rejects_invalid_page_before_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        _save_image(runtime / "page.jpg", (999, 1400))
        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        recognition = _SingleQuestionRecognition()
        app.state.recognition_service = recognition

        response = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/rerecognize",
            json={
                "expectedRevision": 0,
                "regions": [_fragment("q-one", 0.12, source="teacher")],
            },
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "SINGLE_QUESTION_PAGE_SIZE_MISMATCH"
        assert error["details"]["savedFrameSet"]["revision"] == 1
        assert error["details"]["questionContentUnchanged"] is True
        assert recognition.calls == []
        assert database.fetchone("SELECT stem FROM questions WHERE id='q-one'") == {
            "stem": "题干 任意甲"
        }
        run = database.fetchone(
            "SELECT status,error_code FROM runs WHERE kind='single_question_recognition'"
        )
        assert run == {
            "status": "failed",
            "error_code": "SINGLE_QUESTION_PAGE_SIZE_MISMATCH",
        }


def test_single_question_rerecognition_rejects_late_result_after_frame_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        _save_image(runtime / "page.jpg", (1000, 1400))
        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        app.state.recognition_service = _SupersedingSingleQuestionRecognition(database)

        response = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/rerecognize",
            json={
                "expectedRevision": 0,
                "regions": [_fragment("q-one", 0.12, source="teacher")],
            },
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SINGLE_QUESTION_FRAME_SUPERSEDED"
        assert database.fetchone("SELECT stem FROM questions WHERE id='q-one'") == {
            "stem": "题干 任意甲"
        }
        run = database.fetchone(
            "SELECT status,error_code FROM runs WHERE kind='single_question_recognition'"
        )
        assert run == {
            "status": "failed",
            "error_code": "SINGLE_QUESTION_FRAME_SUPERSEDED",
        }


@pytest.mark.parametrize(
    ("active_kind", "expected_code"),
    [
        ("student", "STUDENT_PROCESSING_ACTIVE"),
        ("grading", "GRADING_PROCESSING_ACTIVE"),
    ],
)
def test_single_question_rerecognition_blocks_active_processing(
    tmp_path: Path,
    monkeypatch,
    active_kind: str,
    expected_code: str,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO student_submissions(id,task_id,status,created_at,updated_at)
                   VALUES('active-submission','task',?,?,?)""",
                ("recognizing" if active_kind == "student" else "ready", timestamp, timestamp),
            )
            if active_kind == "grading":
                connection.execute(
                    """INSERT INTO grading_runs(
                         id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
                       ) VALUES('active-grading','active-submission','task','grading','grading',
                         'active-input',?,?)""",
                    (timestamp, timestamp),
                )
        recognition = _SingleQuestionRecognition()
        app.state.recognition_service = recognition

        response = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/rerecognize",
            json={
                "expectedRevision": 0,
                "regions": [_fragment("q-one", 0.12, source="teacher")],
            },
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == expected_code
        assert recognition.calls == []
        current = client.get("/api/tasks/task/question-frame-sets/current").json()["data"][
            "frameSet"
        ]
        assert current["revision"] == 0


def test_student_upload_auto_confirms_safe_fill_configs_without_manual_blank_save(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed(database)
        app.state.student_pipeline = _NoopStudentPipeline()
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """UPDATE questions
                   SET stem='____',confirmation_status='confirmed' WHERE task_id='task'"""
            )
            for question_id, answer in (("q-one", "alpha"), ("q-two", "beta")):
                connection.execute(
                    """INSERT INTO matches(
                         id,task_id,question_id,method,status,teacher_answer,updated_at
                       ) VALUES(?, 'task',?,'manual','confirmed',?,?)""",
                    (f"match-{question_id}", question_id, answer, timestamp),
                )

        created = client.post(
            "/api/tasks/task/question-frame-sets",
            json={
                "source": "model",
                "candidates": [
                    {"questionId": "q-one", "fragments": [_fragment("q-one", 0.05)]},
                    {"questionId": "q-two", "fragments": [_fragment("q-two", 0.55)]},
                ],
            },
        ).json()["data"]
        first = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-one/confirm",
            json={"expectedRevision": 0},
        ).json()["data"]
        second = client.post(
            f"/api/question-frame-sets/{created['id']}/questions/q-two/confirm",
            json={"expectedRevision": first["revision"]},
        ).json()["data"]
        frozen = client.post(
            f"/api/question-frame-sets/{created['id']}/confirm",
            json={"expectedRevision": second["revision"]},
        )
        assert frozen.status_code == 200

        gate = client.get("/api/tasks/task/student-processing-gate").json()["data"]
        assert gate["ready"] is True
        review_gate = client.get("/api/tasks/task/review").json()["data"][
            "studentUploadGate"
        ]
        assert review_gate["ready"] is True
        assert review_gate["blankConfigIssues"] == []
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM question_blank_config_versions"
        ) == {"count": 0}

        uploaded = client.post(
            "/api/tasks/task/student-submissions",
            data={"studentName": "auto-config"},
            files={"file": ("student.png", _png_bytes(), "image/png")},
        )

        assert uploaded.status_code == 202
        assert database.fetchone(
            """SELECT COUNT(*) AS count FROM question_blank_config_versions
               WHERE status='auto_confirmed' AND source='model'"""
        ) == {"count": 2}
        audit_rows = database.fetchall(
            """SELECT payload_json FROM audit_events
               WHERE event_type='fill_blank_config_auto_confirmed'"""
        )
        assert len(audit_rows) == 2
        assert all(json_loads(row["payload_json"], {})["modelCalls"] == 0 for row in audit_rows)
