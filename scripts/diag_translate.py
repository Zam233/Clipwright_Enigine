"""诊断 _translate_plan 是否挂起。"""
import os, sys, asyncio, time
os.chdir(r"D:\Clipweight")
sys.path.insert(0, r"D:\Clipweight")
from clipwright.services.requirements_service import RequirementsService


async def main():
    svc = RequirementsService()
    scenes = [
        {"title": "开场", "description": "黑底白字标题 [文字动画]打字", "duration_sec": 120, "keywords": ["文理"]},
        {"title": "解构", "description": "对比图表 [逻辑动画]mg_dynamic", "duration_sec": 180, "keywords": ["对比"]},
        {"title": "升华", "description": "拉康镜像 [逻辑动画]对比", "duration_sec": 120, "keywords": ["镜像"]},
    ]
    brief = {"title": "文理之争", "overview": "测试", "core_message": "测试"}
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            svc._translate_plan(scenes, brief, "测试文稿内容"), timeout=180
        )
        print("耗时:", round(time.time() - t0, 1), "s")
        print("scene_count:", result.get("scene_count"))
        print("markdown len:", len(result.get("markdown_content", "")))
        print("markdown 前 300 字:", (result.get("markdown_content") or "")[:300])
    except asyncio.TimeoutError:
        print("!!! _translate_plan 180s 超时（LLM 调用挂起）")


asyncio.run(main())
