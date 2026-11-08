"""V60：老会话（只有 scenes、没有规划书）→ 反向生成 legacy plan → 下游能跑。

链路刻意走**真实组件**：

1. 走 API 建一个真需求会话；
2. 把会话写成"老形态"：只有 `production_plan.raw_scenes`（没有 `plan` 键），
   状态停 `brief_ready`——这正是 V60 说的"老 session"；
3. 把 `_generate_plan`（LLM 翻译）替换成返回 None，**只**模拟"翻译失败"这一个
   事实（否则真 LLM 会直接给出新规划，永远进不到 legacy 分支）；
4. 通过 API 发「确认」→ 走 brief_ready 分支 → legacy 反向生成 → `plan_ready`；
5. 通过 `/api/requirements/proceed` 真起管线 → 等它 `completed`。

**口径**：第 3 步是唯一的人为注入（明确的失败模拟）；其余全部是真代码真数据。
不这么做就只能证明"函数能算"，证明不了"这条兜底路径真的接在会话流程上"。

用法::

    python scripts/e2e_legacy_plan.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TERMINAL = {"completed", "failed", "cancelled", "canceled"}

#: 老会话里只有这个：旧管线留下的场景列表（没有 plan、没有 markdown）
OLD_SCENES = [
    {"title": "开场", "description": "版画台的俯拍，纸与刻刀并置", "duration_sec": 5.0},
    {"title": "主体", "description": "套印过程特写，红与靛蓝依次压上", "duration_sec": 8.0},
    {"title": "收束", "description": "印张揭起的瞬间，颗粒可见", "duration_sec": 4.0},
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V60：老会话 → legacy plan → 下游可跑")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    started = time.perf_counter()
    report: dict = {"ok": False, "old_scenes": len(OLD_SCENES)}
    with TestClient(app) as client:
        session_id = str(
            client.post("/api/requirements/init", json={
                "topic": "三墨之间",
                "persona_id": "default",
                "category_plugin_id": "vlog_daily",
            }).json().get("session_id") or ""
        )
        report["session_id"] = session_id
        if not session_id:
            report["error"] = "会话创建失败"
            _write(report, started)
            return 1

        # ② 写成老形态
        from clipwright.api.requirements import _service

        _service._persist(
            session_id,
            "brief_ready",
            [],
            {"overview": "老会话留下的简报", "core_message": "版画与套印"},
            {"raw_scenes": OLD_SCENES},
            {"topic": "三墨之间", "persona_id": "default",
             "category_plugin_id": "vlog_daily", "script_text": "", "audio_duration_sec": 0},
        )
        before = client.get(f"/api/requirements/session/{session_id}").json()
        report["before"] = {
            "status": before.get("status"),
            "plan_keys": sorted((before.get("production_plan") or {}).keys()),
        }

        # ③ 只模拟"LLM 翻译失败"
        original = _service._generate_plan

        async def _fail_generate(*_a, **_k):
            return None

        _service._generate_plan = _fail_generate
        try:
            chat = client.post("/api/requirements/chat", json={
                "session_id": session_id, "message": "确认",
            })
        finally:
            _service._generate_plan = original
        report["chat_http"] = chat.status_code
        if chat.status_code >= 400:
            report["error"] = chat.text[:400]
            _write(report, started)
            return 1
        body = chat.json()
        after = client.get(f"/api/requirements/session/{session_id}").json()
        plan = after.get("production_plan") or {}
        report["after"] = {
            "status": after.get("status"),
            "chat_status": body.get("status"),
            "plan_keys": sorted(plan.keys()),
            "legacy": bool(plan.get("legacy") or (plan.get("plan") or {}).get("legacy")),
            "scene_count": plan.get("scene_count"),
            "has_markdown": bool(plan.get("markdown_content")),
            "sections": len((plan.get("plan") or {}).get("sections") or []),
        }

        # ④ 下游：proceed 真起管线
        proceed = client.post("/api/requirements/proceed", json={
            "session_id": session_id,
            "persona_id": "default",
            "category_plugin_id": "vlog_daily",
            "extra_params": {"dry_run": True},
        })
        report["proceed_http"] = proceed.status_code
        pipeline_id = str((proceed.json() or {}).get("pipeline_id") or "")
        report["pipeline_id"] = pipeline_id
        if pipeline_id:
            status = ""
            deadline = time.perf_counter() + args.timeout
            while time.perf_counter() < deadline:
                status = str(client.get(f"/api/pipeline/status/{pipeline_id}").json().get("status") or "")
                if status.lower() in TERMINAL:
                    break
                time.sleep(args.interval)
            report["pipeline_status"] = status
        else:
            report["error"] = proceed.text[:400]

        report["ok"] = bool(
            report["after"]["legacy"]
            and report["after"]["status"] == "plan_ready"
            and report.get("pipeline_status") == "completed"
        )
    _write(report, started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write(report: dict, started: float) -> None:
    report["elapsed_sec"] = round(time.perf_counter() - started, 1)
    target = ROOT / "out" / "v60" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
