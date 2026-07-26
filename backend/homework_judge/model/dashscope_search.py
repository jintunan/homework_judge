from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..answer_config.normalizer import normalize_paper
from ..answer_config.parser import JsonCandidateError, extract_json_object
from ..answer_config.prompts import ANSWER_SEARCH_PROMPT_VERSION, build_search_prompt
from ..config import Settings
from ..errors import ModelRequestError
from ..schemas import AnswerMode, ScoringPoint
from .answer_generator import AnswerInput


@dataclass(frozen=True, slots=True)
class SearchSource:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchedAnswer:
    found: bool
    standard_answer: str
    scoring_points: list[ScoringPoint]
    reason: str
    confidence: float
    sources: list[SearchSource]
    raw_response: Any
    usage: dict[str, int] | None


def _sources(raw: dict[str, Any]) -> list[SearchSource]:
    output = raw.get("output")
    nested_search = output.get("search_info") if isinstance(output, dict) else None
    search_info = nested_search if isinstance(nested_search, dict) else raw.get("search_info")
    values = search_info.get("search_results") if isinstance(search_info, dict) else None
    if not isinstance(values, list):
        return []
    result: list[SearchSource] = []
    for value in values[:10]:
        if not isinstance(value, dict):
            continue
        url = str(value.get("url", "")).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        title = str(value.get("title", "")).strip() or url
        snippet = str(value.get("snippet", value.get("summary", ""))).strip()
        result.append(SearchSource(title=title[:300], url=url, snippet=snippet[:1000]))
    return result


class DashScopeNativeSearchClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.model_id = settings.dashscope_search_model
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.model_timeout_ms / 1000)
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def build_request_snapshot(self, answer_input: AnswerInput) -> dict[str, Any]:
        split = urlsplit(str(self.settings.dashscope_native_base_url))
        return {
            "provider": "阿里云百炼",
            "model": self.model_id,
            "endpoint": f"{split.scheme}://{split.netloc}",
            "promptVersion": ANSWER_SEARCH_PROMPT_VERSION,
            "subject": answer_input.subject.value,
            "questionNumber": answer_input.question_number,
            "questionText": answer_input.question_text,
            "questionType": answer_input.question_type.value,
            "maxScore": float(answer_input.max_score),
            "enableSearch": True,
            "enableSource": True,
            "forcedSearch": True,
        }

    async def search(self, answer_input: AnswerInput) -> SearchedAnswer:
        api_key = self.settings.api_key_value
        if not api_key:
            raise ModelRequestError("MODEL_NOT_CONFIGURED", "尚未配置 DASHSCOPE_API_KEY")
        endpoint = (
            f"{str(self.settings.dashscope_native_base_url).rstrip('/')}"
            "/services/aigc/text-generation/generation"
        )
        prompt = build_search_prompt(
            subject=answer_input.subject,
            question_number=answer_input.question_number,
            question_text=answer_input.question_text,
            question_type=answer_input.question_type,
            max_score=float(answer_input.max_score),
        )
        body = {
            "model": self.model_id,
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "你是答案检索助手。区分检索证据和自身知识，只输出 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ]
            },
            "parameters": {
                "result_format": "message",
                "enable_search": True,
                "search_options": {
                    "search_strategy": "turbo",
                    "enable_source": True,
                    "forced_search": True,
                },
            },
        }
        raw: Any = None
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
                    raw = response.json()
                except ValueError:
                    raw = response.text
                if response.status_code >= 400:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt < 2:
                        await asyncio.sleep(0.4 * (2**attempt))
                        continue
                    error_message = (
                        str(raw.get("message", response.status_code))
                        if isinstance(raw, dict)
                        else str(raw)[:500]
                    )
                    code = (
                        "MODEL_AUTH_FAILED"
                        if response.status_code in {401, 403}
                        else "SEARCH_RATE_LIMITED"
                        if response.status_code == 429
                        else "SEARCH_REQUEST_FAILED"
                    )
                    raise ModelRequestError(code, f"百炼联网搜索失败：{error_message}", raw)
                break
            except ModelRequestError:
                raise
            except httpx.TimeoutException as error:
                raise ModelRequestError("SEARCH_TIMEOUT", "百炼联网搜索请求超时") from error
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise ModelRequestError(
                        "SEARCH_NETWORK_ERROR",
                        f"无法连接百炼联网搜索：{error}",
                    ) from error
                await asyncio.sleep(0.4 * (2**attempt))

        if not isinstance(raw, dict):
            raise ModelRequestError("SEARCH_RESPONSE_INVALID", "联网搜索返回了非 JSON 响应", raw)
        output = raw.get("output")
        choices = output.get("choices") if isinstance(output, dict) else None
        response_message = (
            choices[0].get("message")
            if isinstance(choices, list)
            and choices
            and isinstance(choices[0], dict)
            else None
        )
        content = (
            response_message.get("content")
            if isinstance(response_message, dict)
            else None
        )
        if not isinstance(content, str) or not content.strip():
            raise ModelRequestError("SEARCH_RESPONSE_EMPTY", "联网搜索没有返回答案内容", raw)
        try:
            parsed = extract_json_object(content)
        except JsonCandidateError as error:
            raise ModelRequestError(
                "SEARCH_RESPONSE_INVALID",
                "联网搜索答案结构无法解析",
                raw,
            ) from error

        paper = normalize_paper(
            [
                {
                    **parsed,
                    "questionNumber": answer_input.question_number,
                    "questionText": answer_input.question_text,
                    "type": answer_input.question_type.value,
                    "maxScore": str(answer_input.max_score),
                }
            ],
            answer_mode=AnswerMode.REFERENCE_UPLOAD,
            subject=answer_input.subject,
        )
        question = paper.questions[0] if paper.questions else None
        sources = _sources(raw)
        found_value = parsed.get("found")
        declared_found = found_value is True or str(found_value).lower() in {"true", "1", "yes"}
        reliable = bool(
            declared_found
            and question
            and question.standard_answer
            and sources
            and question.confidence >= self.settings.answer_search_confidence_threshold
        )
        raw_usage = raw.get("usage")
        usage = None
        if isinstance(raw_usage, dict):
            usage = {
                key: int(raw_usage[source])
                for key, source in (
                    ("promptTokens", "input_tokens"),
                    ("completionTokens", "output_tokens"),
                    ("totalTokens", "total_tokens"),
                )
                if isinstance(raw_usage.get(source), (int, float))
            }
        return SearchedAnswer(
            found=reliable,
            standard_answer=question.standard_answer if reliable and question else "",
            scoring_points=question.scoring_points if reliable and question else [],
            reason=(
                question.reason
                if reliable and question
                else (question.reason if question else "")
                or "未检索到带可靠来源的直接答案"
            ),
            confidence=question.confidence if question else 0,
            sources=sources,
            raw_response=raw,
            usage=usage,
        )
