from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from homework_judge.config import Settings
from homework_judge.errors import ModelRequestError
from homework_judge.model.dashscope import DashScopeClient


def _settings(tmp_path: Path, key: str | None = "secret-test-key") -> Settings:
    return Settings(
        _env_file=None,
        APP_DATA_DIR=tmp_path,
        DATABASE_PATH=tmp_path / "test.sqlite",
        UPLOAD_DIR=tmp_path / "uploads",
        TEMP_DIR=tmp_path / "tmp",
        APP_ENV="test",
        DASHSCOPE_API_KEY=key,
    )


@pytest.mark.asyncio
async def test_array_message_content_and_secret_free_snapshot(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": '{"questions":'},
                                {"type": "text", "text": "[]}"},
                            ]
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DashScopeClient(_settings(tmp_path), http_client)
    response = await client.chat(messages=[{"role": "user", "content": "test"}])
    assert response.content == '{"questions":\n[]}'
    assert response.usage == {
        "promptTokens": 3,
        "completionTokens": 2,
        "totalTokens": 5,
    }
    snapshot = client.snapshot(
        prompt_version="test",
        purpose="test",
        details={"pageCount": 1},
    )
    assert "secret-test-key" not in str(snapshot)
    assert "secret-test-key" not in str(client.status())
    await http_client.aclose()


@pytest.mark.asyncio
async def test_auth_error_is_classified(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"message": "invalid key"})
    )
    http_client = httpx.AsyncClient(transport=transport)
    client = DashScopeClient(_settings(tmp_path), http_client)
    with pytest.raises(ModelRequestError) as captured:
        await client.chat(messages=[])
    assert captured.value.code == "MODEL_AUTH_FAILED"
    assert captured.value.status == 401
    await http_client.aclose()


@pytest.mark.asyncio
async def test_missing_key_never_sends_request(tmp_path: Path) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DashScopeClient(_settings(tmp_path, None), http_client)
    with pytest.raises(ModelRequestError) as captured:
        await client.chat(messages=[])
    assert captured.value.code == "MODEL_NOT_CONFIGURED"
    assert called is False
    await http_client.aclose()
