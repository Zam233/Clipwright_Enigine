"""`fact_need` 能力（插件提供）：判断哪些场景**需要外部实时/事实数据**。

用户 2026-11-02 的两条批评合在这里落地：

1. **判断权必须在模型，不在正则**。旧实现是动画 agent 里一条 `_DATA_FACT_RE`
   （图表|数据|%|年份 20\\d\\d|引语|GDP|报告…）：词表漏一个就整场不联网，
   写多了又把"数据可视化"这种纯视觉描述也拉去搜索。
2. **它该是可替换的能力，不该沉在 engine**。所以做成插件工具 `fact_need`：
   谁都能注册一个 `capability = "fact_need"` 的实现接管这个判断（例如接内部的
   事实核查服务、或换成更严的合规词表），agent 代码一个字都不用改。

注意是**一次批量调用**：43 个场景逐个问模型 = 43 次往返，那不叫尊重模型判断。
"""

from __future__ import annotations

import json
import re
from typing import Any

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

#: 旧正则（仅 heuristic 模式使用；保留是为了可解释、可回退，且只存在于本插件里）
_LEGACY_DATA_FACT_RE = re.compile(
    r"图表|数据|统计|对比|百分比|%|年份 20\d\d|引语|quote|citation|增长率|GDP|报告|survey",
    re.IGNORECASE,
)

#: 一次判断最多看多少个场景
MAX_SCENES = 60
MAX_CHARS_PER_SCENE = 160


def heuristic_hit(text: str) -> bool:
    """旧正则判断（heuristic 模式实现，公开给测试与回退路径用）。"""
    return bool(_LEGACY_DATA_FACT_RE.search(str(text or "")))


def _prompt(scenes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, scene in enumerate(scenes):
        title = str(scene.get("title") or "").strip()
        desc = str(scene.get("description") or "").strip()
        text = str(scene.get("text") or "").strip()
        keywords = scene.get("keywords")
        kw = "、".join(str(k) for k in keywords[:6]) if isinstance(keywords, list) else ""
        body = " ".join(x for x in (title, desc, text) if x)[:MAX_CHARS_PER_SCENE]
        lines.append(f"{idx}. {body}" + (f"（关键词：{kw}）" if kw else ""))
    return (
        "你是视频动画阶段的事实核查助手。下面是按顺序编号的场景描述。\n"
        "请判断**哪些场景需要外部实时/事实数据**才能把动画做对——例如具体数字、统计、\n"
        "年份/日期事件、排名、金额、比例、引文出处、产品参数、赛事比分、股价汇率这类\n"
        "「不查就会编」的内容。纯情绪、纯风格、纯视觉描述（例如“强调标题出现”“对比左右\n"
        "两栏布局”“节奏加快”）**不需要**外部数据。\n\n"
        "场景列表：\n" + "\n".join(lines) + "\n\n"
        "只输出 JSON 数组，元素是上面编号中需要外部数据的场景编号，例如 [0,3,7]。\n"
        "没有任何场景需要就输出 []。不要解释、不要代码块标记。"
    )


def _parse_ids(raw: str, total: int) -> set[int]:
    text = str(raw or "").strip()
    if not text:
        return set()
    m = re.search(r"\[[^\[\]]*\]", text, re.DOTALL)
    payload = m.group(0) if m else text
    try:
        data = json.loads(payload)
    except Exception:  # noqa: BLE001 — 模型给了非 JSON：当作没判断出来
        logger.info("fact_need: 模型返回不是 JSON，按“不需要”处理: %.120s", text)
        return set()
    if not isinstance(data, list):
        return set()
    out: set[int] = set()
    for item in data:
        try:
            idx = int(item)
        except Exception:  # noqa: BLE001
            continue
        if 0 <= idx < total:
            out.add(idx)
    return out


async def _ask_llm(prompt: str) -> str:
    from clipwright.services.llm import LLMService

    resp = await LLMService().ask(prompt, use_flash=True, max_tokens=200)
    if not getattr(resp, "success", False):
        raise RuntimeError(str(getattr(resp, "error", "") or "LLM 调用失败"))
    return str(getattr(resp, "content", "") or "")


def _mode(explicit: str = "") -> str:
    if explicit:
        return str(explicit).strip().lower()
    try:
        from clipwright.config import settings

        return str(getattr(settings, "animation_fact_judge", "llm") or "llm").strip().lower()
    except Exception:  # noqa: BLE001
        return "llm"


def _heuristic_indexes(items: list[dict[str, Any]]) -> set[int]:
    return {
        i for i, s in enumerate(items)
        if heuristic_hit(" ".join(str(s.get(k) or "") for k in ("title", "description", "text")))
    }


async def decide(scenes: list[dict[str, Any]], *, mode: str = "") -> tuple[set[int], str]:
    """返回 `(需要外部事实的场景下标集合, 实际使用的模式)`。"""
    items = list(scenes or [])[:MAX_SCENES]
    if not items:
        return set(), _mode(mode)
    how = _mode(mode)
    if how == "off":
        return set(), how
    if how == "heuristic":
        return _heuristic_indexes(items), how
    try:
        raw = await _ask_llm(_prompt(items))
        picked = _parse_ids(raw, len(items))
        logger.info("fact_need: 模型判定 %d/%d 个场景需要外部事实（%s）",
                    len(picked), len(items), sorted(picked))
        return picked, "llm"
    except Exception as exc:  # noqa: BLE001 — 模型不可用 → 退回正则，不阻断动画
        logger.info("fact_need: 模型判断不可用（%s），退回启发式", exc)
        return _heuristic_indexes(items), "heuristic"


class FactNeedTool(BaseTool):
    """判断哪些场景需要外部事实数据（模型判断，一次批量）。"""

    name = "fact_need"
    capability = "fact_need"
    capability_priority = 50
    permission_tier = "costly"       # 一次 flash 调用 → 花钱
    dry_run_capable = False
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "判断一批场景里**哪些需要外部实时/事实数据**（数字、统计、年份事件、排名、金额、"
        "比例、引文出处、产品参数…），由模型判断、一次批量完成。"
        "参数：`scenes`（数组，每项 {id?, title?, description?, text?, keywords?[]}）；"
        "可选 `mode`（llm｜heuristic｜off，缺省读配置）。"
        "返回 `{needed_ids[], picked_indexes[], total, mode}`。"
    )

    async def execute(
        self,
        scenes: list | None = None,
        mode: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        items = [s for s in (scenes or []) if isinstance(s, dict)]
        picked, how = await decide(items, mode=mode)
        needed = [
            str(items[i].get("id") or "")
            for i in sorted(picked)
            if 0 <= i < len(items) and str(items[i].get("id") or "")
        ]
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "needed_ids": needed,
                "picked_indexes": sorted(picked),
                "total": len(items),
                "mode": how,
            },
        )


__all__ = ["FactNeedTool", "MAX_SCENES", "decide", "heuristic_hit"]
