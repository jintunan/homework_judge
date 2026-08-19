from homework_judge.matching.numbers import normalize_question_number


def test_normalizes_common_question_numbers() -> None:
    assert normalize_question_number("1、") == "1"
    assert normalize_question_number("第１题") == "1"
    assert normalize_question_number("十二") == "12"
    assert normalize_question_number("1（2）") == "1.2"
    assert normalize_question_number("1-2") == "1.2"


def test_rejects_non_question_numbers() -> None:
    assert normalize_question_number("A") == ""
    assert normalize_question_number("一、选择题") == ""
    assert normalize_question_number("") == ""
