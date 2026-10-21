"""V17 真机取证：类型同一性 **LLM 裁判**（L2 判定 + L3 反例检验）。

跑法（需要 .env 里的真实 LLM 凭据）::

    python scripts/e2e_genre_judge_v17.py            # 3 个用例
    python scripts/e2e_genre_judge_v17.py --out out/v17

直接用路由函数 ``api.type_maker.genre_check``（就是 HTTP 端点本体），因此既走
真实 LLM，又不需要起 uvicorn/数据库。

用例设计（关键：证明"指标全过 ≠ 是这个类型"）
--------------------------------------------
1. ``mv_shaped``   ：8×3s + 每镜都有歌词文字 + 落拍 → L1 全过，期望裁判 match；
2. ``talk_slice``  ：6×3s + 满屏口播文案（指标同样全过）→ **L1 全过但裁判应判
   mismatch**，这就是 V17「错配可判」的核心证据；
3. ``long_take``   ：1×20s 无文字 → L1 已判不过（strict），核对裁判不会把它"洗白"。

输出：``out/v17/report.json``（含每例 measured / traits / judge / judge_detail）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _timeline(durations: list[float], texts: list[str] | None = None):
    """视频轨 + **字幕轨**（text_overlay_ratio 只统计 CAPTION/TEXT 轨）。"""
    from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

    start = 0.0
    clips = []
    caption_clips = []
    for index, duration in enumerate(durations):
        clips.append(
            Clip(
                id=f"c{index}",
                kind=ClipKind.VIDEO,
                asset_id=f"a{index}",
                track_id="v",
                start_sec=start,
                duration_sec=duration,
                source_offset_sec=0,
                speed=1,
                volume=1,
                opacity=1,
                keyframes=[],
            )
        )
        if texts and index < len(texts):
            caption_clips.append(
                Clip(
                    id=f"t{index}",
                    kind=ClipKind.CAPTION,
                    asset_id="",
                    track_id="cap",
                    start_sec=start,
                    duration_sec=duration,
                    source_offset_sec=0,
                    speed=1,
                    volume=1,
                    opacity=1,
                    keyframes=[],
                    text=texts[index],
                )
            )
        start += duration
    tracks = [Track(id="v", name="video", kind=ClipKind.VIDEO, index=0, clips=clips)]
    if caption_clips:
        tracks.append(
            Track(id="cap", name="字幕", kind=ClipKind.CAPTION, index=1, clips=caption_clips)
        )
    return Timeline(
        id="tl_v17",
        width=1080,
        height=1920,
        fps=30,
        duration_sec=start,
        tracks=tracks,
    )


LYRIC_LINES = [
    "夜色落进窗台",
    "我把心事折成纸船",
    "顺着风的方向",
    "漂到你住的那条街",
    "路灯一盏一盏",
    "把影子拉得很长",
    "原来想念也有形状",
    "像潮水一样",
]

TALK_LINES = [
    "大家好欢迎来到本期视频",
    "今天我们来聊一聊手机续航",
    "第一个结论是屏幕功耗占比最高",
    "第二个结论是后台唤醒很关键",
    "第三个结论是充电策略需要优化",
    "最后记得一键三连我们下期再见",
]


def _cases() -> list[dict]:
    return [
        {
            "id": "mv_shaped",
            "expect": "match",
            "note": "8×3s + 每镜一句歌词字幕 + 120BPM 落拍：L1 全过且形态就是 MV",
            "payload": {
                "timeline": _timeline([3.0] * 8, LYRIC_LINES).model_dump(mode="json"),
                "bpm": 120,
            },
        },
        {
            "id": "talk_slice",
            "expect": "mismatch",
            "note": "同样的 8×3s 与字幕覆盖率，但字幕是口播文案：L1 全过、形态不是 MV（V17 核心证据）",
            "payload": {
                "timeline": _timeline([3.0] * 6 + [3.0, 3.0], TALK_LINES + ["感谢观看", "我们下期再见"])
                .model_dump(mode="json"),
                "bpm": 120,
            },
        },
        {
            "id": "long_take",
            "expect": "mismatch",
            "note": "1×20s 无文字：L1 已判不过，核对裁判不会洗白",
            "payload": {
                "timeline": _timeline([20.0]).model_dump(mode="json"),
            },
        },
    ]


def _register() -> list[str]:
    from clipwright.category import (
        DigitalReviewPlugin,
        KichikuFastcutPlugin,
        KnowledgeLongformPlugin,
        LyricMvPlugin,
        VlogDailyPlugin,
    )
    from clipwright.category.registry import CategoryRegistry

    plugins = [
        KnowledgeLongformPlugin(),
        KichikuFastcutPlugin(),
        DigitalReviewPlugin(),
        VlogDailyPlugin(),
        LyricMvPlugin(),
    ]
    for plugin in plugins:
        try:
            CategoryRegistry.register(plugin)
        except Exception:  # noqa: BLE001 - 已注册则跳过
            pass
    return ["lyric_mv"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out/v17")
    parser.add_argument("--type", default="lyric_mv")
    args = parser.parse_args()

    _register()
    from clipwright.api.type_maker import genre_check

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for case in _cases():
        payload = dict(case["payload"])
        payload["enable_genre_judge"] = True  # 显式开启（D7 默认关）
        try:
            result = await genre_check(args.type, payload)
        except Exception as exc:  # noqa: BLE001 - 取证脚本按失败记录
            result = {"error": f"{type(exc).__name__}: {exc}"}
        result = {k: v for k, v in result.items() if k != "traits"}
        results.append({**case, "result": result})
        judge = result.get("judge", "?")
        detail = result.get("judge_detail") or {}
        confidence = detail.get("confidence", "?")
        print(f"[{case['id']}] judge={judge} confidence={confidence} expect={case['expect']}")
        for reason in (detail.get("reasons") or [])[:2]:
            print(f"  - {reason}")
        for item in (result.get("traits") or [])[:4]:
            print(f"  · {item.get('name')}={item.get('value')} passed={item.get('passed')}")

    correct = sum(
        1
        for item in results
        if item["result"].get("judge") in ("passed", "mismatch")
        and (item["result"].get("judge") == "mismatch") == (item["expect"] == "mismatch")
    )
    report = {
        "type_id": args.type,
        "judge_enabled": True,
        "cases": results,
        "verdict_expectation_met": f"{correct}/{len(results)}",
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n期望命中的判定：{correct}/{len(results)} → {path}")
    return 0 if correct == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
