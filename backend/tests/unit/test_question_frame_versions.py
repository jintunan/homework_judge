from __future__ import annotations

from pathlib import Path

import pytest

from homework_judge.db.database import Database, now_iso
from homework_judge.errors import AppError
from homework_judge.question_frames.service import QuestionFrameService
from homework_judge.review.invalidation import ensure_frame_set_is_current


def _database(path: Path) -> Database:
    database = Database(path)
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','题框版本测试','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,
                 size_bytes,sha256,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf',
                 '.pdf',1,'sha','exam.pdf',?)""",
            (timestamp,),
        )
        for page_number in (1, 2):
            connection.execute(
                """INSERT INTO pages(
                     id,document_id,page_number,image_path,width,height,sha256
                   ) VALUES(?,?,?,?,1000,1400,?)""",
                (
                    f"page-{page_number}",
                    "exam",
                    page_number,
                    f"page-{page_number}.jpg",
                    f"page-sha-{page_number}",
                ),
            )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('run','task','exam_recognition','succeeded','done',?)""",
            (timestamp,),
        )
        for sort_order, question_id, number in ((0, "q-alpha", "甲"), (1, "q-beta", "乙")):
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                     stem,options_json,question_type,score,source_pages_json,confidence,
                     issues_json,confirmation_status
                   ) VALUES(?,?,?,?,?,?,?,'[]','fill_blank',4,'[1]',1,'[]','pending')""",
                (question_id, "task", "run", sort_order, number, number, f"题干 {number}"),
            )
    return database


def _region(
    question_id: str,
    *,
    y: float,
    page_number: int = 1,
    source: str = "model",
) -> dict[str, object]:
    return {
        "regionKey": f"{question_id}:fragment:1",
        "templatePageId": f"page-{page_number}",
        "pageNumber": page_number,
        "x": 0.08,
        "y": y,
        "width": 0.84,
        "height": 0.25,
        "sortOrder": 0,
        "source": source,
        "confidence": 0.91,
        "issues": [],
    }


def _create_initial(service: QuestionFrameService) -> dict[str, object]:
    return service.create_draft(
        "task",
        [
            {"questionId": "q-alpha", "fragments": [_region("q-alpha", y=0.05)]},
            {"questionId": "q-beta", "fragments": [_region("q-beta", y=0.55)]},
        ],
        source="model",
        actor="model:test",
    )


def _confirm_all(service: QuestionFrameService, value: dict[str, object]) -> dict[str, object]:
    frame_set_id = str(value["id"])
    revision = int(value["revision"])
    for question_id in ("q-alpha", "q-beta"):
        value = service.confirm_item(
            frame_set_id,
            question_id,
            expected_revision=revision,
            actor="teacher",
        )
        revision = int(value["revision"])
    return service.confirm_set(
        frame_set_id,
        expected_revision=revision,
        actor="teacher",
    )


def test_initial_draft_confirms_each_question_then_freezes(tmp_path: Path) -> None:
    database = _database(tmp_path / "frames.sqlite")
    service = QuestionFrameService(database)

    draft = _create_initial(service)
    assert draft["versionNumber"] == 1
    assert draft["status"] == "draft"
    assert draft["revision"] == 0
    assert {item["status"] for item in draft["items"]} == {"pending"}
    assert service.processing_gate("task")["ready"] is False

    confirmed = _confirm_all(service, draft)

    assert confirmed["status"] == "confirmed"
    assert confirmed["revision"] == 3
    assert {item["status"] for item in confirmed["items"]} == {"confirmed"}
    gate = service.processing_gate("task")
    assert gate == {
        "ready": True,
        "frameSetId": confirmed["id"],
        "frameSetVersion": 1,
        "missingQuestionIds": [],
        "unconfirmedQuestionIds": [],
        "issues": [],
    }


def test_editing_frozen_set_forks_and_only_reopens_changed_question(tmp_path: Path) -> None:
    database = _database(tmp_path / "fork.sqlite")
    service = QuestionFrameService(database)
    frozen = _confirm_all(service, _create_initial(service))

    fork = service.update_item(
        str(frozen["id"]),
        "q-alpha",
        [_region("q-alpha", y=0.08, source="teacher")],
        expected_revision=int(frozen["revision"]),
        actor="teacher",
    )

    assert fork["id"] != frozen["id"]
    assert fork["versionNumber"] == 2
    assert fork["baseFrameSetId"] == frozen["id"]
    assert fork["status"] == "draft"
    statuses = {item["questionId"]: item["status"] for item in fork["items"]}
    assert statuses == {"q-alpha": "pending", "q-beta": "confirmed"}
    assert service.get_frame_set(str(frozen["id"]))["status"] == "superseded"
    assert service.processing_gate("task")["unconfirmedQuestionIds"] == ["q-alpha"]

    old_region = database.fetchone(
        """SELECT r.y FROM question_frame_regions r
           JOIN question_frame_items i ON i.id=r.frame_item_id
           WHERE i.frame_set_id=? AND i.question_id='q-alpha'""",
        (frozen["id"],),
    )
    assert old_region == {"y": 0.05}


def test_expected_revision_rejects_second_concurrent_save(tmp_path: Path) -> None:
    service = QuestionFrameService(_database(tmp_path / "cas.sqlite"))
    draft = _create_initial(service)
    expected = int(draft["revision"])

    saved = service.update_item(
        str(draft["id"]),
        "q-alpha",
        [_region("q-alpha", y=0.09, source="teacher")],
        expected_revision=expected,
        actor="teacher-one",
    )
    assert saved["revision"] == expected + 1

    with pytest.raises(AppError) as captured:
        service.update_item(
            str(draft["id"]),
            "q-alpha",
            [_region("q-alpha", y=0.12, source="teacher")],
            expected_revision=expected,
            actor="teacher-two",
        )
    assert captured.value.status_code == 409
    assert captured.value.code == "FRAME_SET_REVISION_CONFLICT"
    assert captured.value.details["currentRevision"] == expected + 1


def test_overlap_can_be_saved_as_intermediate_state_but_cannot_be_confirmed(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "geometry.sqlite")
    service = QuestionFrameService(database)
    draft = _create_initial(service)

    saved = service.update_item(
        str(draft["id"]),
        "q-beta",
        [_region("q-beta", y=0.1, source="teacher")],
        expected_revision=int(draft["revision"]),
        actor="teacher",
    )
    beta = next(item for item in saved["items"] if item["questionId"] == "q-beta")
    assert beta["fragments"][0]["y"] == 0.1
    assert saved["revision"] == 1
    geometry = [
        issue
        for issue in service.processing_gate("task")["issues"]
        if issue["code"] == "frame_cross_question_overlap"
    ]
    assert len(geometry) == 1
    assert geometry[0]["layer"] == "question_frame"
    assert geometry[0]["relatedQuestionId"] == "q-beta"

    with pytest.raises(AppError) as captured:
        service.confirm_item(
            str(draft["id"]),
            "q-beta",
            expected_revision=1,
            actor="teacher",
        )
    assert captured.value.code == "QUESTION_FRAME_INVALID"
    assert captured.value.details["issues"][0]["code"] == "frame_cross_question_overlap"
    assert "第 甲 题与第 乙 题" in captured.value.message


def test_unrelated_existing_overlap_does_not_block_saving_another_question(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "unrelated-overlap.sqlite")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,options_json,question_type,score,source_pages_json,confidence,
                 issues_json,confirmation_status
               ) VALUES('q-gamma','task','run',2,'丙','丙','题干 丙','[]','fill_blank',
                 4,'[1]',1,'[]','pending')"""
        )
    service = QuestionFrameService(database)
    draft = service.create_draft(
        "task",
        [
            {"questionId": "q-alpha", "fragments": [_region("q-alpha", y=0.05)]},
            {"questionId": "q-beta", "fragments": [_region("q-beta", y=0.45)]},
            {"questionId": "q-gamma", "fragments": [_region("q-gamma", y=0.5)]},
        ],
        source="teacher",
        actor="teacher",
    )

    saved = service.update_item(
        str(draft["id"]),
        "q-alpha",
        [_region("q-alpha", y=0.08, source="teacher")],
        expected_revision=int(draft["revision"]),
        actor="teacher",
    )

    assert saved["revision"] == 1
    confirmed = service.confirm_item(
        str(draft["id"]),
        "q-alpha",
        expected_revision=1,
        actor="teacher",
    )
    assert next(
        item for item in confirmed["items"] if item["questionId"] == "q-alpha"
    )["status"] == "confirmed"


def test_duplicate_questions_are_excluded_from_gate(tmp_path: Path) -> None:
    database = _database(tmp_path / "duplicate.sqlite")
    service = QuestionFrameService(database)
    draft = _create_initial(service)
    database.execute("UPDATE questions SET is_duplicate=1 WHERE id='q-beta'")

    only_active = service.confirm_item(
        str(draft["id"]),
        "q-alpha",
        expected_revision=int(draft["revision"]),
        actor="teacher",
    )
    frozen = service.confirm_set(
        str(draft["id"]),
        expected_revision=int(only_active["revision"]),
        actor="teacher",
    )

    assert frozen["status"] == "confirmed"
    assert service.processing_gate("task")["ready"] is True


def test_fork_marks_old_blank_config_and_processing_revision_stale(tmp_path: Path) -> None:
    database = _database(tmp_path / "invalidation.sqlite")
    service = QuestionFrameService(database)
    frozen = _confirm_all(service, _create_initial(service))
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('q-alpha','fill_blank','4.00',1,?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_blank_config_versions(
                 id,question_id,version_number,frame_set_id,status,source,signals_json,
                 blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES('blank-v1','q-alpha',1,?,'teacher_confirmed','teacher','{}','[]',
                 '[]','blank-hash','teacher',?,?,?,'teacher')""",
            (frozen["id"], timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE question_grading_configs SET current_blank_config_version_id='blank-v1'
               WHERE question_id='q-alpha'"""
        )
        connection.execute(
            """INSERT INTO student_submissions(id,task_id,status,created_at,updated_at)
               VALUES('submission','task','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,created_at,updated_at
               ) VALUES('processing-v1','submission',1,?,'ready','input',1,'system','[]',?,?)""",
            (frozen["id"], timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='processing-v1'
               WHERE id='submission'"""
        )

    service.update_item(
        str(frozen["id"]),
        "q-alpha",
        [_region("q-alpha", y=0.09, source="teacher")],
        expected_revision=int(frozen["revision"]),
        actor="teacher",
    )

    assert database.fetchone(
        "SELECT status FROM question_blank_config_versions WHERE id='blank-v1'"
    ) == {"status": "stale"}
    assert database.fetchone(
        """SELECT config_version,current_blank_config_version_id
           FROM question_grading_configs WHERE question_id='q-alpha'"""
    ) == {"config_version": 2, "current_blank_config_version_id": None}
    assert database.fetchone(
        "SELECT status,is_current FROM student_processing_revisions WHERE id='processing-v1'"
    ) == {"status": "ready", "is_current": 0}
    assert database.fetchone(
        """SELECT status,current_processing_revision_id,error_code
           FROM student_submissions WHERE id='submission'"""
    ) == {
        "status": "uploaded",
        "current_processing_revision_id": None,
        "error_code": "QUESTION_FRAME_SET_CHANGED",
    }
    with database.transaction() as connection, pytest.raises(AppError) as captured:
        ensure_frame_set_is_current(connection, "task", str(frozen["id"]))
    assert captured.value.code == "FRAME_SET_SUPERSEDED"
