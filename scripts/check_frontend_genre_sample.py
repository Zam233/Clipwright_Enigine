"""校验前端「类型复核」里的示例时间线能被后端真实接受（不是自说自话）。"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sample_timeline() -> dict:
    """与前端 TypeMakerPage 的 SAMPLE_TIMELINE_JSON 保持同构。"""
    clips = [
        {
            "id": f"c{i}",
            "kind": "video",
            "asset_id": f"a{i}",
            "track_id": "v",
            "start_sec": i * 3,
            "duration_sec": 3,
            "source_offset_sec": 0,
            "speed": 1,
            "volume": 1,
            "opacity": 1,
            "keyframes": [],
        }
        for i in range(8)
    ]
    captions = [
        {
            "id": f"t{i}",
            "kind": "caption",
            "asset_id": "",
            "track_id": "cap",
            "start_sec": i * 3,
            "duration_sec": 3,
            "source_offset_sec": 0,
            "speed": 1,
            "volume": 1,
            "opacity": 1,
            "keyframes": [],
            "text": f"第 {i + 1} 句歌词",
        }
        for i in range(8)
    ]
    return {
        "id": "tl_genre_check_sample",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_sec": 24,
        "tracks": [
            {"id": "v", "name": "视频", "kind": "video", "index": 0, "clips": clips},
            {"id": "cap", "name": "字幕", "kind": "caption", "index": 1, "clips": captions},
        ],
    }


async def main() -> int:
    from clipwright.api.type_maker import run_genre_check_async
    from clipwright.category import LyricMvPlugin
    from clipwright.category.registry import CategoryRegistry

    try:
        CategoryRegistry.register(LyricMvPlugin())
    except Exception:
        pass

    result = await run_genre_check_async("lyric_mv", {"timeline": sample_timeline()})
    print(json.dumps({
        "ok": result["ok"],
        "judge": result["judge"],
        "measured": {k: round(v, 3) for k, v in result["measured"].items()},
        "traits": [(t["name"], t["value"], t["passed"]) for t in result["traits"]],
        "summary": result["summary"],
    }, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
