from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Settings
from ..errors import ModelError


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    raw: dict[str, Any]
    usage: dict[str, int]


class DashScopeClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.model_timeout_ms / 1000)
        )
        self._semaphore = asyncio.Semaphore(settings.model_concurrency)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.dashscope_api_key),
            "provider": "阿里云百炼",
            "model": self.settings.dashscope_model,
            "baseUrlConfigured": bool(self.settings.dashscope_base_url),
        }

    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        if not self.settings.dashscope_api_key:
            raise ModelError("MODEL_NOT_CONFIGURED", "尚未配置 DASHSCOPE_API_KEY")
        body = {
            "model": self.settings.dashscope_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "enable_thinking": False,
        }
        endpoint = f"{self.settings.dashscope_base_url}/chat/completions"
        async with self._semaphore:
            for attempt in range(self.settings.model_retry_count + 1):
                try:
                    response = await self.client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self.settings.dashscope_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                except httpx.TimeoutException as error:
                    if attempt < self.settings.model_retry_count:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    raise ModelError("MODEL_TIMEOUT", "百炼请求超时") from error
                except httpx.HTTPError as error:
                    if attempt < self.settings.model_retry_count:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    raise ModelError("MODEL_NETWORK_ERROR", "无法连接百炼服务") from error
                try:
                    raw: Any = response.json()
                except ValueError:
                    raw = {"message": response.text[:500]}
                if response.status_code >= 400:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt < self.settings.model_retry_count:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    code = (
                        "MODEL_AUTH_FAILED"
                        if response.status_code in {401, 403}
                        else "MODEL_RATE_LIMITED"
                        if response.status_code == 429
                        else "MODEL_REQUEST_FAILED"
                    )
                    message = "百炼认证失败" if code == "MODEL_AUTH_FAILED" else "百炼请求失败"
                    raise ModelError(code, message, {"status": response.status_code})
                if not isinstance(raw, dict):
                    raise ModelError("MODEL_RESPONSE_INVALID", "百炼返回了无效响应")
                choices = raw.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ModelError("MODEL_RESPONSE_EMPTY", "百炼响应没有可用结果")
                message = choices[0].get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else ""
                if isinstance(content, list):
                    content = "\n".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, dict) and item.get("text")
                    )
                if not isinstance(content, str) or not content.strip():
                    raise ModelError("MODEL_RESPONSE_EMPTY", "百炼响应没有可用文本")
                usage_raw = raw.get("usage", {})
                usage = {
                    "promptTokens": int(usage_raw.get("prompt_tokens", 0)),
                    "completionTokens": int(usage_raw.get("completion_tokens", 0)),
                    "totalTokens": int(usage_raw.get("total_tokens", 0)),
                }
                return ModelResponse(content=content.strip(), raw=raw, usage=usage)
        raise ModelError("MODEL_REQUEST_FAILED", "百炼请求失败")
