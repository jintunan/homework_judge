from pathlib import Path

import pytest

from homework_judge.config import Settings, _bool, _load_env_file


def test_env_file_does_not_override_existing_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / ".env"
    path.write_text('EXISTING=file\nQUOTED="hello world"\n# ignored\n', encoding="utf-8")
    monkeypatch.setenv("EXISTING", "process")
    monkeypatch.delenv("QUOTED", raising=False)
    _load_env_file(path)
    assert __import__("os").environ["EXISTING"] == "process"
    assert __import__("os").environ["QUOTED"] == "hello world"


@pytest.mark.parametrize("raw", ["1", "true", "YES", "on"])
def test_bool_accepts_true_values(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("GRADING_ENABLED", raw)
    assert _bool("GRADING_ENABLED", False) is True


@pytest.mark.parametrize("raw", ["0", "false", "NO", "off"])
def test_bool_accepts_false_values(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("GRADING_ENABLED", raw)
    assert _bool("GRADING_ENABLED", True) is False


def test_bool_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("GRADING_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="must be a boolean"):
        _bool("GRADING_ENABLED", False)


def test_grading_settings_enforce_ranges(monkeypatch) -> None:
    monkeypatch.setenv("GRADING_CONCURRENCY", "0")
    with pytest.raises(ValueError, match="GRADING_CONCURRENCY"):
        Settings.load()


def test_student_recognition_concurrency_accepts_at_most_three(monkeypatch) -> None:
    monkeypatch.setenv("STUDENT_RECOGNITION_CONCURRENCY", "3")
    assert Settings.load().student_recognition_concurrency == 3

    monkeypatch.setenv("STUDENT_RECOGNITION_CONCURRENCY", "4")
    with pytest.raises(ValueError, match="STUDENT_RECOGNITION_CONCURRENCY"):
        Settings.load()


def test_boundary_merge_confidence_enforces_range(monkeypatch) -> None:
    monkeypatch.setenv("BOUNDARY_MERGE_MIN_CONFIDENCE", "1.1")
    with pytest.raises(ValueError, match="BOUNDARY_MERGE_MIN_CONFIDENCE"):
        Settings.load()


def test_log_path_is_resolved_inside_application_data(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "runtime"
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LOG_FILE_PATH", "logs/service.jsonl")

    settings = Settings.load()

    assert settings.log_file_path == (data_dir / "logs" / "service.jsonl").resolve()


def test_log_path_cannot_escape_application_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("LOG_FILE_PATH", str(tmp_path / "outside.jsonl"))

    with pytest.raises(ValueError, match="LOG_FILE_PATH"):
        Settings.load()
