from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures"
GENERIC_FIXTURE = FIXTURE_DIR / "generic_blank_layout_cases.json"
REAL_ORACLES = (
    FIXTURE_DIR / "q8_full_frame_oracle.json",
    FIXTURE_DIR / "q11_three_blanks_oracle.json",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    assert isinstance(value, dict)
    return value


def _repo_path(relative_path: str) -> Path:
    path = (ROOT / relative_path).resolve()
    assert path == ROOT or ROOT in path.parents
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _point_on_segment(
    point: dict[str, float],
    start: dict[str, float],
    end: dict[str, float],
) -> bool:
    cross = (point["y"] - start["y"]) * (end["x"] - start["x"]) - (
        point["x"] - start["x"]
    ) * (end["y"] - start["y"])
    if abs(cross) > 1e-9:
        return False
    return (
        min(start["x"], end["x"]) - 1e-9
        <= point["x"]
        <= max(start["x"], end["x"]) + 1e-9
        and min(start["y"], end["y"]) - 1e-9
        <= point["y"]
        <= max(start["y"], end["y"]) + 1e-9
    )


def _point_in_polygon(point: dict[str, float], polygon: list[dict[str, float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _point_on_segment(point, previous, current):
            return True
        crosses = (current["y"] > point["y"]) != (previous["y"] > point["y"])
        if crosses:
            intersection_x = (previous["x"] - current["x"]) * (
                point["y"] - current["y"]
            ) / (previous["y"] - current["y"]) + current["x"]
            if point["x"] < intersection_x:
                inside = not inside
        previous = current
    return inside


def _inside_any_frame(item: dict[str, Any], frames: list[dict[str, Any]]) -> bool:
    return any(
        frame["pageNumber"] == item["pageNumber"]
        and _point_in_polygon(item["point"], frame["polygon"])
        for frame in frames
    )


def _assert_normalized_point(point: dict[str, Any]) -> None:
    assert set(point) == {"x", "y"}
    assert all(isinstance(point[axis], int | float) for axis in ("x", "y"))
    assert all(0.0 <= float(point[axis]) <= 1.0 for axis in ("x", "y"))


def _assert_common_geometry(case: dict[str, Any]) -> None:
    frames = case["frameRegions"]
    assert frames
    assert len({frame["regionKey"] for frame in frames}) == len(frames)
    assert {frame["pageNumber"] for frame in frames} == set(case["question"]["pageNumbers"])

    for frame in frames:
        assert len(frame["polygon"]) >= 4
        for point in frame["polygon"]:
            _assert_normalized_point(point)

    contexts = case["visualContextRegions"]
    context_keys = {context["contextRegionKey"] for context in contexts}
    assert len(context_keys) == len(contexts)
    for context in contexts:
        assert context["pageNumber"] in case["question"]["pageNumbers"]
        for point in context["polygon"]:
            _assert_normalized_point(point)

    blanks = case["blanks"]
    assert [blank["blankKey"] for blank in blanks] == [
        f"B{index}" for index in range(1, len(blanks) + 1)
    ]
    for blank in blanks:
        anchor = blank["anchor"]
        _assert_normalized_point(anchor["point"])
        assert _inside_any_frame(anchor, frames)
        assert blank["contextRegionKeys"]
        assert set(blank["contextRegionKeys"]) <= context_keys
        assert blank["referenceAnswer"]
        assert Decimal(blank["maxScore"]) > 0

    if blanks:
        assert sum(Decimal(blank["maxScore"]) for blank in blanks) == Decimal(
            case["question"]["maxScore"]
        )

    response_anchors = case.get("responseAnchors", [])
    for response in response_anchors:
        anchor = response["anchor"]
        _assert_normalized_point(anchor["point"])
        assert _inside_any_frame(anchor, frames)
        assert response["contextRegionKeys"]
        assert set(response["contextRegionKeys"]) <= context_keys
        assert response["referenceAnswer"]
        assert Decimal(response["maxScore"]) > 0
    if response_anchors:
        assert sum(Decimal(response["maxScore"]) for response in response_anchors) == Decimal(
            case["question"]["maxScore"]
        )

    required = case["requiredContentSentinels"]
    forbidden = case["forbiddenSentinels"]
    assert required
    assert forbidden
    assert len({item["sentinelId"] for item in [*required, *forbidden]}) == len(
        [*required, *forbidden]
    )
    for sentinel in required:
        _assert_normalized_point(sentinel["point"])
        assert _inside_any_frame(sentinel, frames), sentinel["sentinelId"]
    for sentinel in forbidden:
        _assert_normalized_point(sentinel["point"])
        assert not _inside_any_frame(sentinel, frames), sentinel["sentinelId"]

    anchor_locations = {
        (blank["anchor"]["pageNumber"], *blank["anchor"]["point"].values())
        for blank in blanks
    }
    for distractor in case["distractorCandidates"]:
        _assert_normalized_point(distractor["point"])
        assert distractor["mustNotCreateBlank"] is True
        distractor_location = (
            distractor["pageNumber"],
            distractor["point"]["x"],
            distractor["point"]["y"],
        )
        assert distractor_location not in anchor_locations


def _sharing_signature(case: dict[str, Any]) -> list[tuple[str, ...]]:
    blank_keys_by_context: dict[str, list[str]] = defaultdict(list)
    for blank in case["blanks"]:
        for context_key in blank["contextRegionKeys"]:
            blank_keys_by_context[context_key].append(blank["blankKey"])
    return sorted(tuple(keys) for keys in blank_keys_by_context.values())


def test_generic_fixture_covers_required_blank_counts_and_layouts() -> None:
    fixture = _load_json(GENERIC_FIXTURE)
    assert fixture["schemaVersion"] == 1
    assert fixture["fixtureKind"] == "generic_blank_layout_cases"
    assert fixture["coordinateSpace"] == {
        "unit": "normalized",
        "origin": "top_left",
        "minimum": 0.0,
        "maximum": 1.0,
    }

    cases = fixture["cases"]
    assert {len(case["blanks"]) for case in cases} >= {1, 2, 3, 5}
    assert len({case["caseId"] for case in cases}) == len(cases)
    assert len({case["question"]["number"] for case in cases}) == len(cases)
    assert len({case["question"]["text"] for case in cases}) == len(cases)
    assert len({tuple(case["question"]["pageNumbers"]) for case in cases}) == len(cases)

    traits = {trait for case in cases for trait in case["layoutTraits"]}
    assert {
        "same_row",
        "cross_line",
        "multiple_subquestions",
        "shared_visual_context",
        "option_interference",
        "diagram_text_interference",
    } <= traits

    for case in cases:
        _assert_common_geometry(case)


def test_metamorphic_cases_change_surface_data_but_keep_structure() -> None:
    cases = _load_json(GENERIC_FIXTURE)["cases"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if case["metamorphicGroup"] is not None:
            groups[case["metamorphicGroup"]].append(case)
    assert groups

    for variants in groups.values():
        assert len(variants) >= 2
        first = variants[0]
        for transformed in variants[1:]:
            assert transformed["question"]["number"] != first["question"]["number"]
            assert transformed["question"]["text"] != first["question"]["text"]
            assert transformed["question"]["pageNumbers"] != first["question"]["pageNumbers"]
            assert transformed["frameRegions"] != first["frameRegions"]
            assert [blank["referenceAnswer"] for blank in transformed["blanks"]] != [
                blank["referenceAnswer"] for blank in first["blanks"]
            ]

            assert transformed["layoutTraits"] == first["layoutTraits"]
            assert [blank["blankKey"] for blank in transformed["blanks"]] == [
                blank["blankKey"] for blank in first["blanks"]
            ]
            assert [blank["subquestionKey"] for blank in transformed["blanks"]] == [
                blank["subquestionKey"] for blank in first["blanks"]
            ]
            assert [blank["maxScore"] for blank in transformed["blanks"]] == [
                blank["maxScore"] for blank in first["blanks"]
            ]
            assert _sharing_signature(transformed) == _sharing_signature(first)
            assert Counter(
                item["kind"] for item in transformed["requiredContentSentinels"]
            ) == Counter(item["kind"] for item in first["requiredContentSentinels"])
            assert Counter(
                item["kind"] for item in transformed["distractorCandidates"]
            ) == Counter(item["kind"] for item in first["distractorCandidates"])


@pytest.mark.parametrize("oracle_path", REAL_ORACLES, ids=lambda path: path.stem)
def test_real_oracles_are_candidate_only_and_have_valid_geometry(oracle_path: Path) -> None:
    oracle = _load_json(oracle_path)
    assert oracle["schemaVersion"] == 1
    assert oracle["fixtureKind"] == "real_sample_oracle"
    assert oracle["reviewStatus"] == "candidate"
    assert oracle["reviewedBy"] is None
    assert oracle["reviewedAt"] is None
    assert oracle["geometryProvenance"]["status"] == "candidate"
    assert "teacher" in oracle["candidateReason"].lower()
    assert "unreviewed" in oracle["candidateReason"].lower()
    _assert_common_geometry(oracle)


@pytest.mark.parametrize("oracle_path", REAL_ORACLES, ids=lambda path: path.stem)
def test_real_oracle_sources_and_hashes_match_repository(oracle_path: Path) -> None:
    oracle = _load_json(oracle_path)
    source = oracle["source"]
    for key in (
        "sourceImage",
        "sourcePdf",
        "datasetManifest",
        "questionCatalog",
        "studentLabel",
    ):
        artifact = source[key]
        path = _repo_path(artifact["path"])
        assert path.is_file()
        assert _sha256(path) == artifact["sha256"]

    source_image = source["sourceImage"]
    with Image.open(_repo_path(source_image["path"])) as image:
        assert image.size == (source_image["width"], source_image["height"])
    assert source_image["pageNumber"] in oracle["question"]["pageNumbers"]

    manifest = _load_json(_repo_path(source["datasetManifest"]["path"]))
    assert manifest["dataset_id"] == source["datasetId"]
    assert manifest["source_pdf_sha256"] == source["sourcePdf"]["sha256"]

    student_label = _load_json(_repo_path(source["studentLabel"]["path"]))
    assert student_label["review_status"] == source["datasetReviewStatus"]
    dataset_relative_image = source_image["path"].split(f"{source['datasetId']}/", 1)[1]
    assert dataset_relative_image in student_label["pages"]


@pytest.mark.parametrize("oracle_path", REAL_ORACLES, ids=lambda path: path.stem)
def test_real_oracle_answers_and_scores_match_unreviewed_catalog(oracle_path: Path) -> None:
    oracle = _load_json(oracle_path)
    catalog_path = _repo_path(oracle["source"]["questionCatalog"]["path"])
    catalog_question = _load_json(catalog_path)[oracle["question"]["id"]]
    assert int(catalog_question["number"]) == oracle["question"]["number"]
    assert Decimal(str(catalog_question["max_score"])) == Decimal(
        oracle["question"]["maxScore"]
    )

    if oracle["question"]["type"] == "fill_blank":
        assert [blank["blankKey"] for blank in oracle["blanks"]] == ["B1", "B2", "B3"]
        assert [blank["referenceAnswer"] for blank in oracle["blanks"]] == catalog_question[
            "correct_answers"
        ]
        assert [Decimal(blank["maxScore"]) for blank in oracle["blanks"]] == [
            Decimal(str(value)) for value in catalog_question["field_scores"]
        ]
    else:
        assert oracle["question"]["referenceAnswer"] == catalog_question["correct_answer"]
        assert oracle["responseAnchors"][0]["referenceAnswer"] == catalog_question[
            "correct_answer"
        ]
        assert Decimal(oracle["responseAnchors"][0]["maxScore"]) == Decimal(
            str(catalog_question["max_score"])
        )
