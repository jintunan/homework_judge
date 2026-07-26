from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    port: int = Field(default=8787, ge=1, le=65535, validation_alias="PORT")
    app_data_dir: Path = Field(default=Path("data"), validation_alias="APP_DATA_DIR")
    database_path: Path | None = Field(default=None, validation_alias="DATABASE_PATH")
    upload_dir: Path | None = Field(default=None, validation_alias="UPLOAD_DIR")
    temp_dir: Path | None = Field(default=None, validation_alias="TEMP_DIR")
    teacher_name: str = Field(default="本机教师", validation_alias="TEACHER_NAME")

    max_upload_mb: int = Field(default=20, ge=1, le=100, validation_alias="MAX_UPLOAD_MB")
    max_files_per_batch: int = Field(
        default=50,
        ge=1,
        le=50,
        validation_alias="MAX_FILES_PER_BATCH",
    )
    max_pdf_pages: int = Field(
        default=20,
        ge=1,
        le=50,
        validation_alias="MAX_PDF_PAGES",
    )
    grading_concurrency: int = Field(
        default=2,
        ge=1,
        le=8,
        validation_alias="GRADING_CONCURRENCY",
    )
    answer_config_concurrency: int = Field(
        default=2,
        ge=1,
        le=8,
        validation_alias="ANSWER_CONFIG_CONCURRENCY",
    )
    model_timeout_ms: int = Field(
        default=120_000,
        ge=5_000,
        le=300_000,
        validation_alias="MODEL_TIMEOUT_MS",
    )
    low_confidence_threshold: float = Field(
        default=0.65,
        ge=0,
        le=1,
        validation_alias="LOW_CONFIDENCE_THRESHOLD",
    )
    answer_search_confidence_threshold: float = Field(
        default=0.72,
        ge=0,
        le=1,
        validation_alias="ANSWER_SEARCH_CONFIDENCE_THRESHOLD",
    )

    dashscope_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DASHSCOPE_API_KEY",
    )
    dashscope_base_url: AnyHttpUrl = Field(
        default_factory=lambda: AnyHttpUrl(
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        validation_alias="DASHSCOPE_BASE_URL",
    )
    dashscope_model: str = Field(
        default="qwen3.7-plus",
        validation_alias="DASHSCOPE_MODEL",
    )
    dashscope_native_base_url: AnyHttpUrl = Field(
        default_factory=lambda: AnyHttpUrl("https://dashscope.aliyuncs.com/api/v1"),
        validation_alias="DASHSCOPE_NATIVE_BASE_URL",
    )
    dashscope_search_model: str = Field(
        default="qwen-plus",
        validation_alias="DASHSCOPE_SEARCH_MODEL",
    )
    app_env: str = Field(default="development", validation_alias="APP_ENV")

    @model_validator(mode="after")
    def resolve_paths(self) -> Self:
        root = self.app_data_dir.expanduser().resolve()
        self.app_data_dir = root
        self.database_path = (
            self.database_path.expanduser().resolve()
            if self.database_path
            else root / "homework-judge.sqlite"
        )
        self.upload_dir = (
            self.upload_dir.expanduser().resolve() if self.upload_dir else root / "uploads"
        )
        self.temp_dir = self.temp_dir.expanduser().resolve() if self.temp_dir else root / "tmp"
        self.teacher_name = self.teacher_name.strip() or "本机教师"
        self.dashscope_model = self.dashscope_model.strip()
        self.dashscope_search_model = self.dashscope_search_model.strip()
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def api_key_value(self) -> str:
        return self.dashscope_api_key.get_secret_value().strip() if self.dashscope_api_key else ""

    @property
    def is_test(self) -> bool:
        return self.app_env.lower() == "test"

    def ensure_directories(self) -> None:
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        assert self.upload_dir is not None
        assert self.temp_dir is not None
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
