"""诊断脚本：运行管线并输出各环节的时长信息。"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 注册内置插件（模拟 FastAPI lifespan）
from clipwright.category import CategoryRegistry
from clipwright.category.knowledge_longform import KnowledgeLongformPlugin
from clipwright.category.digital_review import DigitalReviewPlugin
from clipwright.category.kichiku_fastcut import KichikuFastcutPlugin
from clipwright.category.vlog_daily import VlogDailyPlugin
CategoryRegistry.register(KnowledgeLongformPlugin())
CategoryRegistry.register(KichikuFastcutPlugin())
CategoryRegistry.register(DigitalReviewPlugin())
CategoryRegistry.register(VlogDailyPlugin())
from clipwright.tool import register_builtin_tools
register_builtin_tools()
from clipwright.animation import register_builtin_animations
register_builtin_animations()
from clipwright.skill import register_builtin_skills
register_builtin_skills()

from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.schema.pipeline import PipelineRequest
from clipwright.services.trace import get_all_events, clear
from clipwright.config import logger
import logging
logger.setLevel(logging.DEBUG)

async def main():
    request = PipelineRequest(
        persona_id="zam_knowledge_critical",
        category_plugin_id="knowledge_longform",
        topic="只有B站黑话才能描述斩杀线？中国青年的亚文化，正在解构西方的定义权",
        extra_params={
            "audio_duration_sec": 600.0,
            "script_text": """大家好，这里是扎姆。这两天互联网上最魔幻的事情，莫过于那位名为斯奎奇大王牢A连夜提桶跑路，润回上海。大家都在刷斩杀线，都在玩梗，但是我产生了一个疑问，为什么在2026年的今天，当我们去描述大洋彼岸那个超级大国底层的淋漓鲜血时，我们更倾向于亚文化的语言？

斩杀线、拼高达、长生种、清理地图、版本T0等等，这些词原本属于MOBA，属于胶佬。但现在，这些词语成了认知现实苦难的窗口。今天这期视频，我想抛开那些具体的数据争论，咱们不聊那个所谓的400美元到底是不是真的，咱们来聊聊这个现象背后的东西：为什么亚文化用词取代了常规语言，成为我们描述这个世界的术语。

很多人第一反应会说：这不明摆着吗？用黑话是为了过审啊。没错，为了规避平台的机制，确实在某些场合习惯了用米代替钱，牢Alex也的确有出于这种目的而是用亚文化用词的意图。

但是，你们有没有想过，牢Alex所描述的那个美国斩杀线之下的世界，如果不用黑话，而是用最直白的白描手法写出来，会是什么样？
""",
        },
        dry_run=True,
    )

    orchestrator = PipelineOrchestrator()
    state = await orchestrator.run(request)

    print(f"\n=== Pipeline Status: {state.status} ===")
    if state.error:
        print(f"Error: {state.error}")

    for step in state.steps:
        print(f"\n--- {step.agent_name} ({step.status}) ---")
        print(f"  Duration: {step.duration_ms}ms")
        if step.error:
            print(f"  Error: {step.error}")
        if step.result:
            scenes = step.result.get("scenes", []) or step.result.get("script_skeleton", {}).get("scenes", [])
            if scenes:
                total = sum(s.get("duration_sec", 0) for s in scenes)
                print(f"  Scenes: {len(scenes)}, total duration: {total:.1f}s")
                for j, s in enumerate(scenes[:8]):
                    print(f"    [{j}] {s.get('title', '')}: {s.get('duration_sec', 0):.1f}s")
                if len(scenes) > 8:
                    print(f"    ... and {len(scenes)-8} more")
            timeline = step.result.get("timeline")
            if timeline:
                tracks = timeline.get("tracks", [])
                clip_count = sum(len(t.get("clips", [])) for t in tracks)
                tl_dur = timeline.get("duration_sec", 0)
                print(f"  Timeline: {tl_dur:.1f}s, {len(tracks)} tracks, {clip_count} clips")
            if step.agent_name == "edit":
                notes = step.result.get("edit_notes", [])
                for n in notes:
                    print(f"  Note: {n}")

    ft = state.shared_data.get("final_timeline")
    if ft:
        print(f"\n=== Final Timeline ===")
        print(f"  Duration: {ft.get('duration_sec', 0):.1f}s")
        for t in ft.get("tracks", []):
            print(f"  Track {t.get('name', '')} ({t.get('kind', '')}): {len(t.get('clips', []))} clips")

    print("\n=== Trace Events (errors) ===")
    events = get_all_events(state.pipeline_id)
    for ev in events:
        if ev.get("type") in ("error", "warning"):
            print(f"  [{ev['type']}] {ev.get('agent', '')}: {ev.get('summary', '')}")

if __name__ == "__main__":
    asyncio.run(main())
