from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "homework_judge.main:app",
        host="127.0.0.1",
        port=settings.port,
        reload=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
