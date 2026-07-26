from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..errors import ModelRequestError


@dataclass(frozen=True, slots=True)
class ModelResponse:
    raw_response: Any
    content: str
    usage: dict[str, int] | None


def extract_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text", block.get("content"))
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _response_message(raw: Any, status: int) -> str:
    if isinstance(raw, str):
        return raw[:500]
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])[:500]
        if isinstance(raw.get("message"), str):
            return str(raw["message"])[:500]
    return f"HTTP {status}"


class DashScopeClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.model_id = settings.dashscope_model
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.model_timeout_ms / 1000)
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def status(self) -> dict[str, Any]:
        host = urlsplit(str(self.settings.dashscope_base_url)).hostname or ""
        if "cn-beijing" in host:
            region_hint = "华北 2（北京）"
        elif host == "dashscope.aliyuncs.com":
            region_hint = "中国大陆公共地址"
        elif "ap-southeast-1" in host:
            region_hint = "新加坡"
        else:
            region_hint = "自定义地址"
        return {
            "configured": bool(self.settings.api_key_value),
            "provider": "阿里云百炼",
            "model": self.model_id,
            "regionHint": region_hint,
            "baseUrlConfigured": bool(host),
        }

    def snapshot(
        self,
        *,
        prompt_version: str,
        purpose: str,
        details: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        base_url = str(self.settings.dashscope_base_url)
        split = urlsplit(base_url)
        return {
            "provider": "阿里云百炼",
            "model": model or self.model_id,
            "endpoint": f"{split.scheme}://{split.netloc}",
            "promptVersion": prompt_version,
            "purpose": purpose,
            **details,
        }

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.1,
        extra_body: dict[str, Any] | None = None,
    ) -> ModelResponse:
        api_key = self.settings.api_key_value
        if not api_key:
            raise ModelRequestError("MODEL_NOT_CONFIGURED", "尚未配置 DASHSCOPE_API_KEY")
        endpoint = f"{str(self.settings.dashscope_base_url).rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": model or self.model_id,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "temperature": temperature,
        }
        if extra_body:
            body.update(extra_body)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                try:
                    raw: Any = response.json()
                except ValueError:
                    raw = response.text
                if response.status_code >= 400:
                    error_message = _response_message(raw, response.status_code)
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    if response.status_code in {401, 403}:
                        code = "MODEL_AUTH_FAILED"
                    elif response.status_code == 429:
                        code = "MODEL_RATE_LIMITED"
                    else:
                        code = "MODEL_REQUEST_FAILED"
                    raise ModelRequestError(
                        code,
                        f"百炼请求失败：{error_message}",
                        raw,
                        response.status_code,
                    )
                if not isinstance(raw, dict):
                    raise ModelRequestError(
                        "MODEL_RESPONSE_INVALID",
                        "百炼返回了非 JSON 响应",
                        raw,
                    )
                choices = raw.get("choices")
                response_message = (
                    choices[0].get("message")
                    if isinstance(choices, list)
                    and choices
                    and isinstance(choices[0], dict)
                    else None
                )
                content = extract_message_content(
                    response_message.get("content")
                    if isinstance(response_message, dict)
                    else None
                )
                if not content:
                    raise ModelRequestError(
                        "MODEL_RESPONSE_EMPTY",
                        "百炼响应中没有可用的模型文本",
                        raw,
                    )
                raw_usage = raw.get("usage")
                usage = None
                if isinstance(raw_usage, dict):
                    usage = {
                        key: int(raw_usage[source])
                        for key, source in (
                            ("promptTokens", "prompt_tokens"),
                            ("completionTokens", "completion_tokens"),
                            ("totalTokens", "total_tokens"),
                        )
                        if isinstance(raw_usage.get(source), (int, float))
                    }
                return ModelResponse(raw_response=raw, content=content, usage=usage)
            except ModelRequestError:
                raise
            except httpx.TimeoutException as error:
                raise ModelRequestError("MODEL_TIMEOUT", "百炼请求超时") from error
            except httpx.HTTPError as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
        raise ModelRequestError(
            "MODEL_NETWORK_ERROR",
            f"无法连接百炼服务：{last_error or '网络错误'}",
        )
