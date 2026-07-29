"""素材预处理后台工作线程。

定期检查待预处理队列，执行缩略图生成、元数据提取等任务。
"""

from __future__ import annotations

import asyncio

from clipwright.config import logger


async def preprocess_worker() -> None:
    """后台预处理循环 — 每 30 秒检查一次待处理任务。"""
    logger.debug("素材预处理 worker 已启动")
    while True:
        try:
            # TODO: 从队列/数据库获取待预处理任务并执行
            pass
        except Exception as e:
            logger.warning("预处理 worker 异常: %s", e)
        await asyncio.sleep(30)
