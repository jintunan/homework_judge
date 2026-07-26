from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from homework_judge.config import Settings
from homework_judge.main import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        APP_DATA_DIR=tmp_path / "data",
        DATABASE_PATH=tmp_path / "data" / "api.sqlite",
        UPLOAD_DIR=tmp_path / "data" / "uploads",
        TEMP_DIR=tmp_path / "data" / "tmp",
        APP_ENV="test",
        DASHSCOPE_API_KEY=None,
    )


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_existing_react_api_contract_without_real_model(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["data"]["database"] == "ok"

            status = await client.get("/api/model/status")
            assert status.status_code == 200
            assert status.json()["data"]["configured"] is False
            assert "api" not in str(status.json()).lower()

            created = await client.post(
                "/api/tasks",
                data={
                    "name": "高二物理单元测评",
                    "className": "高二 1 班",
                    "paperName": "静电场单元",
                    "subject": "high_school_physics",
                    "answerMode": "agent_search",
                },
                files={"template": ("paper.png", _png(), "image/png")},
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["ok"] is True
            task = body["data"]
            task_id = task["id"]
            assert task["subject"] == "high_school_physics"
            assert task["answerMode"] == "agent_search"

            listed = await client.get("/api/tasks")
            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()["data"]] == [task_id]

            saved = await client.put(
                f"/api/tasks/{task_id}/questions",
                json={
                    "questions": [
                        {
                            "number": "1",
                            "type": "choice",
                            "maxScore": 5,
                            "standardAnswer": "A",
                            "scoringPoints": [
                                {"description": "选择正确", "score": 5}
                            ],
                            "sortOrder": 0,
                        }
                    ]
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["data"]["answerConfigStatus"] == "approved"

            uploaded = await client.post(
                f"/api/tasks/{task_id}/submissions",
                files=[("files", ("张三.png", _png(), "image/png"))],
            )
            assert uploaded.status_code == 201, uploaded.text
            result = uploaded.json()["data"]["results"][0]
            assert result["ok"] is True
            assert result["submission"]["studentName"] == "张三"
            submission_id = result["submission"]["id"]

            grading = await client.post(f"/api/tasks/{task_id}/grading-runs")
            assert grading.status_code == 409
            assert grading.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"

            preview = await client.get(result["submission"]["previewUrl"])
            assert preview.status_code == 200
            assert preview.headers["content-type"].startswith("image/png")

            audit = await client.get(f"/api/submissions/{submission_id}/audit")
            assert audit.status_code == 200
            assert audit.json()["data"][0]["eventType"] == "submission.uploaded"

            missing = await client.get("/api/not-a-real-route")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "NOT_FOUND"
