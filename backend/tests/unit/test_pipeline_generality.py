from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from homework_judge.alignment import AlignmentQuality, Homography, PageSize
from homework_judge.alignment.models import FramePageAlignment, MappedFrameRegion
from homework_judge.alignment.regions import map_confirmed_frame_set
from homework_judge.grading.contracts import (
    BoundingBox,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionType,
)
from homework_judge.grading.fill import grade_fill_question
from homework_judge.recognition.client import ModelResponse
from homework_judge.recognition.parser import parse_keyed_fill_response

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "generic_blank_layout_cases.json"
)
FIXTURE_DIR = FIXTURE.parent
Q8_ORACLE = FIXTURE_DIR / "q8_full_frame_oracle.json"
Q11_ORACLE = FIXTURE_DIR / "q11_three_blanks_oracle.json"


class KeyedJudgeStub:
    settings = SimpleNamespace(dashscope_model="generic-keyed-judge")

    def __init__(self, expected_keys: list[str], evidence_region_id: str) -> None:
        self.expected_keys = expected_keys
        self.evidence_region_id = evidence_region_id
        self.calls: list[str] = []

    async def chat(self, **kwargs: object) -> ModelResponse:
        user_content = kwargs["user_content"]
        assert isinstance(user_content, list)
        payload = json.loads(user_content[0]["text"])
        blank_key = str(payload["blankKey"])
        assert blank_key == self.expected_keys[len(self.calls)]
        self.calls.append(blank_key)
        return ModelResponse(
            content=json.dumps(
                {
                    "blankKey": blank_key,
                    "decision": "correct",
                    "reason": "student response matches the configured answer",
                    "evidenceRegionIds": [self.evidence_region_id],
                    "confidence": 0.99,
                }
            ),
            raw={},
            usage={"totalTokens": 1},
        )


def _cases() -> list[dict[str, object]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["cases"]


def _oracle(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _point_in_polygon(point: dict[str, float], polygon: list[dict[str, float]]) -> bool:
    """Small test-only containment check for template and mapped sentinels."""

    inside = False
    previous = polygon[-1]
    for current in polygon:
        crosses = (current["y"] > point["y"]) != (previous["y"] > point["y"])
        if crosses:
            intersection_x = (previous["x"] - current["x"]) * (
                point["y"] - current["y"]
            ) / (previous["y"] - current["y"]) + current["x"]
            if point["x"] < intersection_x:
                inside = not inside
        previous = current
    return inside


def _fixture_mapping(case: dict[str, object]):
    """Map fixture frames through a non-identity page transform, test-side only."""

    frames = list(case["frameRegions"])
    question = case["question"]
    assert isinstance(question, dict)
    fragments: list[dict[str, object]] = []
    page_alignments: dict[str, FramePageAlignment] = {}
    for index, frame in enumerate(frames):
        assert isinstance(frame, dict)
        polygon = frame["polygon"]
        assert isinstance(polygon, list)
        xs = [float(point["x"]) for point in polygon]
        ys = [float(point["y"]) for point in polygon]
        page_number = int(frame["pageNumber"])
        template_page_id = f"template-page-{page_number}"
        fragments.append(
            {
                "id": f"fixture-frame-{index}",
                "regionKey": str(frame["regionKey"]),
                "templatePageId": template_page_id,
                "pageNumber": page_number,
                "coordinateSpace": "template_page_normalized",
                "x": min(xs),
                "y": min(ys),
                "width": max(xs) - min(xs),
                "height": max(ys) - min(ys),
                "sortOrder": index,
            }
        )
        page_alignments[template_page_id] = FramePageAlignment(
            template_page_id=template_page_id,
            template_page_number=page_number,
            student_page_id=f"student-page-{page_number}",
            alignment_revision_id=f"alignment-{page_number}",
            template_size=PageSize(1000, 1000),
            student_size=PageSize(1100, 1100),
            template_to_student=Homography.from_rows(
                ((1.0, 0.0, 37.0), (0.0, 1.0, 23.0), (0.0, 0.0, 1.0))
            ),
            quality=AlignmentQuality(
                method="fixture_translation",
                score=1.0,
                matched_features=8,
                inliers=8,
                inlier_ratio=1.0,
                mean_reprojection_error_px=0.0,
                template_feature_coverage=1.0,
                student_feature_coverage=1.0,
                visible_template_ratio=1.0,
                is_reliable=True,
            ),
        )
    case_id = str(case.get("caseId") or case.get("oracleId"))
    frame_set = {
        "id": f"fixture-frame-set-{case_id}",
        "status": "confirmed",
        "items": [
            {
                "id": f"fixture-item-{case_id}",
                "questionId": f"fixture-question-{case_id}",
                "status": "confirmed",
                "fragments": fragments,
            }
        ],
    }
    return map_confirmed_frame_set(
        frame_set,
        page_alignments,
        min_alignment_score=0.55,
        min_polygon_area_px=16.0,
        min_visible_ratio=0.8,
        max_out_of_bounds_ratio=0.2,
        max_cross_question_overlap_ratio=0.1,
    ), frames


def _mapped_sentinel_is_inside(
    sentinel: dict[str, object], mappings: tuple[MappedFrameRegion, ...]
) -> bool:
    point = sentinel["point"]
    assert isinstance(point, dict)
    page_number = int(sentinel["pageNumber"])
    student_point = {"x": float(point["x"]) * 1000 + 37, "y": float(point["y"]) * 1000 + 23}
    return any(
        mapping.page_number == page_number
        and _point_in_polygon(student_point, mapping.original_page_polygon.as_dicts())
        for mapping in mappings
    )


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda case: str(case["caseId"]),
)
@pytest.mark.asyncio
async def test_generic_frame_keyed_recognition_and_grading_chain(
    case: dict[str, object],
) -> None:
    blanks = list(case["blanks"])
    expected_keys = [str(blank["blankKey"]) for blank in blanks]
    assert expected_keys == [f"B{index}" for index in range(1, len(blanks) + 1)]
    assert len(blanks) in {1, 2, 3, 5}

    frame_regions = list(case["frameRegions"])
    evidence_region_ids = {str(region["regionKey"]) for region in frame_regions}
    shared_evidence_id = str(frame_regions[0]["regionKey"])
    recognition_payload = {
        "answers": [
            {
                "blankKey": blank["blankKey"],
                "recognizedText": blank["referenceAnswer"],
                "isBlank": False,
                "confidence": 0.99,
                "issues": [],
                "evidenceRefs": [shared_evidence_id],
            }
            for blank in reversed(blanks)
        ]
    }
    parsed = parse_keyed_fill_response(
        json.dumps(recognition_payload, ensure_ascii=False),
        expected_keys=expected_keys,
        allowed_evidence_refs=evidence_region_ids,
    )
    assert parsed.issues == []
    assert [item["blankKey"] for item in parsed.nodes] == expected_keys

    configured_blanks = []
    for blank, recognized in zip(blanks, parsed.nodes, strict=True):
        configured_blanks.append(
            {
                "blankKey": recognized["blankKey"],
                "maxScore": blank["maxScore"],
                "answerKind": "text",
                "standardAnswers": [blank["referenceAnswer"]],
                "synonyms": [],
                "studentAnswer": recognized["recognizedText"],
                "isBlank": recognized["isBlank"],
                "recognitionConfidence": recognized["confidence"],
                "evidenceRegionIds": recognized["evidenceRefs"],
            }
        )
    expected_total = sum(
        (Decimal(str(blank["maxScore"])) for blank in blanks),
        Decimal(0),
    )
    evidence = EvidenceRef(
        page_id=f"page-{case['question']['pageNumbers'][0]}",
        region_id=shared_evidence_id,
        original_bbox=BoundingBox(x=10, y=20, width=300, height=120),
        recognized_text="shared complete-question evidence",
    )
    grading_input = QuestionGradingInput(
        run_id="generic-run",
        question_id=f"question-{case['caseId']}",
        question_type=QuestionType.FILL_BLANK,
        max_score=expected_total,
        question_content=str(case["question"]["text"]),
        standard_answer_snapshot={},
        student_response={"summaryOnly": True},
        evidence_regions=[evidence],
        recognition_confidence=0.99,
        grading_config={"blanks": configured_blanks},
        frame_set_id="generic-frame-set",
        blank_config_version_id="generic-blank-config",
        processing_revision_id="generic-processing",
    )
    model = KeyedJudgeStub(expected_keys, shared_evidence_id)

    result = await grade_fill_question(grading_input, model)  # type: ignore[arg-type]

    assert model.calls == expected_keys
    assert [decision.key for decision in result.decisions] == expected_keys
    assert result.status is GradingStatus.GRADED
    assert result.final_score == expected_total.quantize(Decimal("0.01"))
    assert all(
        [reference.region_id for reference in decision.evidence_refs]
        == [shared_evidence_id]
        for decision in result.decisions
    )


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
def test_keyed_recognition_fails_closed_when_any_runtime_key_is_missing(
    blank_count: int,
) -> None:
    expected_keys = [f"B{index}" for index in range(1, blank_count + 1)]
    answers = [
        {
            "blankKey": key,
            "recognizedText": f"answer-{key}",
            "isBlank": False,
            "confidence": 0.99,
            "issues": [],
            "evidenceRefs": ["whole-question-frame"],
        }
        for key in expected_keys[:-1]
    ]

    parsed = parse_keyed_fill_response(
        json.dumps({"answers": answers}),
        expected_keys=expected_keys,
        allowed_evidence_refs={"whole-question-frame"},
    )

    assert any(issue["code"] == "blank_key_missing" for issue in parsed.issues)
    assert {item["blankKey"] for item in parsed.nodes} != set(expected_keys)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["caseId"]))
def test_generic_confirmed_frames_keep_required_and_forbidden_sentinels_after_mapping(
    case: dict[str, object],
) -> None:
    """The common 1/2/3/5 matrix exercises frame -> mapped-frame provenance."""

    result, _frames = _fixture_mapping(case)

    assert result.status == "ready"
    assert result.blockers == ()
    assert len(result.mappings) == len(case["frameRegions"])
    assert all(mapping.status == "ready" for mapping in result.mappings)
    assert all(
        _mapped_sentinel_is_inside(sentinel, result.mappings)
        for sentinel in case["requiredContentSentinels"]
    )
    assert all(
        not _mapped_sentinel_is_inside(sentinel, result.mappings)
        for sentinel in case["forbiddenSentinels"]
    )


@pytest.mark.asyncio
async def test_q11_candidate_oracle_is_a_three_blank_regression_and_old_segments_fail_closed(
) -> None:
    """Candidate remains unreviewed; it is never a production rule or teacher fact."""

    oracle = _oracle(Q11_ORACLE)
    assert oracle["reviewStatus"] == "candidate"
    assert oracle["reviewedBy"] is None
    assert oracle["reviewedAt"] is None
    assert oracle["question"]["number"] == 11
    assert oracle["question"]["type"] == "fill_blank"
    assert oracle["question"]["referenceAnswers"] == [
        "\u7535\u8377\u8f6c\u79fb",
        "\u9075\u5b88",
        "CD",
    ]
    assert [blank["blankKey"] for blank in oracle["blanks"]] == ["B1", "B2", "B3"]
    assert [blank["referenceAnswer"] for blank in oracle["blanks"]] == [
        "\u7535\u8377\u8f6c\u79fb",
        "\u9075\u5b88",
        "CD",
    ]
    assert [Decimal(blank["maxScore"]) for blank in oracle["blanks"]] == [
        Decimal("1"),
        Decimal("1"),
        Decimal("3"),
    ]
    required_kinds = [item["kind"] for item in oracle["requiredContentSentinels"]]
    assert "stem" in required_kinds
    assert required_kinds.count("subquestion") == 2
    assert "diagram" in required_kinds
    assert required_kinds.count("declared_option") == 4
    assert all(
        distractor["mustNotCreateBlank"] is True
        for distractor in oracle["distractorCandidates"]
    )

    expected_keys = ["B1", "B2", "B3"]
    old_two_segment = parse_keyed_fill_response(
        json.dumps(
            {
                "transcription": "legacy positional text",
                "segments": [
                    {"region_index": 1, "transcription": "first two answers"},
                    {"region_index": 2, "transcription": "third answer"},
                ],
            }
        ),
        expected_keys=expected_keys,
        allowed_evidence_refs={"whole-q11-frame"},
    )
    assert old_two_segment.nodes == []
    assert [issue["code"] for issue in old_two_segment.issues] == ["missing_array"]

    missing_b3 = parse_keyed_fill_response(
        json.dumps(
            {
                "answers": [
                    {
                        "blankKey": blank["blankKey"],
                        "recognizedText": blank["referenceAnswer"],
                        "isBlank": False,
                        "confidence": 0.99,
                        "issues": [],
                        "evidenceRefs": ["whole-q11-frame"],
                    }
                    for blank in oracle["blanks"][:2]
                ]
            },
            ensure_ascii=False,
        ),
        expected_keys=expected_keys,
        allowed_evidence_refs={"whole-q11-frame"},
    )
    assert [item["blankKey"] for item in missing_b3.nodes] == ["B1", "B2"]
    assert any(issue["code"] == "blank_key_missing" for issue in missing_b3.issues)

    parsed = parse_keyed_fill_response(
        json.dumps(
            {
                "answers": [
                    {
                        "blankKey": blank["blankKey"],
                        "recognizedText": blank["referenceAnswer"],
                        "isBlank": False,
                        "confidence": 0.99,
                        "issues": [],
                        "evidenceRefs": ["whole-q11-frame"],
                    }
                    for blank in reversed(oracle["blanks"])
                ]
            },
            ensure_ascii=False,
        ),
        expected_keys=expected_keys,
        allowed_evidence_refs={"whole-q11-frame"},
    )
    assert parsed.issues == []
    assert [item["blankKey"] for item in parsed.nodes] == expected_keys
    evidence = EvidenceRef(
        page_id="q11-candidate-page",
        region_id="whole-q11-frame",
        original_bbox=BoundingBox(x=95, y=165, width=820, height=415),
        recognized_text="complete question evidence",
    )
    grading_input = QuestionGradingInput(
        run_id="q11-candidate-run",
        question_id="candidate-q11",
        question_type=QuestionType.FILL_BLANK,
        max_score=Decimal("5"),
        question_content="candidate-only regression context",
        standard_answer_snapshot={},
        student_response={"summaryOnly": True},
        evidence_regions=[evidence],
        recognition_confidence=0.99,
        grading_config={
            "blanks": [
                {
                    "blankKey": blank["blankKey"],
                    "maxScore": blank["maxScore"],
                    "answerKind": "text",
                    "standardAnswers": [blank["referenceAnswer"]],
                    "synonyms": [],
                    "studentAnswer": recognized["recognizedText"],
                    "isBlank": recognized["isBlank"],
                    "recognitionConfidence": recognized["confidence"],
                    "evidenceRegionIds": recognized["evidenceRefs"],
                }
                for blank, recognized in zip(oracle["blanks"], parsed.nodes, strict=True)
            ]
        },
        frame_set_id="q11-candidate-frame-set",
        blank_config_version_id="q11-candidate-config",
        processing_revision_id="q11-candidate-processing",
    )
    judge = KeyedJudgeStub(expected_keys, "whole-q11-frame")
    result = await grade_fill_question(grading_input, judge)  # type: ignore[arg-type]

    assert judge.calls == expected_keys
    assert [decision.key for decision in result.decisions] == expected_keys
    assert [decision.max_score for decision in result.decisions] == [
        Decimal("1"),
        Decimal("1"),
        Decimal("3"),
    ]
    assert result.status is GradingStatus.GRADED
    assert result.final_score == Decimal("5.00")


def test_q8_candidate_frame_mapping_preserves_full_range_without_adjacent_content() -> None:
    """The unreviewed q8 fixture uses the same generic mapping contract."""

    oracle = _oracle(Q8_ORACLE)
    assert oracle["reviewStatus"] == "candidate"
    assert oracle["reviewedBy"] is None
    assert oracle["reviewedAt"] is None
    assert oracle["question"]["number"] == 8
    assert oracle["question"]["type"] == "multiple_choice"

    result, frames = _fixture_mapping(oracle)

    assert result.status == "ready"
    assert result.blockers == ()
    assert len(result.mappings) == len(frames) == 1
    mapping = result.mappings[0]
    frame = frames[0]
    polygon = frame["polygon"]
    assert isinstance(polygon, list)
    expected_template_polygon = [
        {"x": float(point["x"]) * 1000, "y": float(point["y"]) * 1000}
        for point in polygon
    ]
    expected_student_polygon = [
        {"x": point["x"] + 37, "y": point["y"] + 23}
        for point in expected_template_polygon
    ]
    assert mapping.template_page_polygon.as_dicts() == expected_template_polygon
    assert mapping.original_page_polygon.as_dicts() == expected_student_polygon
    assert mapping.visible_original_page_polygon is not None
    assert mapping.visible_original_page_polygon.as_dicts() == expected_student_polygon
    assert all(
        _mapped_sentinel_is_inside(sentinel, result.mappings)
        for sentinel in oracle["requiredContentSentinels"]
    )
    assert all(
        not _mapped_sentinel_is_inside(sentinel, result.mappings)
        for sentinel in oracle["forbiddenSentinels"]
    )
