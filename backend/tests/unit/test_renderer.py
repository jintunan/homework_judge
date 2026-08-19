from pathlib import Path

from PIL import Image

from homework_judge.config import Settings
from homework_judge.files.renderer import prepare_document_pages


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite",
        port=8787,
        dashscope_api_key="",
        dashscope_base_url="https://example.invalid/v1",
        dashscope_model="qwen3-vl-plus",
        model_timeout_ms=1000,
        model_retry_count=0,
        model_concurrency=1,
        model_pages_per_batch=4,
        answer_pages_per_batch=3,
        max_upload_mb=30,
        max_document_pages=30,
        auto_match_threshold=0.82,
        auto_match_margin=0.08,
        teacher_name="test",
        soffice_path="",
    )


async def test_png_becomes_single_jpeg_page(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    source = tmp_path / "uploads" / "task" / "exam" / "image.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (300, 200), (255, 255, 255, 0)).save(source)
    pages = await prepare_document_pages(
        settings,
        "task",
        "document",
        source.relative_to(tmp_path).as_posix(),
        "image/png",
    )
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert (tmp_path / pages[0].relative_path).is_file()
