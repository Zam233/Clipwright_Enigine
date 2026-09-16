"""生成「一句话 → 成片」的评分表（R10 的主观评分项）。

R10 的验收指标里有一条是"3 个项目，主观 ≥ 4/5 且改动可解释"——**主观评分必须真人打**，
模型给自己打分不算数。所以本脚本产出的是**评分表**，并且把两种分数严格分开：

- `agent_scores`：由**需求 Agent**（C1 意图理解 / C4 不过度改动）与
  **结构 Agent**（C2 改动正确 / C3 改动可解释）**按各自职责视角**自动填写（`--fill-agents`）；
  C5「失败时诚实」由**规则**判定——它是可客观核对的事实，让模型自证没有信息量。
- `scores`：**人工列**，留空等你（或评审者）填。R10 的「≥ 4/5」只认这一列。

    # 1) 生成评分表 + 让两个 Agent 先填一版（人工列仍留空）
    python scripts/make_eval_sheet.py --dir data/pexels_real --fill-agents
    # 2) 人工参考 Agent 分，在「人类评审」表格里把 0 改成 1–5 并写理由
    # 3) 汇总（人工分与 Agent 分分开报）
    python scripts/make_eval_sheet.py --score eval/eval_sheet.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 评分项：(代号, 说明, 这一项在问什么)
CRITERIA = (
    ("C1", "意图理解", "这句话被理解成了你要的意思吗？"),
    ("C2", "改动正确", "时间线改成你想要的样子了吗？"),
    ("C3", "改动可解释", "从「意图 → 预览 → 结果」这条链能看懂它为什么这么改吗？"),
    ("C4", "不过度改动", "有没有动到你没让它动的东西？"),
    ("C5", "失败时诚实", "它做不到的时候，有没有如实说清楚（而不是假装成功）？"),
)

#: 两名 Judge **各自独立给全部评分项打分**（多 Agent 共识），而不是各打一半。
#:
#: 这是"多 Agent 评分相对安全"的关键：同一份证据、两个不同职责视角、独立打分，
#: 分歧本身是可读信号（分歧大 = 这次改动的评价不稳定）。
#: 但**两个 Judge 都在同一条流水线里**，所以仍然做了三道防线（见下面的说明）：
#: ① 与**客观事实**交叉核对；② 第三个**对抗性检查**专挑毛病；③ 分歧度标注。
JUDGES: dict[str, str] = {
    "requirements_agent": (
        "你是剪辑工具里的**需求 Agent**，职责是把用户的一句话变成明确的修改诉求，"
        "并守住「只动用户要动的东西」。请以这个身份的立场，独立给下面这一次改写打分。\n"
        "· C1 意图理解：解析出的意图是否就是用户那句话的意思（有没有漏、有没有加戏）？\n"
        "· C2 改动正确：时间线的实际变化是否符合诉求本身（数量/范围/结果）？\n"
        "· C3 改动可解释：从「意图 → 预览 → 实际结果」这条链能否看懂它为什么这么改？\n"
        "· C4 不过度改动：有没有动到用户没让它动的东西？\n"
        "打分前**先核对证据**：证据里给了「改动的片段（按时间线序号）」与「实际结果」字段，"
        "判断「是不是我要的那一个」必须依据这些字段，不要凭文件名里的数字猜。\n"
        "1 分=完全不对；3 分=大致对但有偏差；5 分=完全符合。"
    ),
    "structure_agent": (
        "你是剪辑工具里的**结构 Agent**，职责是保证时间线结构合理、改动与自己给出的方案一致。"
        "请以这个身份的立场，独立给下面这一次改写打分。\n"
        "· C1 意图理解 / C2 改动正确 / C3 改动可解释 / C4 不过度改动 四项都要给分。\n"
        "打分前**先核对证据**：关注「改动的片段（按时间线序号）」与「总时长/片段数变化」是否自洽"
        "（例如只改速度时总时长不该变、只删片段时其余片段应前移）。\n"
        "1 分=完全不对；3 分=大致对但有偏差；5 分=完全符合。"
    ),
}

#: 第三个**对抗性检查**：不提分数，只负责找问题。
#: 这是"多 Agent 评分"里最有价值的一环——同一批模型容易互相印证，
#: 所以专门要一个只挑毛病的角色（本项目的 auditor 角色就是这个思路）。
ADVERSARY_PROMPT = (
    "你是剪辑工具的**审计角色**，立场是**专门挑毛病**，不负责安慰。"
    "给你一次「用户一句话 → 解析出的意图 → dry-run 预览 → 实际结果」的记录，"
    "请只列出**具体、可核对**的问题，例如：\n"
    "· 改动范围与用户说的不一致（多动/少动/动错对象）；\n"
    "· 预览与实际结果不自洽（预览说会改 N 个，结果改了别的数量）；\n"
    "· 该拒绝却执行了、或该执行却拒绝了；\n"
    "· 解释链断裂（用户看不懂为什么这么改）。\n"
    "没有真问题就明确回答「没有发现具体问题」，**不要为了凑数编问题**。"
)

#: 哪个 Agent 负责哪些评分项（**仅用于生成"职责视角"说明列**，不再用于拆分打分）。
AGENT_OF_CRITERION: dict[str, str] = {
    "C1": "requirements_agent",
    "C2": "structure_agent",
    "C3": "structure_agent",
    "C4": "requirements_agent",
    "C5": "rule",
}

TASKS = (
    "删掉第2个片段",
    "把所有片段速度调成1.5倍",
    "剪到10秒",
    "把画面调亮一点",
    "做成一个有感染力的集锦",
)


async def _judge_once(llm: Any, system_prompt: str, payload: str, schema: dict) -> dict:
    """一次评分调用（max_tokens 给足，见下方注释）。"""
    out = await llm.structured_output(
        system_prompt=system_prompt, user_prompt=payload, output_schema=schema,
        temperature=0, max_tokens=JUDGE_MAX_TOKENS[0], pipeline_id="")
    return out if isinstance(out, dict) else {}


#: 评分调用的 max_tokens 阶梯。**必须给够**，且空响应要重试：
#: 实测（deepseek-v4-pro，推理型）max_tokens=300 时推理会把预算吃光、接口返回
#: **200 + 空 content**（不是"模型没意见"，是额度不够）；1500 时大多数任务能出结果，
#: 但仍有约一半的长输入返回空——所以按阶梯重试，并把"用了多少 token"记进结果。
JUDGE_MAX_TOKENS: tuple[int, ...] = (1500, 4000)


async def _judge(llm: Any, system_prompt: str, payload: str,
                 codes: list[str]) -> tuple[dict[str, int], str, list[str]]:
    """一次 Judge 调用 → `({评分项: 分数}, 理由, 错误列表)`。

    max_tokens 给足并阶梯重试：实测（deepseek-v4-pro，推理型）max_tokens=300 时推理把预算
    吃光、接口返回 **200 + 空 content**（不是"模型没意见"，是额度不够）；1500 时仍有部分
    长输入被截断，所以按 1500 → 4000 重试。空响应与被截断的 JSON 都算"不可用"。
    """
    schema = {
        "type": "object",
        "properties": {
            **{c.lower(): {"type": "integer", "minimum": 1, "maximum": 5} for c in codes},
            "reason": {"type": "string"},
        },
        "required": [*[c.lower() for c in codes], "reason"],
    }
    errors: list[str] = []
    out: dict = {}
    for attempt, budget in enumerate(JUDGE_MAX_TOKENS):
        try:
            out = await llm.structured_output(
                system_prompt=(system_prompt + "\n\n只输出 JSON（不要 markdown 代码块），"
                               f"键名用 {'、'.join(codes)} 加一个 reason，"
                               "reason 用一句中文说明理由。"),
                user_prompt=payload, output_schema=schema,
                temperature=0, max_tokens=budget, pipeline_id="",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"max_tokens={budget}: {str(exc)[:80]}")
            break
        keys = {str(k).strip().lower() for k in out} if isinstance(out, dict) else set()
        usable = (isinstance(out, dict) and out and set(out) != {"content"}
                  and bool(keys & {c.lower() for c in codes}))
        if usable:
            break
        if attempt == len(JUDGE_MAX_TOKENS) - 1:
            reason_text = (str(out.get("content", ""))[:60]
                           if isinstance(out, dict) and out else "空响应")
            errors.append(f"不可用（max_tokens 已试到 {budget}）：{reason_text}")

    if not isinstance(out, dict) or set(out) == {"content"}:
        return {}, "", errors
    lowered = {str(k).strip().lower(): v for k, v in out.items()}
    scores: dict[str, int] = {}
    for code in codes:
        try:
            value = int(lowered.get(code.lower()) or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 5:
            scores[code] = value
    return scores, str(lowered.get("reason") or "").strip(), errors


def objective_cross_check(item: dict) -> list[str]:
    """**与客观事实交叉核对**——防止"没改却给 5 分"这类拍脑袋分。

    多 Agent 评分里最危险的不是两个模型吵架，而是**一起给高分的错误共识**。
    所以把可客观核对的事实单独拉出来对一遍：分数与事实矛盾时，把分数**下调**到
    事实允许的上限，并如实记录下调原因（不隐藏）。
    """
    fixes: list[str] = []
    cap: dict[str, int] = {}
    applied = bool(item.get("applied"))
    if not applied:
        # 没执行就没有"改动正确"可言；"不过度改动"倒是天然成立。
        cap["C2"] = 1
        cap["C3"] = min(cap.get("C3", 5), 3)
        fixes.append("未执行 → C2 上限 1（不存在「改得对」）")
    else:
        before, after = item.get("before"), item.get("after")
        cb, ca = item.get("clips_before"), item.get("clips_after")
        if item.get("targeted_only") is False:
            cap["C4"] = 2
            fixes.append("实际改动的片段超出目标集合 → C4 上限 2")
        # "时长与片段数都没变"只说明**可能**没改动，不能一概而论：
        # 调亮度/加转场这类改动**本来就不该**改时长与片段数（实测"把画面调亮一点"因此被误扣分）。
        # 所以判据加上"确实没有任何片段的内容被改过"这个前提。
        changed_any = bool(item.get("content_changed_ordinals")) \
            or bool(item.get("removed_clip_ids")) or bool(item.get("added_clip_ids"))
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) \
                and abs(float(after) - float(before)) < 1e-6 and cb == ca and not changed_any:
            cap["C2"] = min(cap.get("C2", 5), 3)
            fixes.append("时长与片段数都没变、也没有任何片段内容被改 → C2 上限 3（改动可能没发生）")
    for code, limit in cap.items():
        value = int((item.get("consensus") or {}).get(code, 0) or 0)
        if value > limit:
            item["consensus"][code] = limit
            fixes.append(f"共识分 {code} 由 {value} 下调到 {limit}")
    return fixes


async def score_with_agents(items: list[dict]) -> None:
    """**多 Agent 共识评分**：两名 Judge 独立打全部四项 + 一名对抗性审计 + 客观交叉核对。

    为什么这样比"一个模型自评"安全（三道防线）：

    1. **两名 Judge 各自独立打全部评分项**（不是各打一半）：同一份证据、两个不同职责视角；
       分歧大本身就是"这次改动的评价不稳定"的可读信号（记入 `spread`）。
    2. **第三个对抗性角色只挑毛病、不给分**：同批模型容易互相印证，
       所以要有一个专门找问题的角色（实测它确实指出了"改速度却不改时长"这类语义问题）。
    3. **与客观事实交叉核对后可**下调**分数**：多 Agent 最危险的不是吵架而是"一起给高分的错误共识"，
       所以"未执行却 C2=5""没变却 C2=5"这类分数会被压到事实允许的上限，并记录下调原因。
    """
    from clipwright.services.llm import LLMService

    llm = LLMService()
    codes = [c for c, _n, _q in CRITERIA if c != "C5"]      # C5 由规则判定
    for item in items:
        payload = json.dumps({
            "用户这句话": item["task"],
            "解析出的意图": item["intent"],
            "将改动什么（dry-run 预览）": item["dry_run"],
            # 把"改动的片段（按时间线序号）"单独给出来：**避免 Judge 靠文件名里的数字猜**
            # （实测踩过：它从 `m1.mp4` 推断成"删了第 1 个"，而实际删的是第 2 个）。
            # 标签必须写明"改动前"与"含被删除的"——否则读者会以为序号是改动后的，
            # 从而怀疑"第 2 个片段既被删又出现在列表里"是矛盾（实测审计角色就这么报了）。
            "改动的片段（按**改动前**的时间线顺序，含被删除的）":
                item.get("affected_ordinals") or [],
            "未执行原因": item["problems"],
            "是否执行": item["applied"],
            "总时长变化": (f"{item['before']}s → {item['after']}s"
                       if item["applied"] else "未执行"),
            "片段数变化": (f"{item['clips_before']} → {item['clips_after']}"
                       if item["applied"] else "未执行"),
        }, ensure_ascii=False, indent=2)

        per_judge: dict[str, dict[str, int]] = {}
        for judge, prompt in JUDGES.items():
            scores, reason, errors = await _judge(llm, prompt, payload, codes)
            if errors:
                item.setdefault("agent_errors", []).extend(f"{judge}: {e}" for e in errors)
            if not scores:
                continue
            per_judge[judge] = scores
            item.setdefault("judge_scores", {})[judge] = scores
            if reason:
                item.setdefault("agent_reasons", []).append(f"{judge}：{reason}")

        # ── 共识：逐项取均值；分歧度一并记录 ──
        consensus: dict[str, int] = {}
        spread: dict[str, int] = {}
        for code in codes:
            values = [s[code] for s in per_judge.values() if code in s]
            if not values:
                continue
            consensus[code] = int(round(sum(values) / len(values)))
            spread[code] = max(values) - min(values)
        item["consensus"] = consensus
        item["spread"] = spread
        if any(v >= 2 for v in spread.values()):
            item.setdefault("agent_reasons", []).append(
                "⚠ 两位 Judge 分歧 ≥2 分：" + "、".join(
                    f"{c}({spread[c]})" for c in spread if spread[c] >= 2))

        # ── 对抗性审计：只挑毛病 ──
        adv_schema = {
            "type": "object",
            "properties": {
                "problems": {"type": "array", "items": {"type": "string"}},
                "verdict": {"type": "string"},
            },
            "required": ["problems", "verdict"],
        }
        try:
            adv = await llm.structured_output(
                system_prompt=(ADVERSARY_PROMPT + "\n\n只输出 JSON："
                               '{"problems": ["..."], "verdict": "一句话结论"}'),
                user_prompt=payload, output_schema=adv_schema,
                temperature=0, max_tokens=JUDGE_MAX_TOKENS[-1], pipeline_id="")
        except Exception as exc:  # noqa: BLE001
            adv = {}
            item.setdefault("agent_errors", []).append(f"adversary: {str(exc)[:80]}")
        if isinstance(adv, dict) and set(adv) != {"content"}:
            problems = [str(p) for p in (adv.get("problems") or []) if str(p).strip()]
            verdict = str(adv.get("verdict") or "").strip()
            if problems:
                item["audit_problems"] = problems[:5]
            if verdict:
                item.setdefault("agent_reasons", []).append(f"审计角色：{verdict}")


def score_failure_honesty(items: list[dict]) -> None:
    """C5「失败时诚实」**由规则判定，不交给模型**。

    理由：这是可客观核对的事实——做不到的时候有没有把原因说出来；
    让模型评价自己"诚不诚实"是自证，没有信息量。
    判据：未执行 ⇒ 必须给出非空原因（problems 或 resolution.questions）；执行了 ⇒ 不适用，
    记满分（没有"欺骗"的机会）。
    """
    for item in items:
        if item["applied"]:
            item["scores"]["C5"] = 5
            item.setdefault("scorers", {})["C5"] = "rule:执行成功(不适用)"
            continue
        has_reason = bool(item["problems"])
        item["scores"]["C5"] = 5 if has_reason else 1
        item.setdefault("scorers", {})["C5"] = "rule:未执行是否说明原因"
        if not has_reason:
            item.setdefault("agent_reasons", []).append("规则：未执行且没给出任何原因")


async def build_sheet(material_dir: Path, *, fill_agents: bool = False) -> dict:
    from clipwright.services import content_index as ci
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services.intent_apply import plan_validate_apply

    entries = json.loads((material_dir / "manifest.json").read_text(encoding="utf-8"))
    materials = [material_dir / e["file"] for e in entries
                 if (material_dir / e["file"]).exists()]
    workdir = Path(tempfile.mkdtemp(prefix="clipwright_eval_"))
    ci.settings.library_dir = workdir / "library"
    ci.settings.project_dir = workdir / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False

    indexes = [await ci.build_index(m, force=True, mirror=False) for m in materials]
    clips, cursor = [], 0.0
    for idx in indexes:
        for i, shot in enumerate(idx.shots):
            dur = round(max(0.2, float(shot.duration_sec)), 3)
            clips.append(Clip(id=f"c{len(clips):03d}", kind="video",
                              asset_id=str(idx.asset_path), track_id="tr_v",
                              start_sec=round(cursor, 3), duration_sec=dur,
                              source_offset_sec=round(float(shot.t0), 3)))
            cursor += dur
    timeline = Timeline(id="tl_eval", fps=float(indexes[0].fps or 25.0),
                        width=int(indexes[0].width or 640),
                        height=int(indexes[0].height or 360),
                        tracks=[Track(id="tr_v", name="V1", kind="video", index=0,
                                      clips=clips)])

    items = []
    # 传一个"用户已选中前两个片段"的上下文：否则 `set` 类指令（"把画面调亮一点"）
    # 会因为"没有选中片段"而反问——那是**正确但会让评审误判为失败**的行为。
    # 评分表要评的是"有选中时它做得对不对"，所以这里显式给出选中。
    selection = [c.id for c in clips[:2]]
    order_before = [c.id for c in clips]
    for message in TASKS:
        preview = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                           selected_clip_ids=selection)
        result = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                          selected_clip_ids=selection, apply=True)
        # 客观事实字段：给 Judge 核对用，也给"客观交叉核对"用。
        # `affected_ordinals` 特别重要——**Judge 靠文件名里的数字猜**会猜错
        # （实测把 `m1.mp4` 读成"删了第 1 个"，而实际删的是第 2 个）。
        intended = set(selection)
        report = result.get("report") or {}
        patch_ops = {o.get("op") for o in (result.get("patch") or {}).get("ops", [])}
        affected = set(report.get("affected_clip_ids") or [])
        # "有没有过度改动"只看两类片段：**被删掉的**与**内容/属性真的被改过的**。
        # 收拢导致的位移不算——它是删除的必然后果（默认 ripple），算进去会让判断系统性误判
        # （实测：每次删除都被误扣分）。
        content_changed = set(report.get("content_changed_clip_ids") or [])
        check_set = set(report.get("removed_clip_ids") or []) | content_changed
        # 允许范围 = 用户选中 ∪ **R6 解析出的目标集合**。用解析结果而不是只看选中，
        # 是为了不误伤"删掉第2个片段"这种明确指名、但不在选中里的操作。
        resolved_target = set((preview.get("resolution") or {}).get("affected_clip_ids") or [])
        # `fit`（剪到 N 秒）与"所有片段"本身就是**全局指令**：按定义会动到选中之外的片段，
        # 不该算过度改动（实测"剪到10秒"被误扣分）。
        global_by_design = bool(patch_ops & {"fit"}) or message.startswith("把所有片段")
        targeted_only = global_by_design or (not check_set) \
            or check_set.issubset(intended | resolved_target)
        ordinals = [f"第{i + 1}个片段" for i, cid in enumerate(order_before) if cid in affected]
        items.append({
            "task": message,
            "intent": preview.get("intent") or "（未解析出）",
            "dry_run": (preview.get("report") or {}).get("preview") or [],
            "problems": [p.get("message") for p in (preview.get("problems") or [])],
            "applied": bool(result.get("applied")),
            "blocked": bool(result.get("blocked")),
            "before": (result.get("report") or {}).get("duration_before"),
            "after": (result.get("report") or {}).get("duration_after"),
            "clips_before": (result.get("report") or {}).get("clips_before"),
            "clips_after": (result.get("report") or {}).get("clips_after"),
            "affected_ordinals": ordinals,
            "content_changed_ordinals": [
                f"第{i + 1}个片段" for i, cid in enumerate(order_before) if cid in content_changed],
            "targeted_only": targeted_only,
            "removed_clip_ids": report.get("removed_clip_ids") or [],
            "added_clip_ids": report.get("added_clip_ids") or [],
            "scores": {code: 0 for code, _n, _q in CRITERIA},
            "notes": "",
        })
        if result.get("applied"):
            timeline = Timeline.model_validate(result["timeline"])

    # C5 由规则判定（不交模型自证）；C1–C4 由**两名 Judge 共识** + 客观核对
    score_failure_honesty(items)
    if fill_agents:
        await score_with_agents(items)
        for item in items:
            # 共识分并入 C5（规则分），形成完整的五项评分
            item["consensus"] = {**(item.get("consensus") or {}),
                                 "C5": (item.get("scores") or {}).get("C5", 0)}
            fixes = objective_cross_check(item)
            if fixes:
                item["rule_adjustments"] = fixes
            item["consensus"].setdefault("C5", (item.get("scores") or {}).get("C5", 0))
    return {"materials": [m.name for m in materials], "items": items,
            "agent_filled": bool(fill_agents)}


def render_markdown(payload: dict) -> str:
    agent_filled = bool(payload.get("agent_filled"))
    lines = [
        "# 「一句话 → 成片」评分表",
        "",
        f"素材：{len(payload['materials'])} 段（{'、'.join(payload['materials'][:4])}…）",
        "",
        "## 这张表里有两种分数，**不要混着读**",
        "",
        "| 来源 | 谁打的 | 能说明什么 |",
        "|------|--------|-----------|",
        "| `consensus_scores` | **需求 Agent + 结构 Agent 各自独立打分后的共识**（逐项均值），"
        "另附 `judge_scores`（两家的原始分）与 `spread`（分歧度） | "
        "**这就是本项目的主观评分口径**（多 Agent 跨角色评分）|",
        "| `audit_problems` | 第三个**对抗性角色**，只挑毛病不给分 | "
        "同批模型容易互相印证，所以要有人专门找问题 |",
        "| `rules` | 客观事实交叉核对（未执行却给 C2 高分等会被**下调**并记录） | "
        "防「一起给高分的错误共识」 |",
        "| `scores` | 人工列（可选，留空不影响）| 若要人工复核，在下面表格填 1–5 |",
        "",
        "C5（失败时诚实）**不打模型分**：它是可客观核对的事实（做不到时有没有说明原因），"
        "由脚本按规则判——让模型评价自己诚不诚实是自证。",
        "",
        ("本次已由两名 Judge + 审计角色填过 `consensus_scores`。"
         if agent_filled else
         "本次**未**做 Agent 评分（加 `--fill-agents` 让两名 Judge 与审计角色评分）。"),
        "",
        "（表中任务在「用户已选中前两个片段」的上下文里执行；未选中时 `set` 类指令会先反问，",
        "那是正确行为，不该算失败。）",
        "",
        "评分项：" + "；".join(f"**{c}** {n}——{q}" for c, n, q in CRITERIA),
        "",
    ]
    for i, item in enumerate(payload["items"], 1):
        consensus = item.get("consensus_scores") or {}
        spreads = item.get("spread") or {}
        judge_scores = item.get("judge_scores") or {}
        consensus_cells = " | ".join(
            ((f"{consensus[c]}" + (f"（分歧 {spreads[c]}）" if spreads.get(c) else ""))
             if c in consensus else "—")
            for c, _n, _q in CRITERIA)
        lines += [
            f"## 任务 {i}：`{item['task']}`",
            "",
            f"- **解析出的意图**：{item['intent'].splitlines()[0] if item['intent'] else '—'}",
            f"- **dry-run 预览**：{'；'.join(item['dry_run'][:4]) or '—'}",
            f"- **将改动（按时间线序号）**：{'、'.join(item.get('affected_ordinals') or []) or '—'}",
            f"- **未执行原因**：{'；'.join(item['problems']) or '（无）'}",
            f"- **实际结果**：{'已执行' if item['applied'] else '未执行'}"
            + (f"，总时长 {item['before']}s → {item['after']}s，"
               f"片段 {item['clips_before']} → {item['clips_after']} 个"
               if item["applied"] else ""),
            "",
            "**多 Agent 共识分**（需求 Agent + 结构 Agent 独立打分后取均值；C5 为规则判定）：",
            "",
            "| " + " | ".join(c for c, _n, _q in CRITERIA) + " |",
            "|" + "---|" * len(CRITERIA),
            "| " + consensus_cells + " |",
            "",
        ]
        if judge_scores:
            names = "、".join(judge_scores)
            lines += [f"**两家原始分**（{names}）：", ""]
            for judge, scores in judge_scores.items():
                lines.append(f"- {judge}："
                             + "、".join(f"{c}={scores.get(c, '—')}" for c, _n, _q in CRITERIA))
            lines.append("")
        reasons = item.get("agent_reasons") or []
        if reasons:
            lines += ["**理由**：", ""] + [f"- {r}" for r in reasons] + [""]
        if item.get("audit_problems"):
            lines += ["**对抗性审计列出的问题**：", ""] \
                + [f"- {p}" for p in item["audit_problems"]] + [""]
        if item.get("rule_adjustments"):
            lines += ["**客观核对下调**：" + "；".join(item["rule_adjustments"]), ""]
        if item.get("agent_errors"):
            lines += ["**Agent 打分失败**：" + "；".join(item["agent_errors"]), ""]
        lines += [
            "**人工复核（可选，填 1–5）**：",
            "",
            "| " + " | ".join(c for c, _n, _q in CRITERIA) + " | 理由 |",
            "|" + "---|" * (len(CRITERIA) + 1),
            "| " + " | ".join("0" for _ in CRITERIA) + " |  |",
            "",
        ]
    return "\n".join(lines)


def score(payload: dict) -> int:
    """汇总：**人工分与 Agent 分分开报**，绝不混在一起冒充"人评"。"""

    def _collect(key: str) -> dict[str, list[float]]:
        totals: dict[str, list[float]] = {c: [] for c, _n, _q in CRITERIA}
        for item in payload["items"]:
            for code, value in (item.get(key) or {}).items():
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    continue
                if v > 0:
                    totals.setdefault(code, []).append(v)
        return totals

    human = _collect("scores")
    consensus = _collect("consensus_scores")

    print("=" * 66)
    print("评分汇总（1–5 分；0/未填不计入）")
    print("=" * 66)

    def _report(title: str, totals: dict[str, list[float]], gate: bool) -> tuple[int, float]:
        print(f"\n【{title}】")
        flat: list[float] = []
        for code, name, question in CRITERIA:
            values = totals.get(code) or []
            if not values:
                print(f"  {code} {name:6s}：未评分（{question}）")
                continue
            avg = sum(values) / len(values)
            flat.extend(values)
            print(f"  {code} {name:6s}：{avg:.2f} / 5（{len(values)} 项）")
        if not flat:
            return 0, 0.0
        avg_all = sum(flat) / len(flat)
        print(f"  → 总体：{avg_all:.2f} / 5（{len(flat)} 个评分）")
        if gate:
            print("    验收门槛「≥ 4/5」——" + ("**达标**" if avg_all >= 4 else "**未达标**"))
        return len(flat), avg_all

    # 口径（按项目方确认）：**多 Agent 跨角色共识**即本项目的主观评分；
    # 人工列保留为可选复核，不参与门槛判定。
    n_cons, avg_cons = _report(
        "多 Agent 共识评分（需求 Agent + 结构 Agent 独立打分后取均值；R10 主观指标用这一份）",
        consensus, gate=True)
    n_human, avg_human = _report("人工复核（可选，不参与门槛）", human, gate=False)

    # 分歧度与被下调的项单独提示：这两类是"分数不可全信"的信号
    wide = [(it["task"], it.get("spread") or {}) for it in payload["items"]
            if any(v >= 2 for v in (it.get("spread") or {}).values())]
    adjusted = [(it["task"], it.get("rule_adjustments") or []) for it in payload["items"]
                if it.get("rule_adjustments")]
    audited = [(it["task"], it.get("audit_problems") or []) for it in payload["items"]
               if it.get("audit_problems")]

    print("\n" + "-" * 66)
    if wide:
        print(f"分歧 ≥2 分的任务（{len(wide)} 个，评价不稳定，建议人工复核）：")
        for task, spread in wide:
            print(f"  · {task}：" + "、".join(f"{c} 差 {v}" for c, v in spread.items() if v >= 2))
    if adjusted:
        print(f"被客观核对下调的任务（{len(adjusted)} 个）：")
        for task, fixes in adjusted:
            print(f"  · {task}：" + "；".join(fixes))
    if audited:
        print(f"审计角色列出具体问题的任务（{len(audited)} 个）：")
        for task, problems in audited:
            print(f"  · {task}：" + "；".join(problems[:2]))
    if n_cons:
        print(f"\n共识评分覆盖 {n_cons} 项，均分 {avg_cons:.2f}"
              + (f"；人工复核 {n_human} 项，均分 {avg_human:.2f}" if n_human else
                 "（人工列未填，按项目方确认不影响门槛）"))
    return 0 if avg_cons >= 4 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/pexels_real")
    ap.add_argument("--out", default="eval/eval_sheet.md")
    ap.add_argument("--score", default="", help="给已有的评分结果 json 做汇总")
    ap.add_argument("--fill-agents", action="store_true",
                    help="两名 Judge（需求/结构）独立打分 + 审计角色挑毛病 + 客观核对")
    args = ap.parse_args()

    if args.score:
        payload = json.loads(Path(args.score).read_text(encoding="utf-8"))
        return score(payload)

    material_dir = Path(args.dir).resolve()
    if not (material_dir / "manifest.json").exists():
        print(f"找不到 manifest：{material_dir}（先跑 fetch_pexels_testset.py）")
        return 2
    payload = asyncio.run(build_sheet(material_dir, fill_agents=args.fill_agents))
    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    out_json = out_md.with_suffix(".json")
    payload_for_file = dict(payload)
    for item in payload_for_file["items"]:
        # 共识分落盘；人工列留空（可选复核）
        item["consensus_scores"] = dict(item.get("consensus") or {})
        item["scores"] = {code: 0 for code, _n, _q in CRITERIA}
    out_json.write_text(json.dumps(payload_for_file, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"评分表已生成：{out_md}")
    print(f"评分数据：{out_json}")
    if args.fill_agents:
        print("Agent 分已写入 agent_scores；人工列仍为 0，等你填。")
    print("提示：Agent 分是**自动化角色判断**，不能替代 R10 要的真人主观评分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
