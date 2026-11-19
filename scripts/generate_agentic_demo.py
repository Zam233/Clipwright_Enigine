"""Generate demo MGs through AnimationAgent._generate_mg_via_agentic (Tool/Skill loop)."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipwright.main import app
from clipwright.agents.animation_agent import AnimationAgent
from clipwright.services.llm import LLMService

CASES = [
    ("01_title", "标题揭示：展示「帧艺动画」四个字，水平居中，文字下留 40px 再放下划线", "帧艺动画"),
    ("02_data", "数据冲击：用户增长 120%，柱状图从零生长，数字连续计数；标签放柱子上方", "用户增长|120|%"),
    ("03_compare", "左右对比：传统流程慢、AI 流程快，两侧卡片同高等大、高对比；中间箭头指出差异", "传统流程|AI 流程|快 3 倍"),
    ("04_flow", "三步流程：选题 → 脚本 → 剪辑；三个节点等距横排，节点依次点亮", "选题|脚本|剪辑"),
    ("05_quote", "金句强调：让每一帧都有信息量；三行文字水平居中，下划线用 line 元素", "让每一帧|都有|信息量"),
]

async def main():
    async with app.router.lifespan_context(app):
        out = []
        for name, desc, text in CASES:
            agent = AnimationAgent()
            agent._llm = LLMService()
            agent._tl_width, agent._tl_height, agent._tl_fps = 1920, 1080, 30
            agent._ctx_store = None
            r = await agent._generate_mg_via_agentic(
                description=desc, text_content=text, persona_style={"accent_color": "#00A8E8"},
                scene_context={"title": desc[:30]}, category_context={}, vision_prompt="", web_context="",
            )
            mg = r.get("mg_def") or {}
            Path(f"renders/agent-demo/{name}-agentic.json").write_text(json.dumps(mg, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append({"name": name, "success": r.get("success"), "qa_errors": r.get("qa_errors"), "elements": len(mg.get("elements", []))})
            print(json.dumps(out[-1], ensure_ascii=False))
        Path("renders/agent-demo/agentic-summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

asyncio.run(main())
