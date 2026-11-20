"""工作区报告（PRD §17.3 / §18.3）：把子 agent 的文字汇报写成 markdown。

三条纪律：

1. **append-only**：报告文件一经写出就不再改写；重跑生成新的序号（`03-edit-2.md`）；
2. **runtime 写盘，不是子 agent 写**：子 agent 只产出文本，由这里落盘——它没有写文件权限，
   改不了别人的报告，也改不了 `docs/plan.md`；
3. **INDEX.md 只是目录**：不参与任何"自动补输入"，主 agent 必须 `read_report` 才能拿到内容。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings

#: 单份报告上限（超出截断并标记）
DEFAULT_REPORT_MAX_CHARS = 20000


def report_root(workspace_root: Path | str) -> Path:
    root = Path(workspace_root) / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_chars() -> int:
    return int(getattr(settings, "report_max_chars", DEFAULT_REPORT_MAX_CHARS)
               or DEFAULT_REPORT_MAX_CHARS)


def reserve_seq(reports_dir: Path) -> int:
    """原子占用一个序号：`O_CREAT|O_EXCL` 建 `.seq-N`，谁建成算谁的。"""
    for candidate in range(1, 100000):
        marker = reports_dir / f".seq-{candidate}"
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
        except OSError as exc:  # noqa: PERF203 — 目录不可写时要如实降级
            logger.warning("报告序号占用失败（reports 不可写）: %s", exc)
            return 0
    return 0


def build_body(payload: dict[str, Any] | None, *, stage: str, status: str,
               summary: str = "", needs: list[Any] | None = None,
               degradations: list[Any] | None = None,
               issues: list[Any] | None = None,
               artifact_names: list[str] | None = None) -> str:
    """自由正文：Agent 自己写什么就是什么，宿主只在缺失时补一段事实。

    v2 起报告**没有固定四节**（PRD v0.4 §7）：正文是给人读的自由 Markdown，
    机器事实只在 `AgentResult`。这里只负责“拿正文”，不做章节切分。
    """

    def _text(value: Any) -> str:
        return str(value or "").strip()

    raw = (payload or {}).get("report_text")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:4000]
    if isinstance(raw, dict):
        for key in ("body", "markdown", "report", "text"):
            body = _text(raw.get(key))
            if body:
                return body[:4000]
        parts: list[str] = []
        for key in ("did", "result", "issues", "next", "summary"):
            body = _text(raw.get(key))
            if body:
                parts.append(f"**{key}**：{body}")
        if parts:
            return "\n\n".join(parts)[:4000]

    # 宿主兜底：只陈述事实，不编造“我做了什么”
    lines = [f"{stage} 阶段终态：{status}"]
    if _text(summary):
        lines.append(_text(summary))
    need_fields = []
    for item in (needs or [])[:8]:
        field = str(getattr(item, "field", "") or (item or {}).get("field") or "") if isinstance(item, dict) else str(getattr(item, "field", "") or "")
        if field:
            need_fields.append(field)
    if need_fields:
        lines.append("待补：" + "、".join(need_fields))
    if degradations:
        lines.append(f"降级：{len(degradations)} 项")
    if issues:
        lines.append(f"问题：{len(issues)} 条")
    if artifact_names:
        lines.append("产物：" + "、".join(artifact_names[:8]))
    return "\n\n".join(lines)[:4000]


def render_report(*, stage: str, status: str, attempt: int, seq: int, body: str,
                  artifacts: dict[str, Any] | None = None,
                  cost_usd: Optional[float] = None, duration_ms: int = 0,
                  provider: str = "", model: str = "", task_id: str = "",
                  run_id: str = "") -> str:
    """渲染完整 markdown：最小机器信封（front-matter）+ 自由正文。"""
    meta = {
        "schema_version": 2,
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "seq": seq,
        "task_id": task_id,
        "run_id": run_id,
        "cost_usd": cost_usd,
        "cost_unknown": cost_usd is None,
        "duration_ms": duration_ms,
        "provider": provider,
        "model": model,
        "artifacts": sorted((artifacts or {}).keys()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    lines = ["---"]
    lines.extend(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    lines.append("---")
    lines.append("")
    text_body = (body or "").strip() or f"{stage} 阶段终态：{status}"
    lines.append(text_body)
    lines.append("")

    text = "\n".join(lines)
    limit = _max_chars()
    if len(text) > limit:
        text = text[:limit] + f"\n\n<!-- truncated: 超过 {limit} 字 -->\n"
    return text


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + 原子换名：读者永远看不到半截报告。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_report(*, workspace_root: Path | str, stage: str, status: str, attempt: int,
                 body: str, artifacts: dict[str, Any] | None = None,
                 cost_usd: Optional[float] = None, duration_ms: int = 0,
                 provider: str = "", model: str = "", task_id: str = "",
                 run_id: str = "") -> tuple[str, int]:
    """写一份报告并更新 INDEX；返回 `(path, seq)`，失败返回 `("", 0)`。"""
    try:
        reports = report_root(workspace_root)
        seq = reserve_seq(reports)
        if seq <= 0:
            return "", 0
        safe_stage = "".join(ch for ch in str(stage) if ch.isalnum() or ch in "-_") or "stage"
        name = f"{seq:02d}-{safe_stage}-{max(1, int(attempt))}.md"
        path = reports / name
        text = render_report(stage=stage, status=status, attempt=attempt, seq=seq,
                             body=body, artifacts=artifacts, cost_usd=cost_usd,
                             duration_ms=duration_ms, provider=provider, model=model,
                             task_id=task_id, run_id=run_id)
        _atomic_write(path, text)
        update_index(reports)
        return str(path), seq
    except Exception as exc:  # noqa: BLE001 — 写盘失败由调用方降级为 degraded
        logger.warning("写报告失败 %s/%s: %s", workspace_root, stage, exc)
        return "", 0


def _parse_front_matter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        try:
            out[key] = json.loads(raw)
        except Exception:  # noqa: BLE001 — 非 JSON 就当字符串
            out[key] = raw.strip('"')
    return out


def list_reports(workspace_root: Path | str, limit: int = 10) -> list[dict[str, Any]]:
    """列出报告（新→旧）：`{seq, stage, status, attempt, headline, path, cost_usd, created_at}`。"""
    reports = Path(workspace_root) / "reports"
    if not reports.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(reports.glob("*.md"), reverse=True):
        # INDEX.md 是目录本身；progress.md 是 Kernel 进度时间线（PRD §14），
        # 两者都不是阶段报告——混进来会让"报告数"虚高、也会被当成可 read_report 的输入。
        if path.name in ("INDEX.md", "progress.md"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_front_matter(text)
        headline = ""
        for line in text.splitlines():
            if line.startswith("## 结果如何"):
                continue
            if line.startswith("# "):
                headline = line[2:].strip()
                break
        items.append({
            "seq": int(meta.get("seq") or 0),
            "stage": str(meta.get("stage") or ""),
            "status": str(meta.get("status") or ""),
            "attempt": int(meta.get("attempt") or 0),
            "headline": headline,
            "path": str(path),
            "run_id": str(meta.get("run_id") or ""),
            "task_id": str(meta.get("task_id") or ""),
            "cost_usd": meta.get("cost_usd"),
            "created_at": str(meta.get("created_at") or ""),
            "preview": _body_excerpt(text)[:300],
        })
    items.sort(key=lambda m: (m["seq"], m["created_at"]), reverse=True)
    return items[:max(1, int(limit or 10))]


def _body_excerpt(text: str) -> str:
    """自由正文的首段摘要（不含 front-matter、不含标题行）。"""
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end >= 0:
            body = body[end + 4:]
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    return " ".join(lines)[:400]


def update_index(reports_dir: Path) -> str:
    """原子重写 INDEX.md（只是目录，不是数据通道）。

    并发安全：写完回读一次文件数，若与索引行数不一致则重建一次——
    避免"两个 writer 各自快照，后写的把前一条挤掉"。

    另外重写时**带上「进度时间线」小节**（PRD §14）：内容来自 `reports/progress.md`
    （Kernel 确定性播报写的），不是报告索引的一部分，但用户只打开 INDEX.md 时也要能
    看到阶段走到哪了。两份文件各自维护，这里只做合并展示，谁也不覆盖谁。
    """
    path = reports_dir / "INDEX.md"
    for _attempt in range(3):
        items = list_reports(reports_dir.parent, limit=500)
        lines = ["# 报告索引", "",
                 "> 自动生成，勿手改。左列 seq 越大越新；内容用 read_report 读取。", "",
                 "| seq | stage | status | attempt | headline | cost | 文件 |",
                 "|---|---|---|---|---|---|---|"]
        for m in sorted(items, key=lambda x: x["seq"]):
            cost = "未知" if m.get("cost_usd") in (None, "") else m["cost_usd"]
            lines.append(
                f"| {m['seq']} | {m['stage']} | {m['status']} | {m['attempt']} | "
                f"{m['headline']} | {cost} | `{Path(m['path']).name}` |"
            )

        try:
            from clipwright.services.progress import timeline_lines

            timeline = timeline_lines(reports_dir, limit=40)
        except Exception:  # noqa: BLE001 — 进度模块不可用不影响索引本身
            timeline = []
        if timeline:
            lines += ["", "## 进度时间线", "",
                      "> 由 Kernel 确定性播报（PRD §14）；最新在下，完整记录见 `progress.md` / `progress.jsonl`。",
                      ""]
            lines += timeline

        text = "\n".join(lines) + "\n"
        tmp = path.with_suffix(f".{hashlib.sha256(text.encode()).hexdigest()[:6]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        # progress.md 不是报告，别把它算进"磁盘上还有几份报告"
        on_disk = len([p for p in reports_dir.glob("*.md")
                       if p.name not in ("INDEX.md", "progress.md")])
        if on_disk <= len(items):
            break
    return str(path)


__all__ = [
    "DEFAULT_REPORT_MAX_CHARS",
    "build_body",
    "list_reports",
    "render_report",
    "report_root",
    "reserve_seq",
    "update_index",
    "write_report",
]
