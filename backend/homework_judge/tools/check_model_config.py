from __future__ import annotations

import asyncio

from ..config import Settings
from ..model.dashscope import DashScopeClient


async def _check() -> None:
    settings = Settings()
    client = DashScopeClient(settings)
    try:
        status = client.status()
        if not status["configured"]:
            raise SystemExit("未配置 DASHSCOPE_API_KEY，无法执行模型配置检查。")
        print(
            f"模型配置已读取：{status['model']} · {status['regionHint']}。"
            "此检查不会发送计费请求。"
        )
    finally:
        await client.close()


def main() -> None:
    asyncio.run(_check())


if __name__ == "__main__":
    main()
