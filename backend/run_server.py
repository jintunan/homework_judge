from __future__ import annotations

import uvicorn

from homework_judge.config import Settings

if __name__ == "__main__":
    settings = Settings.load()
    uvicorn.run(
        "homework_judge.main:app",
        host="127.0.0.1",
        port=settings.port,
        app_dir="backend",
        workers=1,
    )
