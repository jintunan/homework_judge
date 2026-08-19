from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from homework_judge.config import Settings
from homework_judge.db.database import Database, json_dumps, now_iso
from homework_judge.errors import AppError
from homework_judge.grading.blank_config_confirmation import save_blank_config_version
from homework_judge.recognition.client import DashScopeClient
from homework_judge.review.answer_grading_drafts import AnswerGradingDraftService


def _service(tmp_path: Path) -> tuple[Database, AnswerGradingDraftService]:
    database = Database(tmp_path / "answer-grading-draft.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','测试','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','task','exam_recognition','succeeded','completed',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 options_json,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES('q1','task','source',0,'11','11','第一处______，第二处______。',
                 '[]','fill_blank',5,'[1]',1,'[]','confirmed')"""
        )
        connection.execute(
            """INSERT INTO answer_entries(
                 id,task_id,source_run_id,sort_order,number_hint,normalized_number,stem_hint,
                 answer,explanation,source_pages_json,confidence,issues_json
               ) VALUES('a1','task','source',0,'11','11','','电荷转移；CD','旧解析','[1]',1,'[]')"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,answer_entry_id,method,number_score,stem_score,
                 order_score,total_score,reasons_json,status,updated_at
               ) VALUES('m1','task','q1','a1','manual',1,1,1,1,'[]','confirmed',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,relative_path,created_at
               ) VALUES('exam','task','exam','exam.png','exam.png','image/png','.png',1,
                 'sha','exam.png',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('page','exam',1,'page.png',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,revision,source,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES('frame','task',1,'confirmed',1,'teacher','frame-hash','teacher',
                 ?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id='frame' WHERE id='task'"
        )
        connection.execute(
            """INSERT INTO question_frame_items(
                 id,frame_set_id,question_id,status,revision,issues_json,confirmed_at,
                 confirmed_by,created_at,updated_at
               ) VALUES('item','frame','q1','confirmed',1,'[]',?,'teacher',?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_frame_regions(
                 id,frame_item_id,region_key,template_page_id,page_number,x,y,width,height,
                 sort_order,source,confidence,issues_json,created_at,updated_at
               ) VALUES('region','item','Q11','page',1,.05,.05,.9,.8,0,'teacher',1,
                 '[]',?,?)""",
            (timestamp, timestamp),
        )
        save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame",
            expected_config_version=0,
            max_score="5.00",
            blanks=[
                {
                    "blankKey": f"B{index + 1}",
                    "sortOrder": index,
                    "maxScore": "2.50",
                    "answerKind": "text",
                    "standardAnswers": [answer],
                    "synonyms": [],
                    "anchor": None,
                }
                for index, answer in enumerate(("电荷转移", "CD"))
            ],
            actor="teacher",
            source="teacher",
            confirm=True,
        )
        connection.execute(
            "UPDATE questions SET confirmation_status='confirmed' WHERE id='q1'"
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,question_region_status,created_at,updated_at
               ) VALUES('submission','task','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,created_at,updated_at
               ) VALUES('processing','submission',1,'frame','ready','input',1,'system',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='processing'
               WHERE id='submission'"""
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
               ) VALUES('grading','submission','task','completed','completed','input',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_artifacts(
                 id,grading_run_id,artifact_type,result_revision,status,created_at,updated_at
               ) VALUES('artifact','grading','annotation',0,'current',?,?)""",
            (timestamp, timestamp),
        )
    settings = cast(
        Settings,
        SimpleNamespace(teacher_name="测试教师", dashscope_model="fake-model"),
    )
    service = AnswerGradingDraftService(
        settings,
        database,
        cast(DashScopeClient, object()),
    )
    return database, service


def _seed_preview(database: Database, service: AnswerGradingDraftService) -> str:
    state = service._state("q1")
    run_id = "draft-run"
    draft = {
        "questionType": "fill_blank",
        "standardAnswer": "电荷转移；守；CD",
        "explanation": "新解析",
        "maxScore": "5.00",
        "answerOptions": [],
        "rubricPoints": [],
        "warnings": ["请核对三空"],
        "blanks": [
            {
                "blankKey": f"B{index + 1}",
                "sortOrder": index,
                "maxScore": score,
                "answerKind": "text",
                "standardAnswers": [answer],
                "synonyms": [],
                "anchor": None,
            }
            for index, (score, answer) in enumerate(
                zip(("1.67", "1.67", "1.66"), ("电荷转移", "守", "CD"), strict=True)
            )
        ],
    }
    timestamp = now_iso()
    database.execute(
        """INSERT INTO runs(
             id,task_id,kind,status,stage,progress_current,progress_total,
             request_summary_json,raw_response_json,created_at,finished_at
           ) VALUES(?,'task','answer_grading_regeneration','succeeded','preview_ready',1,1,
             ?,?,?,?)""",
        (
            run_id,
            json_dumps({"questionId": "q1", "capture": state["capture"]}),
            json_dumps({"provider": {}, "current": state["current"], "draft": draft}),
            timestamp,
            timestamp,
        ),
    )
    return run_id


def test_apply_fill_draft_keeps_history_and_invalidates_current_results(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    run_id = _seed_preview(database, service)

    result = service.apply(run_id, actor="测试教师")

    assert result["studentResultsInvalidated"] is True
    assert database.fetchone(
        "SELECT teacher_answer,teacher_explanation,method,status FROM matches WHERE id='m1'"
    ) == {
        "teacher_answer": "电荷转移；守；CD",
        "teacher_explanation": "新解析",
        "method": "manual",
        "status": "confirmed",
    }
    assert database.fetchone(
        "SELECT config_version FROM question_grading_configs WHERE question_id='q1'"
    ) == {"config_version": 2}
    assert database.fetchall(
        """SELECT blank_key,max_score,standard_answers_json
           FROM question_blank_definitions WHERE question_id='q1' ORDER BY sort_order"""
    ) == [
        {"blank_key": "B1", "max_score": "1.67", "standard_answers_json": '["电荷转移"]'},
        {"blank_key": "B2", "max_score": "1.67", "standard_answers_json": '["守"]'},
        {"blank_key": "B3", "max_score": "1.66", "standard_answers_json": '["CD"]'},
    ]
    assert database.fetchone(
        "SELECT confirmation_status FROM questions WHERE id='q1'"
    ) == {"confirmation_status": "confirmed"}
    assert database.fetchone("SELECT status FROM tasks WHERE id='task'") == {
        "status": "review_pending"
    }
    assert database.fetchone(
        "SELECT is_current FROM student_processing_revisions WHERE id='processing'"
    ) == {"is_current": 0}
    assert database.fetchone(
        """SELECT status,current_processing_revision_id
           FROM student_submissions WHERE id='submission'"""
    ) == {"status": "uploaded", "current_processing_revision_id": None}
    assert database.fetchone("SELECT is_stale FROM grading_runs WHERE id='grading'") == {
        "is_stale": 1
    }
    assert database.fetchone("SELECT status FROM grading_artifacts WHERE id='artifact'") == {
        "status": "stale"
    }
    assert database.fetchone(
        "SELECT COUNT(*) AS value FROM question_blank_config_versions WHERE question_id='q1'"
    ) == {"value": 2}
    with pytest.raises(AppError) as repeated:
        service.apply(run_id, actor="测试教师")
    assert repeated.value.code == "ANSWER_GRADING_DRAFT_ALREADY_APPLIED"


def test_apply_rejects_preview_after_answer_was_edited(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    run_id = _seed_preview(database, service)
    database.execute(
        "UPDATE matches SET teacher_answer='教师后续修改',updated_at=? WHERE id='m1'",
        (now_iso(),),
    )

    with pytest.raises(AppError) as conflict:
        service.apply(run_id, actor="测试教师")

    assert conflict.value.code == "ANSWER_GRADING_DRAFT_SUPERSEDED"
    assert database.fetchone("SELECT teacher_answer FROM matches WHERE id='m1'") == {
        "teacher_answer": "教师后续修改"
    }
    assert database.fetchone("SELECT stage FROM runs WHERE id='draft-run'") == {
        "stage": "preview_ready"
    }
