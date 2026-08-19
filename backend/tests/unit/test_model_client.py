from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from homework_judge.config import Settings
from homework_judge.recognition.client import DashScopeClient


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_dashscope_client_enforces_global_model_concurrency(limit: int) -> None:
    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        finally:
            active -= 1

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = cast(
        Settings,
        SimpleNamespace(
            dashscope_api_key="test",
            dashscope_model="test-model",
            dashscope_base_url="https://example.invalid/v1",
            model_timeout_ms=1000,
            model_retry_count=0,
            model_concurrency=limit,
        ),
    )
    client = DashScopeClient(settings, http_client)
    try:
        await asyncio.gather(
            *(
                client.chat(system_prompt="test", user_content=[{"type": "text", "text": "x"}])
                for _ in range(6)
            )
        )
    finally:
        await http_client.aclose()

    assert peak == limit
