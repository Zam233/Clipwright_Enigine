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


def build_sections(payload: dict[str, Any] | None, *, stage: str, status: str,
                   summary: str = "", needs: list[dict[str, Any]] | None = None,
                   degradations: list[dict[str, Any]] | None = None,
                   issues: list[dict[str, Any]] | None = None,
                   artifact_names: list[str] | None = None) -> dict[str, str]:
    """四节正文（我做了什么 / 结果如何 / 发现的问题 / 给下一位）。

    优先用 Agent 自己写的 `report_text`（若提供，可为 dict 或纯文本）；
    否则从结构化结果里合成——**不允许空着**，缺内容时写明"未提供"。
    """
    raw = (payload or {}).get("report_text")
    if isinstance(raw, dict):
        return {
            "我做了什么": str(raw.get("did") or raw.get("我做了什么") or "").strip() or "未提供",
            "结果如何": str(raw.get("result") or raw.get("结果如何") or "").strip() or "未提供",
            "发现的问题": str(raw.get("issues") or raw.get("发现的问题") or "").strip() or "无",
            "给下一位": str(raw.get("next") or raw.get("给下一位") or "").strip() or "无",
        }
    if isinstance(raw, str) and raw.strip():
        return {"我做了什么": "见下", "结果如何": raw.strip()[:4000],
                "发现的问题": "未提供", "给下一位": "未提供"}

    did = f"{stage} 阶段执行完毕（status={status}）"
    if artifact_names:
        did += f"；产出 {len(artifact_names)} 项：{'、'.join(artifact_names[:8])}"

    notes: list[str] = []
    for key in ("material_notes", "edit_notes", "audio_notes", "animation_notes"):
        for note in (payload or {}).get(key) or []:
            if str(note).strip():
                notes.append(f"- {str(note)[:300]}")
    if payload and payload.get("redo_agent"):
        notes.append(f"- 建议重做：{payload['redo_agent']}")

    problems: list[str] = []
    for d in (degradations or [])[:8]:
        kind = str(d.get("kind") or "")
        detail = str(d.get("detail") or "")
        problems.append(f"- 降级 `{kind}`：{detail[:200]}" if kind else f"- {detail[:200]}")
    for it in (issues or [])[:8]:
        if isinstance(it, dict):
            problems.append(f"- 问题（{it.get('severity') or 'info'}）："
                            f"{str(it.get('title') or it.get('message') or '')[:200]}")
    for need in (needs or [])[:5]:
        problems.append(f"- 缺输入 `{need.get('field')}`（来自 {need.get('from_stage') or need.get('from')}）："
                        f"{str(need.get('why') or '')[:160]}")

    return {
        "我做了什么": did,
        "结果如何": summary.strip() or "未提供摘要",
        "发现的问题": "\n".join(problems) or ("\n".join(notes) or "无"),
        "给下一位": "\n".join(notes) or "无",
    }


def render_report(*, stage: str, status: str, attempt: int, seq: int,
                  sections: dict[str, str], artifacts: dict[str, Any] | None = None,
                  cost_usd: Optional[float] = None, duration_ms: int = 0,
                  provider: str = "", model: str = "", task_id: str = "") -> str:
    """渲染完整 markdown（含 YAML front-matter）。"""
    meta = {
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "seq": seq,
        "task_id": task_id,
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
    lines.append(f"# {stage} · 第 {attempt} 次 · {status}")
    lines.append("")
    for title in ("我做了什么", "结果如何", "发现的问题", "给下一位"):
        body = (sections.get(title) or "").strip() or "未提供"
        lines.append(f"## {title}")
        lines.append("")
        lines.append(body)
        lines.append("")
    if artifacts:
        lines.append("## 产物")
        lines.append("")
        for name, ref in (artifacts or {}).items():
            preview = ref.get("preview") if isinstance(ref, dict) else None
            artifact = ref.get("artifact") if isinstance(ref, dict) else ref
            lines.append(f"- `{name}` → {artifact or '（内联）'}（preview: {preview}）")
        lines.append("")

    text = "\n".join(lines)
    limit = _max_chars()
    if len(text) > limit:
        text = text[:limit] + f"\n\n<!-- truncated: 超过 {limit} 字 -->\n"
    return text


def write_report(*, workspace_root: Path | str, stage: str, status: str, attempt: int,
                 sections: dict[str, str], artifacts: dict[str, Any] | None = None,
                 cost_usd: Optional[float] = None, duration_ms: int = 0,
                 provider: str = "", model: str = "", task_id: str = "") -> tuple[str, int]:
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
                             sections=sections, artifacts=artifacts, cost_usd=cost_usd,
                             duration_ms=duration_ms, provider=provider, model=model,
                             task_id=task_id)
        path.write_text(text, encoding="utf-8")
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
        if path.name == "INDEX.md":
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
            "cost_usd": meta.get("cost_usd"),
            "created_at": str(meta.get("created_at") or ""),
            "preview": _section_text(text, "结果如何")[:300],
            "next_for": _section_text(text, "给下一位")[:600],
        })
    items.sort(key=lambda m: (m["seq"], m["created_at"]), reverse=True)
    return items[:max(1, int(limit or 10))]


def _section_text(text: str, title: str) -> str:
    marker = f"## {title}"
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker):]
    end = rest.find("\n## ")
    return (rest if end < 0 else rest[:end]).strip()


def update_index(reports_dir: Path) -> str:
    """原子重写 INDEX.md（只是目录，不是数据通道）。

    并发安全：写完回读一次文件数，若与索引行数不一致则重建一次——
    避免"两个 writer 各自快照，后写的把前一条挤掉"。
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
        text = "\n".join(lines) + "\n"
        tmp = path.with_suffix(f".{hashlib.sha256(text.encode()).hexdigest()[:6]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        on_disk = len([p for p in reports_dir.glob("*.md") if p.name != "INDEX.md"])
        if on_disk <= len(items):
            break
    return str(path)


__all__ = [
    "DEFAULT_REPORT_MAX_CHARS",
    "build_sections",
    "list_reports",
    "render_report",
    "report_root",
    "reserve_seq",
    "update_index",
    "write_report",
]
