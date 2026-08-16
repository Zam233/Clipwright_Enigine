"""P8: 热点/选题发现 — 选题推荐（可选 trending 数据源 + LLM 生成）。"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger


async def suggest_topics(
    category: str = "",
    count: int = 5,
    use_web: bool = False,
) -> list[dict[str, str]]:
    """生成选题建议。

    - use_web=True 且 web_search 工具可用：先检索热点关键词，再交给 LLM 整理；
    - 否则纯 LLM 生成（按类别产出选题 + 一句话理由）；
    - LLM 不可用 → 内置启发式选题库回退（零依赖）。
    """
    count = max(1, min(10, count))
    web_keywords: list[str] = []
    if use_web:
        try:
            from clipwright.tool.registry import ToolRegistry
            tool = ToolRegistry.get("web_search")
            if tool is not None:
                result = await ToolRegistry.execute(
                    "web_search", query=f"{category} 热点 选题 2026" if category else "视频创作 热点选题",
                )
                if result.status == "success":
                    raw = result.output or {}
                    for item in (raw.get("results") or raw.get("items") or []):
                        if isinstance(item, dict) and item.get("title"):
                            web_keywords.append(str(item["title"])[:80])
        except Exception as e:
            logger.info("选题发现 web 检索失败（非致命）: %s", e)

    try:
        from clipwright.services.llm import LLMService
        ctx = "\n".join(f"- {k}" for k in web_keywords[:8]) if web_keywords else "（无外部数据）"
        prompt = (
            "你是短视频选题策划。请根据以下信息推荐 {count} 个选题，"
            "每个选题给出标题与一句话「为什么能火」。只输出 JSON 数组："
            '[{"title": "...", "reason": "..."}]，不要其它文字。\n'
            f"类别：{category or '通用'}\n"
            f"热点线索：\n{ctx}"
        ).format(count=count)
        resp = await LLMService().generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            timeout=60,
            use_flash=True,
        )
        text = str(getattr(resp, "content", "") or "").strip()
        import json as _json
        parsed = _json.loads(text)
        if isinstance(parsed, list):
            topics = [
                {"title": str(t.get("title", ""))[:80], "reason": str(t.get("reason", ""))[:200]}
                for t in parsed[:count] if isinstance(t, dict) and t.get("title")
            ]
            if topics:
                return topics
    except Exception as e:
        logger.info("选题发现 LLM 失败，回退启发式: %s", e)

    return _fallback_topics(category, count)


def _fallback_topics(category: str, count: int) -> list[dict[str, str]]:
    """内置启发式选题库（零依赖回退）。"""
    base = [
        ("AI 改变生活的一百个瞬间", "AI 工具平民化，人人都能拍自己的故事"),
        ("我用 AI 3 分钟做了一支宣传片", "展示 AI 工作流降本，观看门槛低"),
        ("普通人如何用 AI 接单变现", "热点+实用主义，转化率高的选题"),
        ("从 0 到 1 学会口播剪辑", "教学类长尾流量稳定"),
        ("年度最值得学的 10 个效率技巧", "盘点类选题天然高完播"),
        ("我的 AI 创作工具清单（含价格）", "清单类内容易收藏转发"),
        ("一镜到底：城市清晨延时", "视觉冲击强，适合竖屏分发"),
        ("冷知识：为什么视频会卡顿", "科普类自带传播性"),
    ]
    picked = [t for t in base if category in ("", "通用") or category in t[1]]
    if not picked:
        picked = base
    return [
        {"title": t, "reason": r} for t, r in picked[:count]
    ]
