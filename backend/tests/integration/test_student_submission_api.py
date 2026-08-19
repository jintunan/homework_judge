from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from homework_judge.alignment import Homography, Point
from homework_judge.db.database import Database, json_dumps, json_loads, now_iso
from homework_judge.main import app
from homework_judge.question_frames.service import QuestionFrameService


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, "PNG")
    return output.getvalue()


class NoopStudentPipeline:
    async def run(self, _submission_id: str) -> None:
        return

    async def resume_current_recognition(self, _submission_id: str) -> None:
        return


@dataclass(frozen=True, slots=True)
class AlignmentCorrectionSeed:
    submission_id: str
    student_page_id: str
    initial_processing_revision_id: str
    frame_set_id: str
    frame_region_ids: dict[str, str]


def manual_control_points() -> list[dict[str, dict[str, float]]]:
    return [
        {"template": {"x": 0.0, "y": 0.0}, "student": {"x": 10.0, "y": 20.0}},
        {"template": {"x": 320.0, "y": 0.0}, "student": {"x": 310.0, "y": 20.0}},
        {
            "template": {"x": 320.0, "y": 240.0},
            "student": {"x": 310.0, "y": 220.0},
        },
        {"template": {"x": 0.0, "y": 240.0}, "student": {"x": 10.0, "y": 220.0}},
        {
            "template": {"x": 160.0, "y": 120.0},
            "student": {"x": 160.0, "y": 120.0},
        },
    ]


def identity_control_points() -> list[dict[str, dict[str, float]]]:
    return [
        {"template": {"x": 0.0, "y": 0.0}, "student": {"x": 0.0, "y": 0.0}},
        {"template": {"x": 320.0, "y": 0.0}, "student": {"x": 320.0, "y": 0.0}},
        {
            "template": {"x": 320.0, "y": 240.0},
            "student": {"x": 320.0, "y": 240.0},
        },
        {"template": {"x": 0.0, "y": 240.0}, "student": {"x": 0.0, "y": 240.0}},
    ]


def _draw_template(path: Path, *, alternate: bool = False) -> None:
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    if alternate:
        draw.ellipse((30, 20, 130, 120), outline="black", width=4)
        draw.line((170, 30, 290, 210), fill="black", width=5)
        draw.text((180, 80), "PAGE TWO", fill="black")
    else:
        draw.rectangle((20, 20, 300, 220), outline="black", width=3)
        draw.line((35, 80, 285, 80), fill="black", width=3)
        draw.text((45, 120), "PAGE ONE", fill="black")
    image.save(path, "PNG")


def _seed_alignment_correction_case(
    database: Database,
    runtime: Path,
) -> AlignmentCorrectionSeed:
    runtime.mkdir(parents=True, exist_ok=True)
    _draw_template(runtime / "template-page-1.png")
    _draw_template(runtime / "template-page-2.png", alternate=True)
    _draw_template(runtime / "student-page.png")
    timestamp = now_iso()
    run_id = uuid.uuid4().hex
    question_specs = (
        ("question-page-1", 0, "1", 1),
        ("question-page-2-a", 1, "2", 2),
        ("question-page-2-b", 2, "3", 2),
    )
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('alignment-task','Alignment template','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('alignment-exam','alignment-task','exam','exam.pdf','exam.pdf',
                 'application/pdf','.pdf',1,'alignment-exam-sha',2,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.executemany(
            """INSERT INTO pages(
                 id,document_id,page_number,image_path,width,height,sha256
               ) VALUES(?,?,?,?,320,240,?)""",
            (
                (
                    "template-page-1",
                    "alignment-exam",
                    1,
                    "template-page-1.png",
                    "template-page-1-sha",
                ),
                (
                    "template-page-2",
                    "alignment-exam",
                    2,
                    "template-page-2.png",
                    "template-page-2-sha",
                ),
            ),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES(?,'alignment-task','exam_recognition','succeeded','done',?)""",
            (run_id, timestamp),
        )
        for question_id, sort_order, number, page_number in question_specs:
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                     options_json,question_type,source_pages_json,confidence,issues_json,
                     answer_regions_json,confirmation_status
                   ) VALUES(?,?,?,?,?,?,?,'[]','single_choice',?,1,'[]','[]','confirmed')""",
                (
                    question_id,
                    "alignment-task",
                    run_id,
                    sort_order,
                    number,
                    number,
                    f"Question {number}",
                    json_dumps([page_number]),
                ),
            )

    frame_candidates = [
        {
            "questionId": "question-page-1",
            "fragments": [
                {
                    "regionKey": "question-page-1:whole",
                    "templatePageId": "template-page-1",
                    "pageNumber": 1,
                    "x": 0.1,
                    "y": 0.1,
                    "width": 0.2,
                    "height": 0.2,
                    "sortOrder": 0,
                    "source": "teacher",
                    "confidence": 1.0,
                    "issues": [],
                }
            ],
        },
        {
            "questionId": "question-page-2-a",
            "fragments": [
                {
                    "regionKey": "question-page-2-a:whole",
                    "templatePageId": "template-page-2",
                    "pageNumber": 2,
                    "x": 0.1,
                    "y": 0.1,
                    "width": 0.3,
                    "height": 0.2,
                    "sortOrder": 0,
                    "source": "teacher",
                    "confidence": 1.0,
                    "issues": [],
                }
            ],
        },
        {
            "questionId": "question-page-2-b",
            "fragments": [
                {
                    "regionKey": "question-page-2-b:whole",
                    "templatePageId": "template-page-2",
                    "pageNumber": 2,
                    "x": 0.5,
                    "y": 0.5,
                    "width": 0.3,
                    "height": 0.25,
                    "sortOrder": 0,
                    "source": "teacher",
                    "confidence": 1.0,
                    "issues": [],
                }
            ],
        },
    ]
    frame_service = QuestionFrameService(database)
    draft = frame_service.create_draft(
        "alignment-task",
        frame_candidates,
        source="teacher",
        actor="test",
    )
    current = draft
    for question_id, _sort_order, _number, _page_number in question_specs:
        current = frame_service.confirm_item(
            str(draft["id"]),
            question_id,
            expected_revision=int(current["revision"]),
            actor="test",
        )
    confirmed = frame_service.confirm_set(
        str(draft["id"]),
        expected_revision=int(current["revision"]),
        actor="test",
    )
    frame_region_ids = {
        str(item["questionId"]): str(item["fragments"][0]["id"])
        for item in confirmed["items"]
    }

    submission_id = "alignment-submission"
    student_page_id = "alignment-student-page"
    processing_revision_id = "alignment-processing-1"
    alignment_revision_id = "alignment-revision-1"
    template_region = {
        "coordinateSpace": "template_page_normalized",
        "x": 0.1,
        "y": 0.1,
        "width": 0.2,
        "height": 0.2,
    }
    old_polygon = [
        {"x": 32.0, "y": 24.0},
        {"x": 96.0, "y": 24.0},
        {"x": 96.0, "y": 72.0},
        {"x": 32.0, "y": 72.0},
    ]
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_identifier,student_name,original_name,mime_type,size_bytes,
                 sha256,relative_path,page_count,status,question_region_status,created_at,updated_at
               ) VALUES(?,?,?,?,'student.png','image/png',1,'student-sha','student.png',1,
                 'ready','ready',?,?)""",
            (
                submission_id,
                "alignment-task",
                "student-1",
                "Student One",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 template_page_id,alignment_transform_json,alignment_quality,alignment_method,
                 alignment_status,created_at,updated_at
               ) VALUES(?,?,1,'student-page.png',320,240,'student-page-sha','template-page-1',
                 '[[1,0,0],[0,1,0],[0,0,1]]',1,'seed','aligned',?,?)""",
            (student_page_id, submission_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,started_at,finished_at,created_at,updated_at
               ) VALUES(?,?,1,?,'ready','seed-input',1,'system','[]',?,?,?,?)""",
            (
                processing_revision_id,
                submission_id,
                confirmed["id"],
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE student_submissions SET current_processing_revision_id=? WHERE id=?",
            (processing_revision_id, submission_id),
        )
        connection.execute(
            """INSERT INTO student_page_alignment_revisions(
                 id,processing_revision_id,student_page_id,revision_number,template_page_id,
                 transform_json,quality,method,status,control_points_json,metrics_json,source,
                 is_current,issues_json,created_by,created_at,updated_at
               ) VALUES(?,?,?,1,'template-page-1','[[1,0,0],[0,1,0],[0,0,1]]',1,'seed',
                 'aligned','[]','{}','model',1,'[]','system',?,?)""",
            (
                alignment_revision_id,
                processing_revision_id,
                student_page_id,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_question_regions(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 frame_region_id,alignment_revision_id,sort_order,template_page_id,
                 student_page_id,template_region_json,student_polygon_json,student_bbox_json,
                 status,issues_json,created_at,updated_at
               ) VALUES('old-question-page-1',?, 'question-page-1',?,?,?,?,0,
                 'template-page-1',?,?,?,?, 'ready','[]',?,?)""",
            (
                submission_id,
                processing_revision_id,
                confirmed["id"],
                frame_region_ids["question-page-1"],
                alignment_revision_id,
                student_page_id,
                json_dumps(template_region),
                json_dumps(old_polygon),
                json_dumps({"x": 32.0, "y": 24.0, "width": 64.0, "height": 48.0}),
                timestamp,
                timestamp,
            ),
        )
    return AlignmentCorrectionSeed(
        submission_id=submission_id,
        student_page_id=student_page_id,
        initial_processing_revision_id=processing_revision_id,
        frame_set_id=str(confirmed["id"]),
        frame_region_ids=frame_region_ids,
    )


def _seed_template(database: Database) -> None:
    timestamp = now_iso()
    run_id = uuid.uuid4().hex
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Template','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 1,'sha',1,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam',1,'template.jpg',320,240,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES(?,'task','exam_recognition','succeeded','done',?)""",
            (run_id, timestamp),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 options_json,question_type,source_pages_json,confidence,issues_json,
                 answer_regions_json,confirmation_status
               ) VALUES('question','task',?,0,'1','1','Question','[]','single_choice','[1]',1,
                 '[]',?,'confirmed')""",
            (
                run_id,
                json_dumps(
                    [
                        {
                            "page_number": 1,
                            "x": 0.1,
                            "y": 0.1,
                            "width": 0.2,
                            "height": 0.1,
                        }
                    ]
                ),
            ),
        )
    frame_service = QuestionFrameService(database)
    draft = frame_service.create_draft(
        "task",
        [
            {
                "questionId": "question",
                "fragments": [
                    {
                        "regionKey": "question:frame:1",
                        "templatePageId": "template-page",
                        "pageNumber": 1,
                        "x": 0.05,
                        "y": 0.05,
                        "width": 0.9,
                        "height": 0.8,
                        "sortOrder": 0,
                        "source": "teacher",
                        "confidence": 1,
                        "issues": [],
                    }
                ],
            }
        ],
        source="teacher",
        actor="test",
    )
    item_confirmed = frame_service.confirm_item(
        str(draft["id"]),
        "question",
        expected_revision=int(draft["revision"]),
        actor="test",
    )
    frame_service.confirm_set(
        str(draft["id"]),
        expected_revision=int(item_confirmed["revision"]),
        actor="test",
    )


def test_upload_list_detail_original_page_and_template_rebuild_guard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed_template(database)
        app.state.student_pipeline = NoopStudentPipeline()

        invalid_region = client.patch(
            "/api/questions/question/answer-regions",
            json=[{"pageNumber": 2, "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1}],
        )
        assert invalid_region.status_code == 422
        assert invalid_region.json()["error"]["code"] == "ANSWER_REGION_PAGE_INVALID"

        uploaded = client.post(
            "/api/tasks/task/student-submissions",
            data={"studentIdentifier": "20260001", "studentName": "Student One"},
            files={"file": ("student.png", png_bytes(), "image/png")},
        )
        assert uploaded.status_code == 202
        submission_id = uploaded.json()["data"]["submissionId"]

        listed = client.get("/api/tasks/task/student-submissions")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == submission_id

        detail = client.get(f"/api/student-submissions/{submission_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["submission"]["student_name"] == "Student One"

        page_path = runtime / "student-page.jpg"
        Image.new("RGB", (320, 240), "white").save(page_path, "JPEG")
        timestamp = now_iso()
        database.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES('student-page',?,1,'student-page.jpg',320,240,'sha','pending',?,?)""",
            (submission_id, timestamp, timestamp),
        )
        original_page = client.get("/api/student-pages/student-page")
        assert original_page.status_code == 200
        assert original_page.headers["content-type"].startswith("image/jpeg")

        rebuild = client.post("/api/tasks/task/process")
        assert rebuild.status_code == 409
        assert rebuild.json()["error"]["code"] == "TEMPLATE_HAS_STUDENT_SUBMISSIONS"

        database.execute(
            "UPDATE student_submissions SET status='ready' WHERE id=?",
            (submission_id,),
        )
        retry_ready = client.post(f"/api/student-submissions/{submission_id}/process")
        assert retry_ready.status_code == 409
        assert retry_ready.json()["error"]["code"] == "STUDENT_SUBMISSION_ALREADY_READY"

        deleted = client.delete("/api/tasks/task")
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted"] is True
        assert not (runtime / "uploads" / "task").exists()
        assert client.get("/api/tasks/task").status_code == 404


def test_delete_one_student_submission_removes_only_its_data_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        _seed_template(database)
        app.state.student_pipeline = NoopStudentPipeline()

        submission_ids: list[str] = []
        for name in ("Delete Me", "Keep Me"):
            response = client.post(
                "/api/tasks/task/student-submissions",
                data={"studentIdentifier": "", "studentName": name},
                files={"file": (f"{name}.png", png_bytes(), "image/png")},
            )
            assert response.status_code == 202
            submission_ids.append(str(response.json()["data"]["submissionId"]))
        deleted_id, kept_id = submission_ids
        timestamp = now_iso()
        page_dir = runtime / "pages" / "task" / f"student-{deleted_id}"
        page_dir.mkdir(parents=True)
        Image.new("RGB", (320, 240), "white").save(page_dir / "page-1.jpg", "JPEG")
        run_id = "delete-grading-run"
        artifact_dir = runtime / "artifacts" / run_id / "revision-0001"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "report.pdf").write_bytes(b"pdf")
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO student_pages(
                     id,submission_id,page_number,original_image_path,width,height,sha256,
                     alignment_status,created_at,updated_at
                   ) VALUES('delete-page',?,1,?,320,240,'page-sha','pending',?,?)""",
                (
                    deleted_id,
                    f"pages/task/student-{deleted_id}/page-1.jpg",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO grading_runs(
                     id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
                   ) VALUES(?,?,?,'completed','completed','delete-hash',?,?)""",
                (run_id, deleted_id, "task", timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO grading_artifacts(
                     id,grading_run_id,artifact_type,result_revision,status,relative_path,
                     created_at,updated_at
                   ) VALUES('delete-artifact',?,'error_report',1,'current',?,?,?)""",
                (
                    run_id,
                    f"artifacts/{run_id}/revision-0001/report.pdf",
                    timestamp,
                    timestamp,
                ),
            )

        response = client.delete(f"/api/student-submissions/{deleted_id}")

        assert response.status_code == 200
        assert response.json()["data"] == {
            "submissionId": deleted_id,
            "taskId": "task",
            "deleted": True,
            "cancelledJobs": 0,
            "cleanupPending": False,
        }
        assert client.get(f"/api/student-submissions/{deleted_id}").status_code == 404
        assert client.get(f"/api/student-submissions/{kept_id}").status_code == 200
        assert database.fetchone("SELECT id FROM student_pages WHERE id='delete-page'") is None
        assert database.fetchone("SELECT id FROM grading_runs WHERE id=?", (run_id,)) is None
        assert not (runtime / "uploads" / "task" / "students" / deleted_id).exists()
        assert not page_dir.exists()
        assert not (runtime / "artifacts" / run_id).exists()
        assert (runtime / "uploads" / "task" / "students" / kept_id).exists()
        audit = database.fetchone(
            """SELECT event_type FROM audit_events
               WHERE task_id='task' AND event_type='student_submission_deleted'"""
        )
        assert audit == {"event_type": "student_submission_deleted"}


def test_delete_missing_student_submission_returns_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        response = client.delete("/api/student-submissions/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STUDENT_SUBMISSION_NOT_FOUND"


def test_page_alignment_override_switches_template_and_remaps_every_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)

        corrected = client.put(
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment",
            json={
                "expectedAlignmentRevision": 1,
                "templatePageId": "template-page-2",
                "controlPoints": manual_control_points(),
            },
        )

        assert corrected.status_code == 200
        processing_rows = database.fetchall(
            """SELECT id,revision_number,is_current,source
               FROM student_processing_revisions WHERE submission_id=?
               ORDER BY revision_number""",
            (seed.submission_id,),
        )
        assert [row["revision_number"] for row in processing_rows] == [1, 2]
        assert [row["is_current"] for row in processing_rows] == [0, 1]
        assert processing_rows[1]["source"] == "teacher"
        current_processing_id = str(processing_rows[1]["id"])

        alignment_rows = database.fetchall(
            """SELECT * FROM student_page_alignment_revisions
               WHERE student_page_id=? ORDER BY revision_number""",
            (seed.student_page_id,),
        )
        assert [row["revision_number"] for row in alignment_rows] == [1, 2]
        latest_alignment = alignment_rows[1]
        assert latest_alignment["processing_revision_id"] == current_processing_id
        assert latest_alignment["template_page_id"] == "template-page-2"
        assert latest_alignment["source"] == "teacher"
        assert len(json_loads(latest_alignment["control_points_json"], [])) == 5
        transform = Homography.from_rows(json_loads(latest_alignment["transform_json"], []))
        assert transform.map_point(Point(0.0, 0.0)).as_tuple() == pytest.approx((10.0, 20.0))
        assert transform.map_point(Point(320.0, 240.0)).as_tuple() == pytest.approx(
            (310.0, 220.0)
        )

        current_mappings = database.fetchall(
            """SELECT question_id,frame_region_id,alignment_revision_id,
                      student_polygon_json,student_bbox_json
               FROM student_question_regions WHERE processing_revision_id=?
               ORDER BY question_id""",
            (current_processing_id,),
        )
        assert [row["question_id"] for row in current_mappings] == [
            "question-page-2-a",
            "question-page-2-b",
        ]
        assert {row["frame_region_id"] for row in current_mappings} == {
            seed.frame_region_ids["question-page-2-a"],
            seed.frame_region_ids["question-page-2-b"],
        }
        assert {row["alignment_revision_id"] for row in current_mappings} == {
            latest_alignment["id"]
        }
        first_polygon = json_loads(current_mappings[0]["student_polygon_json"], [])
        assert first_polygon[0] == pytest.approx({"x": 40.0, "y": 40.0})
        assert first_polygon[2] == pytest.approx({"x": 130.0, "y": 80.0})

        old_mapping = database.fetchone(
            """SELECT question_id,frame_region_id,alignment_revision_id
               FROM student_question_regions WHERE processing_revision_id=?""",
            (seed.initial_processing_revision_id,),
        )
        assert old_mapping == {
            "question_id": "question-page-1",
            "frame_region_id": seed.frame_region_ids["question-page-1"],
            "alignment_revision_id": "alignment-revision-1",
        }
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM student_question_regions WHERE submission_id=?",
            (seed.submission_id,),
        ) == {"count": 3}
        stable_page = database.fetchone(
            "SELECT template_page_id,alignment_method FROM student_pages WHERE id=?",
            (seed.student_page_id,),
        )
        assert stable_page == {"template_page_id": "template-page-1", "alignment_method": "seed"}

        detail = client.get(f"/api/student-submissions/{seed.submission_id}")
        assert detail.status_code == 200
        detail_data = detail.json()["data"]
        assert detail_data["currentProcessingRevisionId"] == current_processing_id
        assert [item["revisionNumber"] for item in detail_data["processingRevisions"]] == [
            2,
            1,
        ]
        current_page = detail_data["pages"][0]
        assert current_page["templatePageId"] == "template-page-2"
        assert current_page["templatePageNumber"] == 2
        assert current_page["alignment"]["revisionNumber"] == 2
        assert current_page["alignment"]["source"] == "teacher"
        assert len(current_page["alignment"]["controlPoints"]) == 5
        for actual_row, expected_row in zip(
            current_page["alignment"]["transform"],
            transform.inverse.as_rows(),
            strict=True,
        ):
            assert actual_row == pytest.approx(expected_row)
        assert {item["frameRegionId"] for item in detail_data["questionRegions"]} == {
            seed.frame_region_ids["question-page-2-a"],
            seed.frame_region_ids["question-page-2-b"],
        }
        assert {item["processingRevisionId"] for item in detail_data["questionRegions"]} == {
            current_processing_id
        }
        assert detail_data["isHistoricalView"] is False

        historical = client.get(
            f"/api/student-submissions/{seed.submission_id}"
            f"?processingRevisionId={seed.initial_processing_revision_id}"
        )
        assert historical.status_code == 200
        historical_data = historical.json()["data"]
        assert historical_data["isHistoricalView"] is True
        assert historical_data["processingRevision"]["id"] == seed.initial_processing_revision_id
        assert historical_data["currentProcessingRevisionId"] == current_processing_id
        assert {item["questionId"] for item in historical_data["questionRegions"]} == {
            "question-page-1"
        }
        history = {item["id"]: item for item in historical_data["processingRevisions"]}
        assert history[current_processing_id]["isHistorical"] is False
        assert history[seed.initial_processing_revision_id]["isHistorical"] is True


def test_page_alignment_override_requires_at_least_four_control_points(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)

        rejected = client.put(
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment",
            json={
                "expectedAlignmentRevision": 1,
                "templatePageId": "template-page-2",
                "controlPoints": manual_control_points()[:3],
            },
        )

        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM student_processing_revisions WHERE submission_id=?",
            (seed.submission_id,),
        ) == {"count": 1}


def test_page_alignment_override_preserves_unaffected_pages_in_new_processing_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)
        _draw_template(runtime / "student-page-2.png", alternate=True)
        timestamp = now_iso()
        second_student_page_id = "alignment-student-page-2"
        second_alignment_id = "alignment-revision-page-2"
        page_two_regions = (
            (
                "old-question-page-2-a",
                "question-page-2-a",
                {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.2},
                [
                    {"x": 32.0, "y": 24.0},
                    {"x": 128.0, "y": 24.0},
                    {"x": 128.0, "y": 72.0},
                    {"x": 32.0, "y": 72.0},
                ],
            ),
            (
                "old-question-page-2-b",
                "question-page-2-b",
                {"x": 0.5, "y": 0.5, "width": 0.3, "height": 0.25},
                [
                    {"x": 160.0, "y": 120.0},
                    {"x": 256.0, "y": 120.0},
                    {"x": 256.0, "y": 180.0},
                    {"x": 160.0, "y": 180.0},
                ],
            ),
        )
        with database.transaction() as connection:
            connection.execute(
                "UPDATE student_submissions SET page_count=2 WHERE id=?",
                (seed.submission_id,),
            )
            connection.execute(
                """INSERT INTO student_pages(
                     id,submission_id,page_number,original_image_path,width,height,sha256,
                     template_page_id,alignment_transform_json,alignment_quality,
                     alignment_method,alignment_status,created_at,updated_at
                   ) VALUES(?,?,2,'student-page-2.png',320,240,'student-page-2-sha',
                     'template-page-2','[[1,0,0],[0,1,0],[0,0,1]]',1,'seed','aligned',?,?)""",
                (second_student_page_id, seed.submission_id, timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO student_page_alignment_revisions(
                     id,processing_revision_id,student_page_id,revision_number,template_page_id,
                     transform_json,quality,method,status,control_points_json,metrics_json,source,
                     is_current,issues_json,created_by,created_at,updated_at
                   ) VALUES(?,?,?,1,'template-page-2','[[1,0,0],[0,1,0],[0,0,1]]',1,'seed',
                     'aligned','[]','{}','model',1,'[]','system',?,?)""",
                (
                    second_alignment_id,
                    seed.initial_processing_revision_id,
                    second_student_page_id,
                    timestamp,
                    timestamp,
                ),
            )
            for region_id, question_id, template_box, polygon in page_two_regions:
                student_box = {
                    "x": polygon[0]["x"],
                    "y": polygon[0]["y"],
                    "width": polygon[2]["x"] - polygon[0]["x"],
                    "height": polygon[2]["y"] - polygon[0]["y"],
                }
                connection.execute(
                    """INSERT INTO student_question_regions(
                         id,submission_id,question_id,processing_revision_id,frame_set_id,
                         frame_region_id,alignment_revision_id,sort_order,template_page_id,
                         student_page_id,template_region_json,student_polygon_json,
                         student_bbox_json,status,issues_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,0,'template-page-2',?,?,?,?,'ready','[]',?,?)""",
                    (
                        region_id,
                        seed.submission_id,
                        question_id,
                        seed.initial_processing_revision_id,
                        seed.frame_set_id,
                        seed.frame_region_ids[question_id],
                        second_alignment_id,
                        second_student_page_id,
                        json_dumps(
                            {
                                "coordinateSpace": "template_page_normalized",
                                **template_box,
                            }
                        ),
                        json_dumps(polygon),
                        json_dumps(student_box),
                        timestamp,
                        timestamp,
                    ),
                )

        corrected = client.put(
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment",
            json={
                "expectedAlignmentRevision": 1,
                "templatePageId": "template-page-1",
                "controlPoints": identity_control_points(),
            },
        )

        assert corrected.status_code == 200
        current = database.fetchone(
            """SELECT id FROM student_processing_revisions
               WHERE submission_id=? AND is_current=1""",
            (seed.submission_id,),
        )
        assert current is not None
        current_processing_id = str(current["id"])
        current_alignments = database.fetchall(
            """SELECT student_page_id,template_page_id
               FROM student_page_alignment_revisions
               WHERE processing_revision_id=? ORDER BY student_page_id""",
            (current_processing_id,),
        )
        assert current_alignments == [
            {
                "student_page_id": seed.student_page_id,
                "template_page_id": "template-page-1",
            },
            {
                "student_page_id": second_student_page_id,
                "template_page_id": "template-page-2",
            },
        ]
        current_mappings = database.fetchall(
            """SELECT question_id,student_page_id FROM student_question_regions
               WHERE processing_revision_id=? ORDER BY question_id""",
            (current_processing_id,),
        )
        assert current_mappings == [
            {"question_id": "question-page-1", "student_page_id": seed.student_page_id},
            {"question_id": "question-page-2-a", "student_page_id": second_student_page_id},
            {"question_id": "question-page-2-b", "student_page_id": second_student_page_id},
        ]


def test_page_alignment_override_rejects_stale_expected_revision_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)

        conflict = client.put(
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment",
            json={
                "expectedAlignmentRevision": 0,
                "templatePageId": "template-page-2",
                "controlPoints": manual_control_points(),
            },
        )

        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "ALIGNMENT_REVISION_CONFLICT"
        details = conflict.json()["error"]["details"]
        assert details["expectedAlignmentRevision"] == 0
        assert details["currentAlignmentRevision"] == 1
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM student_processing_revisions WHERE submission_id=?",
            (seed.submission_id,),
        ) == {"count": 1}
        assert database.fetchone(
            "SELECT current_processing_revision_id FROM student_submissions WHERE id=?",
            (seed.submission_id,),
        ) == {"current_processing_revision_id": seed.initial_processing_revision_id}


def test_page_alignment_override_rejects_template_page_outside_submission_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)

        rejected = client.put(
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment",
            json={
                "expectedAlignmentRevision": 1,
                "templatePageId": "not-this-task-template-page",
                "controlPoints": manual_control_points(),
            },
        )

        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "ALIGNMENT_TEMPLATE_PAGE_INVALID"
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM student_page_alignment_revisions "
            "WHERE student_page_id=?",
            (seed.student_page_id,),
        ) == {"count": 1}


def test_clearing_alignment_override_creates_new_revision_and_preserves_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(runtime))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed = _seed_alignment_correction_case(database, runtime)
        endpoint = (
            f"/api/student-submissions/{seed.submission_id}/pages/"
            f"{seed.student_page_id}/alignment"
        )
        corrected = client.put(
            endpoint,
            json={
                "expectedAlignmentRevision": 1,
                "templatePageId": "template-page-2",
                "controlPoints": manual_control_points(),
            },
        )
        assert corrected.status_code == 200

        cleared = client.put(
            endpoint,
            json={"expectedAlignmentRevision": 2, "clearOverride": True},
        )

        assert cleared.status_code == 200
        processing_rows = database.fetchall(
            """SELECT id,revision_number,is_current FROM student_processing_revisions
               WHERE submission_id=? ORDER BY revision_number""",
            (seed.submission_id,),
        )
        assert [row["revision_number"] for row in processing_rows] == [1, 2, 3]
        assert [row["is_current"] for row in processing_rows] == [0, 0, 1]
        latest_processing_id = str(processing_rows[-1]["id"])
        alignment_rows = database.fetchall(
            """SELECT * FROM student_page_alignment_revisions
               WHERE student_page_id=? ORDER BY revision_number""",
            (seed.student_page_id,),
        )
        assert [row["revision_number"] for row in alignment_rows] == [1, 2, 3]
        latest_alignment = alignment_rows[-1]
        assert latest_alignment["processing_revision_id"] == latest_processing_id
        assert latest_alignment["template_page_id"] == "template-page-1"
        assert json_loads(latest_alignment["control_points_json"], None) == []

        mapping_history = database.fetchall(
            """SELECT p.revision_number,r.question_id,r.frame_region_id
               FROM student_question_regions r
               JOIN student_processing_revisions p ON p.id=r.processing_revision_id
               WHERE r.submission_id=? ORDER BY p.revision_number,r.question_id""",
            (seed.submission_id,),
        )
        assert [
            (row["revision_number"], row["question_id"])
            for row in mapping_history
        ] == [
            (1, "question-page-1"),
            (2, "question-page-2-a"),
            (2, "question-page-2-b"),
            (3, "question-page-1"),
        ]
        assert mapping_history[0]["frame_region_id"] == seed.frame_region_ids["question-page-1"]
        assert mapping_history[-1]["frame_region_id"] == seed.frame_region_ids["question-page-1"]
        assert database.fetchone(
            "SELECT template_page_id,alignment_method FROM student_pages WHERE id=?",
            (seed.student_page_id,),
        ) == {"template_page_id": "template-page-1", "alignment_method": "seed"}
