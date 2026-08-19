from homework_judge.recognition.parser import parse_model_payload


def test_parses_code_fenced_json() -> None:
    result = parse_model_payload(
        '说明\n```json\n{"questions":[{"number":"1","stem":"题干"}]}\n```',
        "exam",
    )
    assert result.nodes == [{"number": "1", "stem": "题干"}]
    assert result.issues == []


def test_keeps_good_nodes_when_one_is_bad() -> None:
    result = parse_model_payload(
        '{"answers":[{"numberHint":"1","answer":"A"},"bad",{"numberHint":"2","answer":"B"}]}',
        "answer",
    )
    assert len(result.nodes) == 2
    assert result.issues[0]["code"] == "node_not_object"


def test_invalid_json_is_a_structured_issue() -> None:
    result = parse_model_payload("not json", "exam")
    assert not result.nodes
    assert result.issues[0]["code"] == "invalid_json"
