from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields


class ModelRequestError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        raw_response: Any = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_response = raw_response
        self.status = status


def require_found[T](value: T | None, message: str = "未找到请求的数据") -> T:
    if value is None:
        raise AppError(404, "NOT_FOUND", message)
    return value
