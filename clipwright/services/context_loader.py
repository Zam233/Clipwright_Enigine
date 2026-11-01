"""决策上下文装载（PRD §17.6 / §17.11）。

**唯一入口**：主 agent 每跳决策前都从这里拿上下文，禁止别处再拼 prompt。
装载五类来源，按优先级从后往前裁剪（超预算时先丢最低优先级的细节）：

    ① docs/brief.md 摘要 + docs/plan.md 场景表
    ② 已有产物 preview
    ③ 最近 3 份报告的「结果如何 / 给下一位」
    ④ 上一步 failure 全文（由调用方传入）
    ⑤ 剩余预算/墙钟/回合（由调用方传入）

返回 `{text, sources[], trimmed[], bytes}`，调用方负责 emit `context_loaded`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.config import logger, settings

DEFAULT_BUDGET_CHARS = 60000
#: 各段默认上限（字符）
LIMITS = {"brief": 400, "plan": 1400, "artifacts": 600, "reports": 1800,
          "failure": 900, "script": 2000}


def _budget() -> int:
    return int(getattr(settings, "context_budget_chars", DEFAULT_BUDGET_CHARS)
               or DEFAULT_BUDGET_CHARS)


def _workspace_root(task: Any) -> Path | None:
    try:
        request = getattr(task, "request", task)
        extra = dict(getattr(request, "extra", {}) or {})
        root = str(extra.get("workspace_root") or "")
        if root and Path(root).is_dir():
            return Path(root)
        from clipwright.services.workspace import Workspace

        ws = Workspace(str(getattr(task, "id", "") or ""))
        if ws.exists:
            return ws.dirs().root
    except Exception:  # noqa: BLE001 — 拿不到工作区就退化为无文档上下文
        return None
    return None


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit] if limit > 0 else text


def _scenes_table(plan_md: str, limit: int) -> str:
    """从 plan.md 里抽场景表（优先 `## scenes`/表格；否则整篇截断）。"""
    if not plan_md:
        return ""
    for marker in ("## scenes", "## 场景", "## 分镜"):
        idx = plan_md.find(marker)
        if idx >= 0:
            return plan_md[idx: idx + limit]
    return plan_md[:limit]


def load(task: Any, *, failure: dict[str, Any] | None = None,
         budget_note: str = "") -> dict[str, Any]:
    """装载上下文；返回 `{text, sources, trimmed, bytes}`。"""
    budget = _budget()
    sources: list[dict[str, Any]] = []
    trimmed: list[str] = []
    parts: list[tuple[str, str, int]] = []  # (key, text, priority) 数字越小越优先保留

    root = _workspace_root(task)

    # ① 需求文档
    brief_txt = plan_txt = ""
    if root is not None:
        brief_txt = _read(root / "docs" / "brief.md", LIMITS["brief"])
        plan_full = _read(root / "docs" / "plan.md", 20000)
        plan_txt = _scenes_table(plan_full, LIMITS["plan"])
        sources.append({"name": "docs/brief.md", "bytes": len(brief_txt),
                        "missing": not brief_txt})
        sources.append({"name": "docs/plan.md", "bytes": len(plan_txt),
                        "missing": not plan_txt})

    # ①0 本轮用户指令（续跑场景最高优先级：用户刚说的这句必须排在最前面）
    instr_txt = _instruction_digest(task)
    if instr_txt:
        parts.append(("instruction", "## 本轮用户指令（最高优先级）\n" + instr_txt, 0))
        sources.append({"name": "user_instruction", "bytes": len(instr_txt),
                        "missing": False})

    # ①0b 会话对话 + 上一轮执行轨迹（工具 / 技能 / 子 agent）
    conv_txt, run_txt, sess_sources = _session_digest(task)
    for entry in sess_sources:
        sources.append(entry)
    if conv_txt:
        parts.append(("conversation", "## 会话对话（最近，越靠后越新）\n" + conv_txt, 1))
    if run_txt:
        parts.append((
            "prev_run",
            "## 上一轮执行轨迹（派过哪些子 agent、调过哪些工具/技能、降级与汇报）\n"
            + run_txt,
            3,
        ))
    if brief_txt:
        parts.append(("brief", "## 需求简报\n" + brief_txt, 1))
    if plan_txt:
        parts.append(("plan", "## 制作规划书（场景表）\n" + plan_txt, 2))
    # 不静默继续（PRD M23）：需求文档缺失必须显式写进上下文，让主 agent 有机会先补需求
    missing_docs = [n for n in ("brief", "plan")
                    if not (brief_txt if n == "brief" else plan_txt)]
    if missing_docs:
        parts.append((
            "missing_docs",
            "## ⚠ 需求文档缺失\n"
            f"docs/{'、docs/'.join(f'{n}.md' for n in missing_docs)} 不存在。"
            "可以先用需求对话补齐，或确认按当前简报继续（你的选择会被记录）。",
            0,
        ))

    # ①b 文案 artifact（B8）：全文**不塞进上下文**，只给「路径 + 全长 + sha256 + 前 N 字」。
    # 这样 10k 字脚本不会把其他段挤没，子 agent 要全文时按路径读并校验 hash。
    script_txt, script_src = _script_digest(task, root)
    if script_src is not None:
        sources.append(script_src)
    if script_txt:
        parts.append(("script", "## 文案（artifact）\n" + script_txt, 1))

    # ② 产物 preview
    arts = _artifacts_preview(task, root)
    if arts:
        parts.append(("artifacts", "## 已有产物\n" + arts, 3))
        sources.append({"name": "artifacts", "bytes": len(arts), "missing": False})

    # ②b 记忆召回（PRD §2.5 / §5.5 M4）：语义记忆 + 本任务情景记忆，带来源
    mem_txt, mem_refs = _memory_digest(task)
    if mem_txt:
        parts.append(("memory", "## 相关记忆（含来源）\n" + mem_txt, 3))
        sources.append({"name": "memory", "bytes": len(mem_txt), "missing": False,
                        "recall": mem_refs})

    # ③ 最近报告
    if root is not None:
        reports_txt, n_reports = _reports_digest(root, LIMITS["reports"])
        if reports_txt:
            parts.append(("reports", "## 最近报告（子 agent 写给主 agent 的信）\n" + reports_txt, 4))
            sources.append({"name": "reports", "bytes": len(reports_txt),
                            "missing": False, "count": n_reports})

    # ④ 上一步失败
    if failure:
        txt = _failure_text(failure, LIMITS["failure"])
        if txt:
            parts.append(("failure", "## 上一步失败\n" + txt, 5))
            sources.append({"name": "failure", "bytes": len(txt), "missing": False})

    # ⑤ 预算/墙钟
    if budget_note:
        parts.append(("limits", "## 剩余额度\n" + budget_note, 6))

    # —— 溢出裁剪：保留优先级高的段，低优先级截断并记录 ——
    parts.sort(key=lambda p: p[2])
    out: list[str] = []
    used = 0
    for key, text, _prio in parts:
        remain = budget - used
        if remain <= 120:
            trimmed.append(key)
            continue
        if len(text) > remain:
            out.append(text[:remain] + "\n…（已截断）")
            trimmed.append(key)
            used = budget
        else:
            out.append(text)
            used += len(text)

    text = "\n\n".join(out).strip() or "（无历史上下文）"
    return {"text": text, "sources": sources, "trimmed": trimmed,
            "bytes": len(text), "budget": budget}


def _script_digest(task: Any, root: Path | None) -> tuple[str, dict[str, Any] | None]:
    """文案 artifact 摘要（B8）：`(上下文片段, sources 条目)`。

    优先用 `extra["script_path"]`（proceed 时落的 `docs/script.md`），否则回落到
    `<workspace>/docs/script.md`。内容只取前 `LIMITS["script"]` 字，并如实标出
    `truncated / chars / sha256`——调用方据此知道"是不是全文"。
    """
    path: Path | None = None
    try:
        request = getattr(task, "request", task)
        extra = dict(getattr(request, "extra", {}) or {})
        raw = str(extra.get("script_path") or "")
        if raw:
            path = Path(raw)
    except Exception:  # noqa: BLE001
        path = None
    if path is None and root is not None:
        candidate = root / "docs" / "script.md"
        if candidate.is_file():
            path = candidate
    if path is None or not path.is_file():
        return "", None

    try:
        full = path.read_text(encoding="utf-8")
    except OSError:
        return "", {"name": "script", "bytes": 0, "missing": True, "path": str(path)}

    import hashlib

    limit = int(LIMITS.get("script") or 2000)
    body = full[:limit]
    truncated = len(full) > limit
    src = {
        "name": "script",
        "bytes": len(body),
        "chars": len(full),
        "sha256": hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "path": str(path),
        "truncated": truncated,
        "missing": False,
    }
    head = (
        f"- 文件：{path}\n"
        f"- 全文长度：{len(full)} 字（sha256={src['sha256'][:16]}…）\n"
        f"- 下面是前 {len(body)} 字{'（已截断，需要全文请按 input_refs 读取该文件）' if truncated else ''}\n\n"
    )
    return head + body, src


def _memory_digest(task: Any) -> tuple[str, list[dict[str, Any]]]:
    """记忆召回摘要：返回 `(文本, 召回证据)`。

    召回证据要进 `context_loaded`——"模型为什么这么决定"必须能追到具体条目，
    不能只有一句"参考了历史经验"。
    """
    task_id = str(getattr(task, "id", "") or "")
    try:
        request = getattr(task, "request", task)
        topic = str(getattr(request, "topic", "") or "")
    except Exception:  # noqa: BLE001
        topic = ""
    try:
        from clipwright.services import memory

        hits = memory.recall(topic or "创作", task_id=task_id, k=4)
    except Exception as exc:  # noqa: BLE001 — 记忆不可用不能阻断装载
        logger.warning("记忆召回失败: %s", exc)
        return "", []
    if not hits:
        return "", []
    lines: list[str] = []
    refs: list[dict[str, Any]] = []
    for hit in hits:
        prov = hit.get("provenance") or {}
        src = prov.get("source") or hit.get("layer")
        lines.append(f"- [{hit.get('layer')}/{hit.get('kind') or 'fact'}] "
                     f"{str(hit.get('text'))[:200]}（来源：{src}）")
        refs.append({"id": hit.get("id"), "layer": hit.get("layer"),
                     "score": hit.get("score"), "source": src})
    return "\n".join(lines), refs


def _artifacts_preview(task: Any, root: Path | None) -> str:
    lines: list[str] = []
    board_names: list[str] = []
    try:
        from clipwright.services import task_blackboard as bb

        board = bb.get(str(getattr(task, "id", "") or ""))
        board_names = list(board)
        for name, value in list(board.items())[:12]:
            if isinstance(value, dict):
                preview = {k: value[k] for k in list(value)[:3]}
            elif isinstance(value, list):
                preview = f"{len(value)} 项"
            else:
                preview = str(value)[:80]
            lines.append(f"- {name}: {str(preview)[:160]}")
        # 时间线里可能是 **远程 URL 资产**（Pexels 等图库直链）：渲染期由 ffmpeg
        # 按 URL 取流，不出现在 materials/ 里。不说明白模型会以为"整条片子的素材
        # 根本不存在"，然后把回合全花在反复下载/校验上（E2E 2026-10-30 实测
        # 160 回合耗尽）。所以这里显式给出"远程/本地"分布。
        _tl = board.get("final_timeline") or board.get("timeline")
        if isinstance(_tl, dict):
            remote = local = 0
            for track in _tl.get("tracks") or []:
                for clip in track.get("clips") or []:
                    asset = str(clip.get("asset_id") or "")
                    if not asset:
                        continue
                    if asset.startswith(("http://", "https://")):
                        remote += 1
                    else:
                        local += 1
            if remote or local:
                note = (f"- 时间线资产：本地 {local} 条"
                        + (f"、远程 {remote} 条（http(s) 直链，渲染时自动取流，"
                           f"**不算素材缺失**，不要再重复下载）" if remote else ""))
                lines.append(note)
    except Exception:  # noqa: BLE001
        pass
    # 黑板为空时**不能**拿磁盘文件冒充黑板内容（E2E 2026-10-30 真实事故）：
    # 模型看到 workspace 里有 art_*.json 就以为产物可用，直接派 edit/material，
    # 结果 48 次调用全部"缺输入"并耗尽回合闸。必须把"黑板空"和"磁盘有文件"
    # 这两件事分开说，并给出可执行动作。
    if not lines and root is not None:
        art_dir = root / "artifacts"
        disk: list[str] = []
        if art_dir.is_dir():
            for p in sorted(art_dir.glob("*"))[:12]:
                disk.append(f"- {p.name}（{p.stat().st_size} 字节，仅磁盘文件）")
        if disk:
            lines.append("黑板当前为空：以下文件只在磁盘上，**不在黑板**，阶段子 agent 读不到：")
            lines.extend(disk)
            lines.append(
                "正确做法：要么先用 read_artifact 读入并用 `inputs` 显式传（形如 "
                "{\"timeline\": {\"artifact\": \"art_xxx\", \"path\": \"...\"}}），"
                "要么先派 `structure` 产出 script_skeleton/scenes 再往下走。"
                "直接派 edit/material 一定会因缺输入失败。"
            )
        else:
            lines.append("黑板当前为空（无产物、无磁盘 artifact）：先派 structure。")
    # 状态快照（2026-10-30）：把"磁盘上有什么产物 / 派过哪些子 agent"直接摆出来。
    # 现场：模型每轮都重新 list_dir/read_artifact 摸底（一次任务 121/161 次），
    # 回合全花在"确认现状"上。已知的现状就写在上下文里，别让它去问。
    try:
        from clipwright.services import agent_run as _agent_run

        runs = _agent_run.list_runs(str(getattr(task, "id", "") or ""))
    except Exception:  # noqa: BLE001 — run 表不可用就跳过
        runs = []
    if runs:
        brief_runs = []
        for r in runs[-8:]:
            brief_runs.append(
                f"{r.get('stage') or r.get('role')}#{r.get('attempt')}"
                f" {r.get('status')}"
                + (f"(降级{len(r.get('degradations') or [])})"
                   if r.get("degradations") else ""))
        lines.append("- 已派发的子 agent：" + " / ".join(brief_runs))
    if root is not None:
        art_dir = root / "artifacts"
        names: list[str] = []
        if art_dir.is_dir():
            for p2 in sorted(art_dir.glob("*.json"))[:8]:
                names.append(f"{p2.stem}({max(1, p2.stat().st_size // 1024)}KB)")
        if names:
            lines.append("- 磁盘产物：" + " / ".join(names)
                         + "（已在上面列出，不用再 list_artifacts 摸底）")
    return "\n".join(lines)


def _reports_digest(root: Path, limit: int) -> tuple[str, int]:
    try:
        from clipwright.services import stage_report

        items = stage_report.list_reports(root, limit=3)
    except Exception:  # noqa: BLE001
        return "", 0
    if not items:
        return "", 0
    chunks: list[str] = []
    for m in items:
        head = f"### #{m['seq']} {m['stage']}（{m['status']}）"
        body = (m.get("next_for") or "").strip() or (m.get("preview") or "").strip()
        chunks.append(f"{head}\n{body[:600]}")
    return "\n\n".join(chunks)[:limit], len(items)


def _failure_text(failure: dict[str, Any], limit: int) -> str:
    lines = [f"- 类别：{failure.get('category')}", f"- 可重试性：{failure.get('retryable')}",
             f"- 阻塞点：{str(failure.get('blocking_point') or '')[:300]}"]
    for need in (failure.get("needs") or [])[:3]:
        if isinstance(need, dict):
            lines.append(f"- 缺输入：{need.get('field')}（来自 {need.get('from_stage') or need.get('from')}）")
    for ev in (failure.get("evidence") or [])[:3]:
        if isinstance(ev, dict):
            lines.append(f"- 证据[{ev.get('kind')}]：{str(ev.get('text'))[:200]}")
    return "\n".join(lines)[:limit]


# ── 会话 / 上一轮执行轨迹（2026-10-31 用户要求）─────────────────────────
#
# 补的是主 agent 的「我记得刚才发生了什么」：
#   ① 会话对话（用户说了什么、自己承诺了什么）；
#   ② 上一轮执行轨迹（派过哪些子 agent、调过哪些工具/技能、哪一步降级/失败）。
#
# 为什么必须补：任务被中止后用户再说一句，主 agent 起的是**新任务**——`context_digest`
# 是空的，而 `load()` 以前只装 docs / 产物 / 记忆 / 报告，于是它对"上一轮干了什么、
# 用户刚提了什么要求"一无所知，只能从零摸索。数据源用**会话时间线**（session_log）：
# 它是服务端唯一有序流，任务事件已由 session_projection 投影进去，落盘、可读、跨进程存活。

SESSION_LIMITS = {"instruction": 1200, "conversation": 1500, "prev_run": 1800}


def _session_id_of(task: Any) -> str:
    try:
        request = getattr(task, "request", task)
        extra = dict(getattr(request, "extra", {}) or {})
        return str(extra.get("session_id") or "")
    except Exception:  # noqa: BLE001
        return ""


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("delta", "text", "content", "markdown", "summary", "reply"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, list):
        return "、".join(_text_of(v) for v in value[:8])
    return "" if value is None else str(value)


def _short(text: Any, limit: int) -> str:
    body = " ".join(_text_of(text).split())
    return body[:limit] + ("…" if len(body) > limit else "")


def _plain_text(value: Any) -> str:
    """只认**真正的文本**：dict 一律当噪声（会话流里 `content` 也可能是事件 detail，
    例如 `{"kind": "preamble", "round": 12}` —— 那些不是"对话内容"，塞进上下文
    只会把模型的注意力从用户诉求上带走）。"""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        for key in ("delta", "text", "content", "markdown", "summary", "reply"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return " ".join(inner.split())
    return ""


def _instruction_digest(task: Any) -> str:
    """本轮用户指令（续跑时由 requirements 写进 `extra["user_instruction"]`）。"""
    try:
        request = getattr(task, "request", task)
        extra = dict(getattr(request, "extra", {}) or {})
        text = str(extra.get("user_instruction") or "").strip()
        prev_task = str(extra.get("previous_task_id") or "")
    except Exception:  # noqa: BLE001
        return ""
    if not text:
        return ""
    suffix = (f"\n（上一轮任务 {prev_task} 已结束；这是同一会话的**续跑**，"
              "不是新项目：接着做完没做完的事，不要从头重做）") if prev_task else ""
    return text[:SESSION_LIMITS["instruction"]] + suffix


def _session_digest(task: Any) -> tuple[str, str, list[dict[str, Any]]]:
    """从会话时间线取 `(对话摘要, 上一轮轨迹摘要, sources)`。"""
    sid = _session_id_of(task)
    if not sid:
        return "", "", []
    try:
        from clipwright.services.session_log import get_log

        items = (get_log(sid).tail(260) or {}).get("items") or []
    except Exception as exc:  # noqa: BLE001 — 时间线不可用不能阻断装载
        logger.warning("会话上下文不可用 %s: %s", sid, exc)
        return "", "", []
    if not items:
        return "", "", []

    conv: list[str] = []
    trace: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("type") or "")
        meta = it.get("meta") or {}
        content = it.get("content")
        state = str(it.get("state") or "")
        if kind == "content":
            role = str(meta.get("role") or "")
            raw = _plain_text(content)
            if not raw:
                continue                      # 事件 detail 不是对话（见 _plain_text）
            text = raw[:160] + ("…" if len(raw) > 160 else "")
            if role == "user":
                conv.append(f"- 用户：{text}")
            elif role == "assistant":
                conv.append(f"- 主 agent：{text}")
            continue
        if kind in ("tool", "skill", "subagent"):
            name = (meta.get("tool") or meta.get("skill") or meta.get("agent")
                    or _plain_text(content) or str(meta.get("message") or "")[:60])
            status = meta.get("status") or state
            owner = str(meta.get("agent") or "")
            owner_tag = f"@{owner}" if owner and owner not in (name, "") else ""
            line = f"- [{kind}{owner_tag}] {name} {status}".strip()
            err = meta.get("error")
            if err:
                line += f"（{_short(err, 90)}）"
            trace.append(line)
        elif kind == "stage":
            trace.append(f"- [阶段] {meta.get('stage') or _short(content, 40)} "
                         f"{meta.get('status') or state}".strip())
        elif kind == "degradation":
            facts = _plain_text(content)
            if not facts:
                stage = str(meta.get("stage") or "")
                knd = str(meta.get("kind") or "")
                facts = " ".join(x for x in (stage, knd) if x) or str(
                    meta.get("message") or ""
                )[:140]
            trace.append(f"- [降级] {_short(facts, 140)}")
        elif kind == "report":
            bullets = meta.get("bullets")
            head = ""
            if isinstance(bullets, list) and bullets:
                head = _short(bullets[0], 160)
            if not head:
                head = _short(content, 160)
            if head:
                trace.append(f"- [汇报] {head}")

    conv_txt = "\n".join(conv[-12:])[:SESSION_LIMITS["conversation"]]
    # 轨迹去重（连续重复的同一行没有信息量，只会白占预算）
    dense: list[str] = []
    for line in trace:
        if dense and dense[-1] == line:
            continue
        dense.append(line)
    run_txt = "\n".join(dense[-26:])[:SESSION_LIMITS["prev_run"]]
    sources = [{
        "name": "session_timeline",
        "bytes": len(conv_txt) + len(run_txt),
        "missing": not (conv_txt or run_txt),
        "items": len(items),
        "tools": sum(1 for t in trace if t.startswith("- [tool")),
        "subagents": sum(1 for t in trace if t.startswith("- [subagent")),
        "stages": sum(1 for t in trace if t.startswith("- [阶段")),
    }]
    return conv_txt, run_txt, sources


__all__ = ["DEFAULT_BUDGET_CHARS", "LIMITS", "load"]