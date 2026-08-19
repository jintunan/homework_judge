from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from homework_judge.config import Settings
from homework_judge.errors import AppError
from homework_judge.files.storage import remove_task_files


def test_remove_task_files_is_isolated_and_rejects_path_traversal(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(data_dir=tmp_path))
    target = tmp_path / "uploads" / "task-a"
    other = tmp_path / "uploads" / "task-b"
    outside = tmp_path / "outside.txt"
    target.mkdir(parents=True)
    other.mkdir(parents=True)
    (target / "student.pdf").write_bytes(b"target")
    (other / "student.pdf").write_bytes(b"other")
    outside.write_bytes(b"outside")

    remove_task_files(settings, "task-a")

    assert not target.exists()
    assert (other / "student.pdf").read_bytes() == b"other"
    assert outside.read_bytes() == b"outside"
    for invalid in ("", ".", "..", "../task-b", "task-a/../../outside"):
        with pytest.raises(AppError) as raised:
            remove_task_files(settings, invalid)
        assert raised.value.code in {"TASK_ID_INVALID", "FILE_PATH_FORBIDDEN"}
