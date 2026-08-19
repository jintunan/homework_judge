from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.config import Settings
from homework_judge.db.database import Database, json_dumps, now_iso
from homework_judge.errors import ModelError
from homework_judge.jobs.pipeline import Pipeline
from homework_judge.jobs.question_region_pipeline import QuestionRegionPipeline
from homework_judge.recognition import normalizer
from homework_judge.recognition.calculation_localization import CalculationSearchFragment
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.normalizer import (
    merge_question_regions_by_page,
    normalize_question,
)
from homework_judge.recognition.parser import (
    parse_calculation_localization,
    parse_calculation_recognition,
    parse_student_response,
)
from homework_judge.recognition.prompts import (
    CALCULATION_LOCALIZATION_PROMPT_VERSION,
    CALCULATION_LOCALIZATION_SYSTEM_PROMPT,
    CALCULATION_RECOGNITION_PROMPT_VERSION,
    CALCULATION_RECOGNITION_SYSTEM_PROMPT,
    calculation_localization_prompt,
    calculation_recognition_prompt,
)
from homework_judge.recognition.service import RecognitionService


class FakeClient:
    def __init__(self) -> None:
        self.user_content: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        assert "template" in system_prompt
        self.user_content = user_content
        return ModelResponse(
            content=(
                '{"response":{"transcription":"$F=ma$","isBlank":false,'
                '"confidence":0.91,"issues":[]}}'
            ),
            raw={"id": "response"},
            usage={"promptTokens": 1, "completionTokens": 2, "totalTokens": 3},
        )


def test_normalizes_answer_regions_from_thousand_grid_and_relative_box() -> None:
    item = normalize_question(
        {
            "number": "2",
            "stem": "Question",
            "type": "fill_blank",
            "sourcePages": [1, 2],
            "answerRegions": [
                {"pageNumber": 1, "bbox": [100, 200, 600, 350]},
                {"pageNumber": 2, "x": 0.2, "y": 0.3, "width": 0.4, "height": 0.1},
            ],
        },
        0,
        {1, 2},
    )
    assert item["answer_regions"] == [
        {"page_number": 1, "x": 0.1, "y": 0.2, "width": 0.5, "height": 0.15},
        {"page_number": 2, "x": 0.2, "y": 0.3, "width": 0.4, "height": 0.1},
    ]


def test_normalizes_full_question_regions_and_rejects_invalid_geometry() -> None:
    item = normalize_question(
        {
            "number": "3",
            "stem": "Question",
            "type": "single_choice",
            "sourcePages": [1],
            "questionRegions": [
                {
                    "pageNumber": 1,
                    "bbox": [80, 120, 920, 760],
                    "confidence": 0.93,
                    "issues": ["diagram_near_edge"],
                },
                {"pageNumber": 1, "x": -0.1, "y": 0.2, "width": 0.4, "height": 0.2},
                {"pageNumber": 2, "x": 0.1, "y": 0.2, "width": 0.4, "height": 0.2},
            ],
        },
        0,
        {1},
    )
    assert item["question_regions"] == [
        {
            "page_number": 1,
            "x": 0.08,
            "y": 0.12,
            "width": 0.84,
            "height": 0.64,
            "confidence": 0.93,
            "issues": ["diagram_near_edge"],
        }
    ]


def test_answer_regions_never_expand_the_complete_question_frame() -> None:
    item = normalize_question(
        {
            "number": "changed",
            "stem": "完整题干和选项",
            "type": "fill_blank",
            "sourcePages": [1],
            "questionRegions": [{"pageNumber": 1, "bbox": [50, 100, 950, 600]}],
            "answerRegions": [{"pageNumber": 1, "bbox": [100, 800, 900, 900]}],
        },
        0,
        {1},
    )

    assert item["question_regions"] == [
        {
            "page_number": 1,
            "x": 0.05,
            "y": 0.1,
            "width": 0.9,
            "height": 0.5,
            "confidence": 0.8,
            "issues": [],
        }
    ]
    assert item["answer_regions"][0]["y"] == 0.8


def test_preserves_independent_question_fragments_without_a_bounding_hull() -> None:
    assert merge_question_regions_by_page(
        [
            {"page_number": 1, "x": 0.1, "y": 0.1, "width": 0.7, "height": 0.2},
            {"page_number": 1, "x": 0.2, "y": 0.5, "width": 0.6, "height": 0.4},
            {"page_number": 2, "x": 0.1, "y": 0.1, "width": 0.8, "height": 0.5},
        ],
        padding=0,
    ) == [
        {
            "page_number": 1,
            "x": 0.1,
            "y": 0.1,
            "width": 0.7,
            "height": 0.2,
            "confidence": 0.8,
            "issues": [],
        },
        {
            "page_number": 1,
            "x": 0.2,
            "y": 0.5,
            "width": 0.6,
            "height": 0.4,
            "confidence": 0.8,
            "issues": [],
        },
        {
            "page_number": 2,
            "x": 0.1,
            "y": 0.1,
            "width": 0.8,
            "height": 0.5,
            "confidence": 0.8,
            "issues": [],
        },
    ]


def test_normalizer_does_not_export_question_frame_expansion_heuristics() -> None:
    assert not hasattr(normalizer, "complete_question_regions")
    assert not hasattr(normalizer, "extend_calculation_work_area")


def test_parses_wrapped_student_response() -> None:
    assert parse_student_response(
        '```json\n{"response":{"transcription":"A","isBlank":false}}\n```'
    ) == {"transcription": "A", "isBlank": False}


async def test_transcribes_paired_regions_without_persisted_crops() -> None:
    client = FakeClient()
    settings = cast(Settings, SimpleNamespace())
    service = RecognitionService(settings, cast(DashScopeClient, client))
    result, raw, usage = await service.recognize_student_response(
        {"number": "2", "type": "calculation", "stem": "Find F"},
        [
            {
                "page_number": 1,
                "template_image": b"template jpeg",
                "student_image": b"student jpeg",
            }
        ],
    )
    assert result == {
        "transcription": "$F=ma$",
        "is_blank": False,
        "confidence": 0.91,
        "issues": [],
        "segments": [
            {
                "region_index": 1,
                "transcription": "$F=ma$",
                "is_blank": False,
                "confidence": 0.91,
                "issues": [],
            }
        ],
    }
    assert raw == {"id": "response"}
    assert usage["totalTokens"] == 3
    assert [item["type"] for item in client.user_content] == [
        "text",
        "text",
        "image_url",
        "text",
        "image_url",
    ]


class TemplateRegionClient:
    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        assert "0..1000" in system_prompt
        assert user_content[-1]["type"] == "image_url"
        return ModelResponse(
            content=(
                '{"regions":[{"questionId":"question-12","questionNumber":"12",'
                '"answerRegions":['
                '{"bbox":[100,700,900,850]}]}]}'
            ),
            raw={"id": "template-regions"},
            usage={"totalTokens": 4},
        )


async def test_locates_missing_regions_on_existing_blank_template(tmp_path: Path) -> None:
    page = tmp_path / "template.jpg"
    page.write_bytes(b"jpeg bytes")
    settings = cast(Settings, SimpleNamespace(data_dir=tmp_path))
    service = RecognitionService(
        settings,
        cast(DashScopeClient, TemplateRegionClient()),
    )
    regions, raw, usage = await service.recognize_template_regions(
        {"page_number": 3, "image_path": "template.jpg"},
        [
            {
                "id": "question-12",
                "number": "12",
                "type": "fill_blank",
                "stem": "Question",
            }
        ],
    )
    assert regions == {
        "question-12": [
            {
                "page_number": 3,
                "x": 0.1,
                "y": 0.7,
                "width": 0.8,
                "height": 0.15,
            }
        ]
    }
    assert raw == {"id": "template-regions"}
    assert usage == {"totalTokens": 4}


class QuestionFrameDraftRecognition:
    def __init__(self) -> None:
        self.calls = 0

    async def recognize_question_regions(
        self,
        page: dict[str, Any],
        questions: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]:
        self.calls += 1
        assert page["id"] == "page"
        assert [question["id"] for question in questions] == ["question"]
        return (
            {
                "question": [
                    {
                        "page_number": 1,
                        "x": 0.05,
                        "y": 0.1,
                        "width": 0.9,
                        "height": 0.7,
                        "confidence": 0.94,
                        "issues": [],
                    }
                ]
            },
            {"id": "question-frame-draft"},
            {"totalTokens": 4},
        )


async def test_question_region_pipeline_only_creates_a_template_draft(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "question-region-draft.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','frame draft','review_pending',?,?)""",
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
               VALUES('page','exam',1,'page.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('run','task','exam_recognition','succeeded','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 options_json,question_type,source_pages_json,confidence,issues_json,
                 confirmation_status,answer_regions_json,question_regions_json
               ) VALUES('question','task','run',0,'1','1','Question','[]','fill_blank','[1]',
                 1,'[]','confirmed','[]','[]')"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,original_name,mime_type,size_bytes,sha256,relative_path,status,
                 question_region_status,created_at,updated_at
               ) VALUES('submission','task','student.png','image/png',1,'student-sha',
                 'student.png','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 template_page_id,alignment_transform_json,alignment_quality,
                 alignment_method,alignment_status,created_at,updated_at
               ) VALUES('student-page','submission',1,'student.png',1000,1400,'student-page-sha',
                 'page',?,1,'test','aligned',?,?)""",
            (json_dumps([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_question_regions(
                 id,submission_id,question_id,sort_order,template_page_id,student_page_id,
                 template_region_json,student_polygon_json,student_bbox_json,status,
                 issues_json,created_at,updated_at
               ) VALUES('sentinel','submission','question',0,'page','student-page',
                 ?,?,?,'ready','[]',?,?)""",
            (
                json_dumps(
                    {"page_number": 1, "x": 0.2, "y": 0.2, "width": 0.2, "height": 0.2}
                ),
                json_dumps(
                    [
                        {"x": 200, "y": 280},
                        {"x": 400, "y": 280},
                        {"x": 400, "y": 560},
                        {"x": 200, "y": 560},
                    ]
                ),
                json_dumps({"x": 200, "y": 280, "width": 200, "height": 280}),
                timestamp,
                timestamp,
            ),
        )

    recognition = QuestionFrameDraftRecognition()
    settings = cast(Settings, SimpleNamespace(dashscope_model="vision-test"))
    pipeline = QuestionRegionPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    )
    await pipeline.run("task")
    await pipeline.run("task")

    frame = database.fetchone(
        """SELECT f.status,f.source,i.status AS item_status,r.template_page_id,
                  r.page_number,r.x,r.y,r.width,r.height
           FROM tasks t JOIN question_frame_sets f ON f.id=t.current_question_frame_set_id
           JOIN question_frame_items i ON i.frame_set_id=f.id
           JOIN question_frame_regions r ON r.frame_item_id=i.id
           WHERE t.id='task'"""
    )
    assert frame == {
        "status": "draft",
        "source": "model",
        "item_status": "pending",
        "template_page_id": "page",
        "page_number": 1,
        "x": 0.05,
        "y": 0.1,
        "width": 0.9,
        "height": 0.7,
    }
    assert recognition.calls == 1
    assert database.fetchall(
        "SELECT id FROM student_question_regions WHERE submission_id='submission'"
    ) == [{"id": "sentinel"}]
    submission = database.fetchone(
        "SELECT question_region_status FROM student_submissions WHERE id='submission'"
    )
    assert submission == {"question_region_status": "ready"}


def test_pipeline_saves_model_frames_as_pending_draft(tmp_path: Path) -> None:
    database = Database(tmp_path / "pipeline-frames.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','frame draft','exam_recognizing',?,?)""",
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
               VALUES('page','exam',1,'page.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('run','task','exam_recognition','running','exam_recognition',?)""",
            (timestamp,),
        )
    settings = cast(Settings, SimpleNamespace(dashscope_model="vision-test"))
    pipeline = Pipeline(settings, database, cast(RecognitionService, None))

    pipeline._save_questions(
        "task",
        "run",
        [
            {
                "sort_order": 0,
                "detected_number": "任意题号",
                "normalized_number": "任意题号",
                "stem": "任意题面",
                "options": [],
                "question_type": "fill_blank",
                "score": 4,
                "source_pages": [1],
                "confidence": 0.96,
                "issues": [],
                "answer_regions": [
                    {"page_number": 1, "x": 0.2, "y": 0.4, "width": 0.2, "height": 0.05}
                ],
                "question_regions": [
                    {
                        "page_number": 1,
                        "x": 0.05,
                        "y": 0.1,
                        "width": 0.9,
                        "height": 0.7,
                        "confidence": 0.94,
                        "issues": [],
                    }
                ],
            }
        ],
    )

    current = database.fetchone(
        """SELECT f.status,f.revision,i.status AS item_status,r.y,r.height
           FROM tasks t JOIN question_frame_sets f ON f.id=t.current_question_frame_set_id
           JOIN question_frame_items i ON i.frame_set_id=f.id
           JOIN question_frame_regions r ON r.frame_item_id=i.id
           WHERE t.id='task'"""
    )
    assert current == {
        "status": "draft",
        "revision": 0,
        "item_status": "pending",
        "y": 0.1,
        "height": 0.7,
    }


def _calculation_fragment(
    key: str,
    page_number: int,
    sort_order: int,
) -> CalculationSearchFragment:
    return CalculationSearchFragment(
        fragment_key=key,
        template_page_id=f"template-{page_number}",
        student_page_id=f"student-{page_number}",
        alignment_revision_id=f"alignment-{page_number}",
        page_number=page_number,
        student_page_number=page_number,
        x=0.0,
        y=0.2 if page_number == 1 else 0.0,
        width=1.0,
        height=0.8 if page_number == 1 else 1.0,
        sort_order=sort_order,
    ).with_images(
        template_image=f"template-{page_number}".encode(),
        student_image=f"student-{page_number}".encode(),
    )


def test_calculation_localization_parser_accepts_only_exact_unwrapped_json() -> None:
    exact = (
        '{"windows":[{"fragmentKey":"p1","status":"blank",'
        '"confidence":0.9,"issues":[],"regions":[]}]}'
    )
    assert parse_calculation_localization(exact).issues == []

    for invalid in (
        f"```json\n{exact}\n```",
        f"response: {exact}",
        f"{exact} trailing",
        '{"windows":[],"isBlank":true}',
        '{"windows":[],"windows":[]}',
        (
            '{"windows":[{"fragmentKey":"p1","status":"blank",'
            '"confidence":NaN,"issues":[],"regions":[]}]}'
        ),
    ):
        parsed = parse_calculation_localization(invalid)
        assert parsed.nodes == []
        assert parsed.issues


def test_calculation_localization_parser_reports_non_object_windows() -> None:
    parsed = parse_calculation_localization('{"windows":[null,3,{"fragmentKey":"p1"}]}')

    assert parsed.nodes == [{"fragmentKey": "p1"}]
    assert [issue["code"] for issue in parsed.issues] == [
        "calculation_localization_window_not_object",
        "calculation_localization_window_not_object",
    ]


def test_calculation_recognition_parser_is_strict_and_keeps_combined_regions() -> None:
    exact = (
        '{"windows":[{"fragmentKey":"p1","status":"located",'
        '"confidence":0.9,"issues":[],"regions":[{"bbox":[1,2,3,4],'
        '"confidence":0.9,"issues":[],"transcription":"x=1",'
        '"transcriptionConfidence":0.8,"transcriptionIssues":[]}]}]}'
    )

    parsed = parse_calculation_recognition(exact)
    assert parsed.issues == []
    assert parsed.nodes[0]["regions"][0]["transcription"] == "x=1"
    assert parse_calculation_recognition(f"```json\n{exact}\n```").issues


def test_calculation_prompt_exposes_only_safe_question_surface() -> None:
    prompt = calculation_localization_prompt(
        {
            "id": "q1",
            "number": "7",
            "type": "calculation",
            "stem": "show the work",
            "standardAnswer": "SECRET_STANDARD_ANSWER",
            "score": 20,
            "rubric": "SECRET_RUBRIC",
            "synonyms": ["SECRET_SYNONYM"],
        },
        [_calculation_fragment("p1", 1, 0).snapshot()],
        frame_set_id="frames-v1",
        batch_index=1,
        attempt_id="attempt-1",
    )

    assert "show the work" in prompt
    assert "frames-v1" in prompt
    assert "p1" in prompt
    assert "SECRET_STANDARD_ANSWER" not in prompt
    assert "SECRET_RUBRIC" not in prompt
    assert "SECRET_SYNONYM" not in prompt
    assert CALCULATION_LOCALIZATION_PROMPT_VERSION == "calculation-answer-localization-v1"
    assert '"windows"' in CALCULATION_LOCALIZATION_SYSTEM_PROMPT
    assert "located|blank|uncertain" in CALCULATION_LOCALIZATION_SYSTEM_PROMPT


def test_combined_calculation_prompt_exposes_only_safe_question_surface() -> None:
    prompt = calculation_recognition_prompt(
        {
            "id": "q1",
            "number": "7",
            "type": "calculation",
            "stem": "show the work",
            "standardAnswer": "SECRET_STANDARD_ANSWER",
            "score": 20,
            "rubric": "SECRET_RUBRIC",
        },
        [_calculation_fragment("p1", 1, 0).snapshot()],
        frame_set_id="frames-v1",
        batch_index=1,
        attempt_id="attempt-1",
    )

    assert "show the work" in prompt
    assert "SECRET_STANDARD_ANSWER" not in prompt
    assert "SECRET_RUBRIC" not in prompt
    assert CALCULATION_RECOGNITION_PROMPT_VERSION == "calculation-localize-transcribe-v1"
    assert "transcriptionConfidence" in CALCULATION_RECOGNITION_SYSTEM_PROMPT
    assert "Do not solve" in CALCULATION_RECOGNITION_SYSTEM_PROMPT


class CalculationLocatorClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.system_prompt = ""
        self.user_content: list[dict[str, Any]] = []
        self.calls = 0

    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        self.calls += 1
        self.system_prompt = system_prompt
        self.user_content = user_content
        return ModelResponse(
            content=self.content,
            raw={"id": "locator-response"},
            usage={"promptTokens": 3, "completionTokens": 4, "totalTokens": 7},
        )


async def test_locates_one_bounded_batch_from_ordered_template_student_pairs() -> None:
    fragments = [
        _calculation_fragment("p1", 1, 0),
        _calculation_fragment("p2", 2, 1),
    ]
    client = CalculationLocatorClient(
        '{"windows":['
        '{"fragmentKey":"p1","status":"located","confidence":0.92,"issues":[],'
        '"regions":[{"bbox":[100,200,500,600],"confidence":0.93,"issues":[]}]},'
        '{"fragmentKey":"p2","status":"blank","confidence":0.96,"issues":[],'
        '"regions":[]}]}'
    )
    settings = cast(
        Settings,
        SimpleNamespace(answer_pages_per_batch=2, dashscope_model="vision-locator"),
    )
    service = RecognitionService(settings, cast(DashScopeClient, client))

    result, raw, usage = await service.locate_calculation_regions(
        {
            "id": "q1",
            "number": "7",
            "type": "calculation",
            "stem": "Find x",
            "standardAnswer": "DO_NOT_SEND",
            "score": 20,
        },
        fragments,
        frame_set_id="frames-v1",
        batch_index=1,
        attempt_id="attempt-1",
    )

    assert result.status == "located"
    assert result.evidence_complete is True
    assert result.model_id == "vision-locator"
    assert result.prompt_version == CALCULATION_LOCALIZATION_PROMPT_VERSION
    assert raw == {"id": "locator-response"}
    assert usage["totalTokens"] == 7
    assert client.system_prompt == CALCULATION_LOCALIZATION_SYSTEM_PROMPT
    assert len([item for item in client.user_content if item["type"] == "image_url"]) == 4
    text_parts = [
        str(item["text"])
        for item in client.user_content
        if item["type"] == "text"
    ]
    assert "p1" in text_parts[1]
    assert "p1" in text_parts[2]
    assert "p2" in text_parts[3]
    assert "p2" in text_parts[4]
    assert "DO_NOT_SEND" not in text_parts[0]


async def test_recognizes_one_calculation_batch_in_one_model_call() -> None:
    client = CalculationLocatorClient(
        '{"windows":[{"fragmentKey":"p1","status":"located",'
        '"confidence":0.92,"issues":[],"regions":[{'
        '"bbox":[100,200,500,600],"confidence":0.93,"issues":[], '
        '"transcription":"x = 42","transcriptionConfidence":0.94,'
        '"transcriptionIssues":[]}]}]}'
    )
    settings = cast(
        Settings,
        SimpleNamespace(answer_pages_per_batch=2, dashscope_model="vision-locator"),
    )
    service = RecognitionService(settings, cast(DashScopeClient, client))

    result, raw, usage = await service.recognize_calculation_batch(
        {
            "id": "q1",
            "number": "7",
            "type": "calculation",
            "stem": "Find x",
            "standardAnswer": "DO_NOT_SEND",
        },
        [_calculation_fragment("p1", 1, 0)],
        frame_set_id="frames-v1",
        batch_index=1,
        attempt_id="attempt-1",
    )

    assert client.calls == 1
    assert client.system_prompt == CALCULATION_RECOGNITION_SYSTEM_PROMPT
    assert result.localization_contract_valid is True
    assert result.transcription_contract_valid is True
    assert result.transcriptions[0].transcription == "x = 42"
    assert raw == {"id": "locator-response"}
    assert usage["totalTokens"] == 7


async def test_invalid_locator_json_becomes_incomplete_batch_without_fabricated_blank() -> None:
    client = CalculationLocatorClient("```json\n{\"windows\": []}\n```")
    settings = cast(
        Settings,
        SimpleNamespace(answer_pages_per_batch=2, dashscope_model="vision-locator"),
    )
    service = RecognitionService(settings, cast(DashScopeClient, client))

    result, _, _ = await service.locate_calculation_regions(
        {"id": "q1", "type": "calculation", "stem": "Find x"},
        [_calculation_fragment("p1", 1, 0)],
        frame_set_id="frames-v1",
        batch_index=1,
        attempt_id="attempt-1",
    )

    assert result.status == "needs_review"
    assert result.evidence_complete is False
    assert result.reliable_blank is False
    assert result.regions == ()
    assert "calculation_localization_invalid_json" in {issue.code for issue in result.issues}


class FailingCalculationLocatorClient:
    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        raise ModelError("MODEL_TIMEOUT", "timeout")


async def test_locator_model_errors_propagate_to_batch_orchestrator() -> None:
    settings = cast(
        Settings,
        SimpleNamespace(answer_pages_per_batch=2, dashscope_model="vision-locator"),
    )
    service = RecognitionService(
        settings,
        cast(DashScopeClient, FailingCalculationLocatorClient()),
    )

    with pytest.raises(ModelError, match="timeout"):
        await service.locate_calculation_regions(
            {"id": "q1", "type": "calculation", "stem": "Find x"},
            [_calculation_fragment("p1", 1, 0)],
            frame_set_id="frames-v1",
            batch_index=1,
            attempt_id="attempt-1",
        )
