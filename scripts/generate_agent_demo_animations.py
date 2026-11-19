"""Generate five real MG animations through the modular prompt chain and render previews."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from clipwright.main import app
from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.prompting import build_animation_plan

OUT = Path("renders/agent-demo")
API = "http://127.0.0.1:8080"

CASES = [
    {
        "name": "01_title",
        "description": "标题揭示：展示「帧艺动画」四个字，水平居中，文字下留 40px 再放下划线；单主角，冷色科技感，落定后 hold 一秒",
        "text": "帧艺动画",
        "category": "知识区",
        "scope": "kinetic-type",
    },
    {
        "name": "02_data",
        "description": "数据冲击：用户增长 120%，柱状图从零生长，数字连续计数；标签放柱子上方，不要与柱体重叠",
        "text": "用户增长|120|%",
        "category": "知识区",
        "scope": "charts",
    },
    {
        "name": "03_compare",
        "description": "左右对比：传统流程慢、AI 流程快，两侧卡片同高等大、高对比；中间箭头指出差异",
        "text": "传统流程|AI 流程|快 3 倍",
        "category": "数码评测",
        "scope": "comparison",
    },
    {
        "name": "04_flow",
        "description": "三步流程：选题 → 脚本 → 剪辑；三个节点等距横排，左右各留 240px 安全边距，节点依次点亮",
        "text": "选题|脚本|剪辑",
        "category": "知识区",
        "scope": "flow",
    },
    {
        "name": "05_quote",
        "description": "金句强调：让每一帧都有信息量；三行文字水平居中，下划线用 line 元素；不要生成任何装饰形状",
        "text": "让每一帧都有信息量",
        "category": "知识区",
        "scope": "kinetic-type",
    },
]

PERSONA = {
    "primary_color": "#0A1428",
    "secondary_color": "#142A4A",
    "accent_color": "#00A8E8",
    "text_color": "#F5F7FA",
    "font_family": "sans-serif",
    "style_description": "冷色科技感，高对比，克制发光",
}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    async with app.router.lifespan_context(app):
        for case in CASES:
            gen = MGGenerator()
            gen._active_plan = build_animation_plan(
                case["description"], {"display_name": case["category"]}, 4.0,
            )
            result = await gen.generate(
                description=case["description"],
                text_content=case["text"],
                persona_style=PERSONA,
                scene_context={"title": case["description"][:30]},
                category_context={"display_name": case["category"], "shot_type": case["scope"]},
            )
            item = {
                "name": case["name"],
                "description": case["description"],
                "success": bool(result.get("success")),
                "method": result.get("method"),
                "error": str(result.get("error") or "")[:300],
                "plan": gen._active_plan.as_dict() if gen._active_plan else None,
            }
            if not result.get("mg_def"):
                summary.append(item)
                continue
            mg_def = result["mg_def"]
            (OUT / f"{case['name']}.json").write_text(
                json.dumps({"mg_def": mg_def, "plan": item["plan"]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                async with httpx.AsyncClient(timeout=600) as client:
                    raw_params = result.get("params") or {}
                    preview_params = {
                        str(k): str(v.get("default", "")) if isinstance(v, dict) else str(v)
                        for k, v in raw_params.items()
                    }
                    resp = await client.post(f"{API}/api/animation/preview", json={
                        "mg_json": mg_def,
                        "params": preview_params,
                        "width": 1920,
                        "height": 1080,
                        "fps": 30,
                    })
                    resp.raise_for_status()
                    preview = resp.json()
                    item.update({"preview": preview, "preview_ok": True})
            except Exception as exc:  # noqa: BLE001
                item.update({"preview_ok": False, "preview_error": str(exc)[:300]})
            summary.append(item)
            print(json.dumps(item, ensure_ascii=False))
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
