from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _log_level(name: str = "LOG_LEVEL", default: str = "INFO") -> str:
    value = os.getenv(name, default).strip().upper() or default
    if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"{name} must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    root_dir: Path
    data_dir: Path
    database_path: Path
    port: int
    dashscope_api_key: str
    dashscope_base_url: str
    dashscope_model: str
    model_timeout_ms: int
    model_retry_count: int
    model_concurrency: int
    model_pages_per_batch: int
    answer_pages_per_batch: int
    max_upload_mb: int
    max_document_pages: int
    auto_match_threshold: float
    auto_match_margin: float
    teacher_name: str
    soffice_path: str
    student_recognition_concurrency: int = 3
    boundary_merge_min_confidence: float = 0.85
    grading_enabled: bool = False
    grading_concurrency: int = 2
    grading_model_timeout_ms: int = 120000
    grading_model_retry_count: int = 2
    grading_auto_confidence_threshold: float = 0.95
    grading_recognition_review_threshold: float = 0.85
    grading_formula_timeout_ms: int = 1500
    mapping_min_alignment_score: float = 0.55
    mapping_min_polygon_area_px: float = 16.0
    mapping_min_visible_ratio: float = 0.8
    mapping_max_out_of_bounds_ratio: float = 0.2
    mapping_max_cross_question_overlap_ratio: float = 0.1
    log_level: str = "INFO"
    log_to_console: bool = True
    log_to_file: bool = True
    log_file_path: Path | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    @classmethod
    def load(cls) -> Settings:
        root = Path(__file__).resolve().parents[2]
        _load_env_file(root / ".env")
        raw_data = Path(os.getenv("APP_DATA_DIR", "./data/runtime"))
        data = raw_data if raw_data.is_absolute() else (root / raw_data).resolve()
        database = Path(os.getenv("DATABASE_PATH", str(data / "homework-judge.sqlite")))
        if not database.is_absolute():
            database = (root / database).resolve()
        raw_log = Path(os.getenv("LOG_FILE_PATH", "logs/homework-judge.jsonl"))
        log_file = raw_log if raw_log.is_absolute() else data / raw_log
        log_file = log_file.resolve()
        data_root = data.resolve()
        if log_file != data_root and data_root not in log_file.parents:
            raise ValueError("LOG_FILE_PATH must stay inside APP_DATA_DIR")
        return cls(
            root_dir=root,
            data_dir=data,
            database_path=database,
            port=_int("PORT", 8787, 1, 65535),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            dashscope_base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
            dashscope_model=os.getenv("DASHSCOPE_MODEL", "qwen3-vl-plus").strip(),
            model_timeout_ms=_int("MODEL_TIMEOUT_MS", 120000, 1000, 600000),
            model_retry_count=_int("MODEL_RETRY_COUNT", 2, 0, 5),
            model_concurrency=_int("MODEL_CONCURRENCY", 3, 1, 8),
            model_pages_per_batch=_int("MODEL_PAGES_PER_BATCH", 4, 1, 10),
            answer_pages_per_batch=_int("ANSWER_PAGES_PER_BATCH", 3, 2, 10),
            max_upload_mb=_int("MAX_UPLOAD_MB", 30, 1, 200),
            max_document_pages=_int("MAX_DOCUMENT_PAGES", 30, 1, 200),
            auto_match_threshold=_float("AUTO_MATCH_THRESHOLD", 0.82, 0.5, 1.0),
            auto_match_margin=_float("AUTO_MATCH_MARGIN", 0.08, 0.0, 0.5),
            teacher_name=os.getenv("TEACHER_NAME", "本机教师").strip() or "本机教师",
            soffice_path=os.getenv("SOFFICE_PATH", "").strip(),
            student_recognition_concurrency=_int(
                "STUDENT_RECOGNITION_CONCURRENCY", 3, 1, 3
            ),
            boundary_merge_min_confidence=_float("BOUNDARY_MERGE_MIN_CONFIDENCE", 0.85, 0.0, 1.0),
            grading_enabled=_bool("GRADING_ENABLED", False),
            grading_concurrency=_int("GRADING_CONCURRENCY", 2, 1, 16),
            grading_model_timeout_ms=_int("GRADING_MODEL_TIMEOUT_MS", 120000, 1000, 600000),
            grading_model_retry_count=_int("GRADING_MODEL_RETRY_COUNT", 2, 0, 5),
            grading_auto_confidence_threshold=_float(
                "GRADING_AUTO_CONFIDENCE_THRESHOLD", 0.95, 0.0, 1.0
            ),
            grading_recognition_review_threshold=_float(
                "GRADING_RECOGNITION_REVIEW_THRESHOLD", 0.85, 0.0, 1.0
            ),
            grading_formula_timeout_ms=_int("GRADING_FORMULA_TIMEOUT_MS", 1500, 100, 10000),
            mapping_min_alignment_score=_float(
                "MAPPING_MIN_ALIGNMENT_SCORE", 0.55, 0.0, 1.0
            ),
            mapping_min_polygon_area_px=_float(
                "MAPPING_MIN_POLYGON_AREA_PX", 16.0, 0.000001, 1_000_000_000.0
            ),
            mapping_min_visible_ratio=_float(
                "MAPPING_MIN_VISIBLE_RATIO", 0.8, 0.0, 1.0
            ),
            mapping_max_out_of_bounds_ratio=_float(
                "MAPPING_MAX_OUT_OF_BOUNDS_RATIO", 0.2, 0.0, 1.0
            ),
            mapping_max_cross_question_overlap_ratio=_float(
                "MAPPING_MAX_CROSS_QUESTION_OVERLAP_RATIO", 0.1, 0.0, 1.0
            ),
            log_level=_log_level(),
            log_to_console=_bool("LOG_TO_CONSOLE", True),
            log_to_file=_bool("LOG_TO_FILE", True),
            log_file_path=log_file,
            log_max_bytes=_int("LOG_MAX_BYTES", 10 * 1024 * 1024, 64 * 1024, 1024 * 1024 * 1024),
            log_backup_count=_int("LOG_BACKUP_COUNT", 5, 1, 50),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "pages").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "tmp").mkdir(parents=True, exist_ok=True)
        if self.log_to_file:
            (self.log_file_path or (self.data_dir / "logs/homework-judge.jsonl")).parent.mkdir(
                parents=True,
                exist_ok=True,
            )
