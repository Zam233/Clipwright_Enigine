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
    try:
        from clipwright.services import task_blackboard as bb

        board = bb.get(str(getattr(task, "id", "") or ""))
        for name, value in list(board.items())[:12]:
            if isinstance(value, dict):
                preview = {k: value[k] for k in list(value)[:3]}
            elif isinstance(value, list):
                preview = f"{len(value)} 项"
            else:
                preview = str(value)[:80]
            lines.append(f"- {name}: {str(preview)[:160]}")
    except Exception:  # noqa: BLE001
        pass
    if not lines and root is not None:
        art_dir = root / "artifacts"
        if art_dir.is_dir():
            for p in sorted(art_dir.glob("*"))[:12]:
                lines.append(f"- {p.name}（{p.stat().st_size} 字节）")
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


__all__ = ["DEFAULT_BUDGET_CHARS", "LIMITS", "load"]
