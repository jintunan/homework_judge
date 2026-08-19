from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def success(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"data": data, "error": None}, status_code=status_code)
