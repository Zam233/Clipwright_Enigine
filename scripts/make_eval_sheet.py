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

#: 哪个 Agent 负责哪些评分项——**按职责分工**，不是随便指派：
#: · 需求 Agent（RequirementsAgent）负责"用户要的是什么"这一侧：C1 意图理解、C4 不过度改动；
#: · 结构 Agent（StructureAgent）负责"时间线改得对不对"这一侧：C2 改动正确、C3 改动可解释；
#: · C5（失败时诚实）**不打模型分**——它是可客观判定的事实（有没有如实说明原因），
#:   由脚本按规则判，避免"让模型评价自己诚不诚实"这种自证。
AGENT_OF_CRITERION: dict[str, str] = {
    "C1": "requirements_agent",
    "C4": "requirements_agent",
    "C2": "structure_agent",
    "C3": "structure_agent",
}

AGENT_ROLE_PROMPT: dict[str, str] = {
    "requirements_agent": (
        "你是剪辑工具里的**需求 Agent**，职责是把用户的一句话变成明确的修改诉求，"
        "并守住「只动用户要动的东西」。请你以这个身份的立场评价下面这一次改写。\n"
        "· C1 意图理解：解析出的意图是否就是用户那句话的意思（有没有漏、有没有加戏）？\n"
        "· C4 不过度改动：有没有动到用户没让它动的东西？\n"
        "1 分=完全不对；3 分=大致对但有偏差；5 分=完全符合。"
    ),
    "structure_agent": (
        "你是剪辑工具里的**结构 Agent**，职责是保证时间线结构合理、改动与自己给出的方案一致。"
        "请你以这个身份的立场评价下面这一次改写。\n"
        "· C2 改动正确：时间线的实际变化是否符合诉求本身（数量/范围/结果）？\n"
        "· C3 改动可解释：从「意图 → dry-run 预览 → 实际结果」这条链能否看懂它为什么这么改？\n"
        "1 分=完全不对；3 分=大致对但有偏差；5 分=完全符合。"
    ),
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


async def score_with_agents(items: list[dict]) -> None:
    """让**需求 Agent** 与**结构 Agent** 按各自职责视角给评分项打分（就地写入 items）。

    为什么分两个 Agent 而不是一个：C1/C4 是"用户要什么"的问题，C2/C3 是"时间线改得对不对"
    的问题——这正是需求 Agent 与结构 Agent 各自的职责。**两个视角分开打分**比一个模型
    一锅端更有信息量（两边不一致本身就是有价值的信号）。

    诚实的边界（必须写在结果里）：这是**自动化的角色判断**，不是 R10 验收指标要的
    "真人主观评分"。所以脚本会把 `scorer` 记为 `<agent>(llm)`，并在汇总时**分开报**：
    人工分（若有人填）与 Agent 分各算各的，绝不混在一起冒充"人评≥4/5"。
    """
    from clipwright.services.llm import LLMService

    llm = LLMService()
    for item in items:
        payload = json.dumps({
            "用户这句话": item["task"],
            "解析出的意图": item["intent"],
            "dry-run 预览": item["dry_run"],
            "未执行原因": item["problems"],
            "是否执行": item["applied"],
            "总时长变化": f"{item['before']}s → {item['after']}s" if item["applied"] else "未执行",
            "片段数变化": (f"{item['clips_before']} → {item['clips_after']}"
                       if item["applied"] else "未执行"),
        }, ensure_ascii=False, indent=2)

        for agent, criteria in AGENT_ROLE_PROMPT.items():
            codes = [c for c, a in AGENT_OF_CRITERION.items() if a == agent]
            schema = {
                "type": "object",
                "properties": {
                    **{c.lower(): {"type": "integer", "minimum": 1, "maximum": 5} for c in codes},
                    "reason": {"type": "string"},
                },
                "required": [*[c.lower() for c in codes], "reason"],
            }
            out: dict = {}
            for attempt, budget in enumerate(JUDGE_MAX_TOKENS):
                try:
                    out = await llm.structured_output(
                        system_prompt=(criteria + "\n\n只输出 JSON（不要 markdown 代码块），"
                                       f"键名用 {'、'.join(codes)} 加一个 reason，"
                                       "reason 用一句中文说明理由。"),
                        user_prompt=payload, output_schema=schema,
                        # **max_tokens 必须给够**：实测（deepseek-v4-pro，推理型）max_tokens=300 时
                        # 推理过程会把预算吃光，接口返回 **200 + 空 content**——
                        # 那不是"模型没意见"，是额度不够（见 llm.py 里新加的空内容告警）。
                        temperature=0, max_tokens=budget, pipeline_id="",
                    )
                except Exception as exc:  # noqa: BLE001 — 打分失败不该让整张表作废
                    item.setdefault("agent_errors", []).append(
                        f"{agent}(max_tokens={budget}): {str(exc)[:80]}")
                    break
                usable = (isinstance(out, dict) and out and set(out) != {"content"}
                          and any(str(k).strip().lower() in {c.lower() for c in codes} | {"reason"}
                                  for k in out))
                if usable:
                    if attempt:
                        item.setdefault("agent_notes", []).append(
                            f"{agent}: 前 {attempt} 次不可用，max_tokens={budget} 才出结果")
                    break
                # 空响应 **和** 被截断的 JSON 都要重试：`structured_output` 在 JSON 解析失败时
                # 返回 `{"content": 原文}`，而原文被 max_tokens 截断是常见形态
                # （实测 reason 写到一半就断了）——那不是"模型答错了"，是额度不够。
                if attempt == len(JUDGE_MAX_TOKENS) - 1:
                    reason_text = (str(out.get("content", ""))[:60]
                                   if isinstance(out, dict) and out else "空响应")
                    item.setdefault("agent_errors", []).append(
                        f"{agent}: 不可用（max_tokens 已试到 {budget}）：{reason_text}——未打分")
            if not isinstance(out, dict) or not out:
                continue
            if set(out) == {"content"}:
                item.setdefault("agent_errors", []).append(
                    f"{agent}: 返回的不是 JSON（{str(out['content'])[:60]}）")
                continue
            # 键名大小写不敏感：实测模型会返回 `C1`/`C4` 而不是小写
            lowered = {str(k).strip().lower(): v for k, v in out.items()}
            scored_any = False
            for code in codes:
                try:
                    value = int(lowered.get(code.lower()) or 0)
                except (TypeError, ValueError):
                    continue
                if 1 <= value <= 5:
                    item["scores"][code] = value
                    item.setdefault("scorers", {})[code] = f"{agent}(llm)"
                    scored_any = True
            if not scored_any:
                item.setdefault("agent_errors", []).append(
                    f"{agent}: 没有可识别的分数（返回键 {sorted(out)}）")
            reason = str(lowered.get("reason") or "").strip()
            if reason:
                item["agent_reasons"] = (item.get("agent_reasons") or [])
                item["agent_reasons"].append(f"{agent}：{reason}")


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
    for message in TASKS:
        preview = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                           selected_clip_ids=selection)
        result = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                          selected_clip_ids=selection, apply=True)
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
            "clips_after": (result.get("report") or {}).get("clips_clips_after")
            or (result.get("report") or {}).get("clips_after"),
            "scores": {code: 0 for code, _n, _q in CRITERIA},
            "notes": "",
        })
        if result.get("applied"):
            timeline = Timeline.model_validate(result["timeline"])

    # C5 由规则判定（不交模型自证）；C1/C2/C3/C4 由两个 Agent 按职责打分
    score_failure_honesty(items)
    if fill_agents:
        await score_with_agents(items)
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
        "| `agent_scores` | **需求 Agent**（C1 意图理解 / C4 不过度改动）与 "
        "**结构 Agent**（C2 改动正确 / C3 改动可解释），按各自职责视角判 | "
        "自动化角色判断：能发现「两边看法不一致」这类信号 |",
        "| `scores` | **人**（在下面表格的 0 处填 1–5） | 这才是 R10 指标要的"
        "「主观 ≥ 4/5」——主观评分必须真人来打，模型自评不算数 |",
        "",
        "C5（失败时诚实）**不打模型分**：它是可客观核对的事实（做不到时有没有说明原因），"
        "由脚本按规则判——让模型评价自己诚不诚实是自证。",
        "",
        ("本次已由 Agent 填过 `agent_scores`；人工列仍是 0，等你填。"
         if agent_filled else
         "本次**未**由 Agent 打分（加 `--fill-agents` 可让两个 Agent 填写）。"),
        "",
        "（表中任务在「用户已选中前两个片段」的上下文里执行；未选中时 `set` 类指令会先反问，",
        "那是正确行为，不该算失败。）",
        "",
        "评分项：" + "；".join(f"**{c}** {n}——{q}" for c, n, q in CRITERIA),
        "",
    ]
    for i, item in enumerate(payload["items"], 1):
        agent_scores = item.get("agent_scores") or {}
        scorers = item.get("scorers") or {}
        agent_cells = " | ".join(
            (f"{agent_scores[c]}（{scorers.get(c, '')}）" if c in agent_scores else "—")
            for c, _n, _q in CRITERIA)
        lines += [
            f"## 任务 {i}：`{item['task']}`",
            "",
            f"- **解析出的意图**：{item['intent'].splitlines()[0] if item['intent'] else '—'}",
            f"- **dry-run 预览**：{'；'.join(item['dry_run'][:4]) or '—'}",
            f"- **未执行原因**：{'；'.join(item['problems']) or '（无）'}",
            f"- **实际结果**：{'已执行' if item['applied'] else '未执行'}"
            + (f"，总时长 {item['before']}s → {item['after']}s，"
               f"片段 {item['clips_before']} → {item['clips_after']} 个"
               if item["applied"] else ""),
            "",
            f"**Agent 视角**（需求 Agent 管 C1/C4，结构 Agent 管 C2/C3，C5 为规则判定）：",
            "",
            "| " + " | ".join(c for c, _n, _q in CRITERIA) + " |",
            "|" + "---|" * len(CRITERIA),
            "| " + agent_cells + " |",
            "",
        ]
        reasons = item.get("agent_reasons") or []
        if reasons:
            lines += ["**Agent 给的理由**：", ""] + [f"- {r}" for r in reasons] + [""]
        if item.get("agent_errors"):
            lines += ["**Agent 打分失败**：" + "；".join(item["agent_errors"]), ""]
        lines += [
            "**人类评审（请填 1–5，0 = 未评）**：",
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
    agent = _collect("agent_scores")

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

    n_human, avg_human = _report("人工评审（R10 指标认这一份）", human, gate=True)
    n_agent, avg_agent = _report("Agent 角色判断（自动化，不能替代人工）", agent, gate=False)

    print("\n" + "-" * 66)
    if n_human == 0:
        print("人工评审还没有任何分数——R10 的「主观 ≥ 4/5」**仍未达成**。")
        print("请在 eval_sheet.md 的「人类评审」表格里把 0 改成 1–5（Agent 分已经填好，可作参照）。")
    if n_agent:
        print(f"Agent 分：{n_agent} 个，均分 {avg_agent:.2f}；人工分：{n_human} 个"
              + (f"，均分 {avg_human:.2f}" if n_human else ""))
    return 0 if n_human else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/pexels_real")
    ap.add_argument("--out", default="eval/eval_sheet.md")
    ap.add_argument("--score", default="", help="给已有的评分结果 json 做汇总")
    ap.add_argument("--fill-agents", action="store_true",
                    help="让需求 Agent 与结构 Agent 按各自职责视角填写 C1–C4（C5 由规则判）")
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
        item["agent_scores"] = {c: v for c, v in (item.get("scores") or {}).items() if v}
        item["scores"] = {code: 0 for code, _n, _q in CRITERIA}   # 人工列留空等填
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
