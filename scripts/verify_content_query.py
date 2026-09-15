"""R4 验收（M1）：素材内容问答命中率 ≥ 80%。

分两部分，**刻意分开报**：

- **A 组「检索层」（20 题）**：用**注入的确定性索引**（内容已知、不跑任何模型）考查
  检索/过滤/时间定位本身。这样"标签质量"不会污染"检索质量"的测量——R2 已实测
  合成素材上的标签不可信，混在一起量就说不清是谁的问题。
- **B 组「端到端」（10 题）**：用 `build_index` 真跑合成视频（含音轨/黑场/纯白段），
  问**由素材构造方式决定的事实**（黑场在哪、第 X 秒是什么、大响段在哪个镜头）。
  这组走完整链路：切分 → 抽帧 → 统计 → 音频事件 → 查询。

判定：命中 = 期望镜头 ref 出现在 **top-3**（`shot_at`/时间类题目要求精确命中）。
门槛：合计 **≥ 80%**（计划 §4 的 R4 验收）。

用法：
    python scripts/verify_content_query.py [--embed] [--min-hit 0.8] [--keep]
    --embed  额外跑一遍带嵌入的检索（会在语义改写题上更好，但更慢且依赖模型可用）
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats, format_shot_ref  # noqa: E402
from clipwright.services import content_index as ci  # noqa: E402
from clipwright.services import content_query as cq  # noqa: E402

SHA_A = "a1" * 32
SHA_B = "b2" * 32
SHA_C = "c3" * 32


@dataclass
class Q:
    text: str
    check: Callable[[dict[str, Any]], bool]
    group: str = "A"
    kind: str = "keyword"
    note: str = ""
    sha: str = ""          # 时间题必须指定素材（否则会命中"第一个包含该时刻的素材"）


@dataclass
class Result:
    q: Q
    ok: bool
    detail: str = ""


def _hits_refs(hits: list[dict[str, Any]]) -> list[str]:
    return [h["ref"] for h in hits]


def _has(expected: str) -> Callable[[dict[str, Any]], bool]:
    def _check(res: dict[str, Any]) -> bool:
        return expected in _hits_refs(res.get("hits") or [])
    return _check


def _has_any(*expected: str) -> Callable[[dict[str, Any]], bool]:
    """多素材都可能有同类镜头时（如"哪些镜头有人脸"），命中任一即可。"""
    def _check(res: dict[str, Any]) -> bool:
        refs = set(_hits_refs(res.get("hits") or []))
        return bool(refs & set(expected))
    return _check


def _exact(expected: str) -> Callable[[dict[str, Any]], bool]:
    def _check(res: dict[str, Any]) -> bool:
        hit = res.get("hit")
        return bool(hit) and hit["ref"] == expected
    return _check


# ── A 组：注入的确定性索引 ────────────────────────────────


def _shot(idx: int, t0: float, t1: float, **kw: Any) -> ShotRecord:
    return ShotRecord(index=idx, t0=t0, t1=t1,
                      visual=kw.pop("visual", {}), speech=kw.pop("speech", {}),
                      audio_events=kw.pop("audio_events", []),
                      stats=ShotStats(**kw.pop("stats", {})))


def _inject_indexes() -> None:
    """三段"内容已知"的素材（描述/标签/语音/事件都是写死的，不依赖模型）。"""
    a = ContentIndex(sha256=SHA_A, filename="咖啡店探店.mp4", duration_sec=24.0,
                     width=1920, height=1080, shots=[
        _shot(0, 0.0, 3.0, visual={"description": "咖啡杯特写，手部动作", "tags": ["咖啡杯", "食物"],
                                   "shot_size": "close", "has_face": False},
              stats={"brightness": 120.0, "sharpness": 900.0}),
        _shot(1, 3.0, 7.5, visual={"description": "店主介绍价格", "tags": ["人物"],
                                   "shot_size": "medium", "has_face": True},
              speech={"text": "这杯手冲的价格是三十八元", "keywords": ["手冲", "价格"]},
              stats={"brightness": 115.0, "sharpness": 820.0}),
        _shot(2, 7.5, 9.0, visual={"description": "", "tags": [], "shot_size": "unknown"},
              stats={"brightness": 3.0, "is_black": True, "frame_count": 2,
                     "underexposed_ratio": 0.98}),
        _shot(3, 9.0, 12.0, visual={"description": "拉花过程", "tags": ["咖啡杯"],
                                    "shot_size": "close", "camera_motion": "low"},
              stats={"brightness": 130.0, "sharpness": 700.0, "motion": 4.0, "frame_count": 2}),
        _shot(4, 12.0, 14.0, visual={"description": "街道空镜", "tags": ["室外", "空镜"],
                                     "shot_size": "wide", "camera_motion": "high"},
              stats={"brightness": 180.0, "sharpness": 600.0, "motion": 26.0, "frame_count": 2}),
    ])
    b = ContentIndex(sha256=SHA_B, filename="球赛集锦.mp4", duration_sec=30.0,
                     width=1920, height=1080, shots=[
        _shot(0, 0.0, 5.0, visual={"description": "球场全景", "tags": ["运动", "室外"],
                                   "shot_size": "wide"},
              stats={"brightness": 150.0, "sharpness": 750.0}),
        _shot(1, 5.0, 8.0, visual={"description": "球员射门瞬间", "tags": ["人物", "运动"],
                                   "shot_size": "medium", "has_face": True},
              speech={"text": "这一脚射门太突然了", "keywords": ["射门"]},
              stats={"brightness": 140.0, "motion": 30.0, "frame_count": 2}),
        _shot(2, 8.0, 10.5, visual={"description": "球进了，观众欢呼", "tags": ["多人", "运动"],
                                    "shot_size": "wide", "has_face": False},
              speech={"text": "球进了！绝杀！", "keywords": ["球进了", "绝杀"]},
              audio_events=[{"kind": "loud", "t0": 8.2, "t1": 9.6},
                            {"kind": "onset", "t0": 8.2, "t1": 8.5}],
              stats={"brightness": 160.0, "motion": 22.0, "frame_count": 2}),
        _shot(3, 10.5, 12.0, visual={"description": "教练表情特写", "tags": ["人脸特写"],
                                     "shot_size": "extreme_close", "has_face": True},
              stats={"brightness": 125.0, "sharpness": 880.0}),
    ])
    c = ContentIndex(sha256=SHA_C, filename="教程录制.mp4", duration_sec=40.0,
                     width=1920, height=1080, shots=[
        _shot(0, 0.0, 6.0, visual={"description": "屏幕录制，软件界面", "tags": ["屏幕录制"],
                                   "shot_size": "full"},
              speech={"text": "第一步先打开设置面板", "keywords": ["第一步", "设置"]},
              stats={"brightness": 200.0, "sharpness": 950.0, "overexposed_ratio": 0.5}),
        _shot(1, 6.0, 20.0, visual={"description": "屏幕录制，参数调节", "tags": ["屏幕录制"],
                                    "shot_size": "full"},
              speech={"text": "注意这里的数值不要填太大，否则会出问题",
                      "keywords": ["注意", "数值"]},
              stats={"brightness": 205.0, "sharpness": 940.0, "overexposed_ratio": 0.6}),
        _shot(2, 20.0, 26.0, visual={"description": "人物出镜总结", "tags": ["人物"],
                                     "shot_size": "medium", "has_face": True},
              speech={"text": "最后总结一下，这个方法性价比很高", "keywords": ["总结", "性价比"]},
              stats={"brightness": 118.0, "sharpness": 800.0}),
        _shot(3, 26.0, 40.0, visual={"description": "静置桌面空镜", "tags": ["空镜"],
                                     "shot_size": "full", "camera_motion": "static"},
              stats={"brightness": 100.0, "sharpness": 300.0, "motion": 0.5, "frame_count": 2}),
    ])
    for idx in (a, b, c):
        ci.save_index(idx)


def _questions_a() -> list[Q]:
    """A 组：**逐素材提问**（真实使用里用户也是在某个素材上问）。"""
    r = lambda sha, i: format_shot_ref(sha, i)  # noqa: E731
    return [
        # 咖啡店素材
        Q("咖啡店素材里找咖啡杯的特写", _has(r(SHA_A, 0)), kind="keyword", sha=SHA_A),
        Q("店主介绍价格的镜头", _has(r(SHA_A, 1)), kind="speech", sha=SHA_A),
        Q("黑场在哪一段", _has(r(SHA_A, 2)), kind="structural", note="is_black", sha=SHA_A),
        Q("拉花的过程", _has(r(SHA_A, 3)), kind="keyword", sha=SHA_A),
        Q("街道空镜", _has(r(SHA_A, 4)), kind="keyword", sha=SHA_A),
        Q("哪些镜头有人脸", _has(r(SHA_A, 1)), kind="structural", note="has_face", sha=SHA_A),
        Q("最长的那一个镜头", _has(r(SHA_A, 1)), kind="structural", note="时长排序", sha=SHA_A),
        Q("运动最剧烈的镜头", _has(r(SHA_A, 4)), kind="structural", note="motion 排序", sha=SHA_A),
        Q("第 4 秒是什么镜头", _exact(r(SHA_A, 1)), kind="shot_at", sha=SHA_A),
        Q("第 8.5 秒是什么镜头", _exact(r(SHA_A, 2)), kind="shot_at", sha=SHA_A),
        # 球赛素材
        Q("球员射门的镜头", _has(r(SHA_B, 1)), kind="keyword", sha=SHA_B),
        Q("球进了观众欢呼", _has(r(SHA_B, 2)), kind="keyword", sha=SHA_B),
        Q("教练的表情特写", _has(r(SHA_B, 3)), kind="keyword", sha=SHA_B),
        Q("有掌声或欢呼声的镜头", _has(r(SHA_B, 2)), kind="structural", note="audio loud", sha=SHA_B),
        Q("第 9 秒是什么镜头", _exact(r(SHA_B, 2)), kind="shot_at", sha=SHA_B),
        # 教程素材
        Q("打开设置面板那一段", _has(r(SHA_C, 0)), kind="speech", sha=SHA_C),
        Q("提到注意事项的那段", _has(r(SHA_C, 1)), kind="speech", sha=SHA_C),
        Q("最后总结性价比", _has(r(SHA_C, 2)), kind="speech", sha=SHA_C),
        Q("静置的桌面空镜", _has(r(SHA_C, 3)), kind="keyword", sha=SHA_C),
        Q("过曝的镜头", _has(r(SHA_C, 1)), kind="structural", note="overexposed", sha=SHA_C),
        Q("最模糊的镜头", _has(r(SHA_C, 3)), kind="structural", note="锐度排序", sha=SHA_C),
        Q("第 30 秒是什么镜头", _exact(r(SHA_C, 3)), kind="shot_at", sha=SHA_C),
    ]


# ── B 组：真跑合成视频 ────────────────────────────────────


def _make_videos(tmp: Path) -> dict[str, dict[str, Any]]:
    """三段合成视频：配色/时长/音轨写死 → 真值由构造方式决定。"""
    out: dict[str, dict[str, Any]] = {}
    # 1) 黑 → 红 → 白（纯色硬切，无音轨）
    v1 = tmp / "solid.mp4"
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", "1.2", "-i", "color=c=black:s=160x120:r=10",
        "-f", "lavfi", "-t", "1.0", "-i", "color=c=red:s=160x120:r=10",
        "-f", "lavfi", "-t", "1.0", "-i", "color=c=white:s=160x120:r=10",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(v1),
    ], capture_output=True, text=True)
    if r.returncode == 0:
        out["solid"] = {"path": v1, "dur": 3.2, "black": 0.0, "white": 2.2}
    # 2) 红 → 蓝（带音轨：1s 静音 + 1s 大响）
    v2 = tmp / "loud.mp4"
    wav = tmp / "loud.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
        "-f", "lavfi", "-i", "sine=frequency=420:sample_rate=16000:duration=1",
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
        "-c:a", "pcm_s16le", str(wav),
    ], capture_output=True)
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", "1.0", "-i", "color=c=red:s=160x120:r=10",
        "-f", "lavfi", "-t", "1.0", "-i", "color=c=blue:s=160x120:r=10",
        "-i", str(wav),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(v2),
    ], capture_output=True, text=True)
    if r.returncode == 0:
        out["loud"] = {"path": v2, "dur": 2.0, "loud_at": 1.0}
    return out


async def _build_b(tmp: Path, videos: dict[str, dict[str, Any]]) -> dict[str, Any]:
    built: dict[str, Any] = {}
    for name, info in videos.items():
        idx = await ci.build_index(info["path"], force=True, mirror=False)
        built[name] = idx
    return built


def _shot_ref_containing(idx: Any, t: float) -> str | None:
    """由**素材构造方式**给出的时刻 → 该时刻所在镜头的 ref（不依赖索引的判定）。"""
    shot = idx.shot_at(t)
    return format_shot_ref(idx.sha256, shot.index) if shot else None


def _questions_b(built: dict[str, Any]) -> list[Q]:
    """B 组：期望值全部来自**视频构造方式**（黑段 0–1.2s、白段 2.2–3.2s、
    大响音在 1.0–2.0s…），而不是从索引里读出来——否则就变成"索引自己考自己"。"""
    qs: list[Q] = []
    if "solid" in built:
        idx = built["solid"]
        sha = idx.sha256
        # 构造：黑 0-1.2 / 红 1.2-2.2 / 白 2.2-3.2
        black_ref = _shot_ref_containing(idx, 0.6)
        white_ref = _shot_ref_containing(idx, 2.7)
        first_ref = _shot_ref_containing(idx, 0.3)
        longest_ref = black_ref          # 1.2s 段长于两个 1.0s 段
        if black_ref:
            qs.append(Q("这段素材里有没有黑场，在第几秒", _has(black_ref),
                        group="B", kind="structural", note="黑段在 0–1.2s", sha=sha))
        if white_ref:
            qs.append(Q("哪一段是全白的画面", _has(white_ref),
                        group="B", kind="structural", note="白段在 2.2–3.2s", sha=sha))
            qs.append(Q("最亮的那个镜头", _has(white_ref),
                        group="B", kind="structural", note="白段最亮", sha=sha))
        if first_ref:
            qs.append(Q("第一个镜头是什么内容", _has(first_ref),
                        group="B", kind="structural", note="t=0.3 所在镜头", sha=sha))
        if longest_ref:
            qs.append(Q("最长的镜头是哪个", _has(longest_ref),
                        group="B", kind="structural", note="黑段 1.2s 最长", sha=sha))
        qs.append(Q("第 1.5 秒是什么镜头", _exact(_shot_ref_containing(idx, 1.5) or ""),
                    group="B", kind="shot_at", note="t=1.5 落在红段", sha=sha))
        if idx.shots:
            qs.append(Q("按时间列出所有镜头",
                        lambda res, d=idx.duration_sec: bool(res.get("hits")) and (
                            max(h["t1"] for h in res["hits"]) >= d - 0.6),
                        group="B", kind="structural", note="清单覆盖到片尾", sha=sha))
    if "loud" in built:
        idx = built["loud"]
        sha = idx.sha256
        # 构造：红 0-1.0 / 蓝 1.0-2.0，音轨 0-1.0 静音、1.0-2.0 大响
        loud_ref = _shot_ref_containing(idx, 1.6)
        if loud_ref:
            qs.append(Q("有掌声或突然变响的那一段", _has(loud_ref),
                        group="B", kind="structural", note="大响在 1.0–2.0s", sha=sha))
            qs.append(Q("最亮的那个镜头", _has(loud_ref),
                        group="B", kind="structural", note="蓝段比红段亮", sha=sha))
        qs.append(Q("素材里一共有多少个镜头",
                    lambda res, n=len(idx.shots): len(res.get("hits") or []) == n,
                    group="B", kind="structural", note="清单数量（索引自洽性）", sha=sha))
    return qs


# ── 主流程 ────────────────────────────────────────────────


async def _run_question(q: Q, embed: bool) -> Result:
    try:
        if q.kind == "shot_at":
            res = await _shot_at_by_question(q)
        else:
            # 面向单素材的问题必须限定素材（真实使用里用户也是在某个素材上问），
            # 否则"一共有多少镜头"会跨素材汇总，与题目期望不一致
            res = await cq.search_shots(q.text, top_k=3, use_embeddings=embed, rerank=False,
                                        sha256=q.sha)
        ok = q.check(res)
        hits = res.get("hits") or ([res["hit"]] if res.get("hit") else [])
        detail = "、".join(f"{h['ref']}({h.get('score', 0):.2f})" for h in hits[:3])
        return Result(q, ok, detail)
    except Exception as exc:
        return Result(q, False, f"异常：{exc}")


async def _shot_at_by_question(q: Q) -> dict[str, Any]:
    """从题目文本里解出时间，再调 shot_at（时间题必须走这条路径）。"""
    import re

    m = re.search(r"第\s*([\d.]+)\s*秒", q.text)
    if not m:
        return {"hit": None}
    t = float(m.group(1))
    hit = await cq.shot_at(t, sha256=q.sha)
    return {"hit": hit}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true", help="额外跑一遍带嵌入的检索")
    ap.add_argument("--min-hit", type=float, default=0.80)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        print("需要本机 ffmpeg")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="clipwright_cq_"))
    ci.settings.library_dir = tmp / "library"
    ci.settings.project_dir = tmp / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False
    ci.settings.content_index_clip_enabled = False   # A 组用注入索引；B 组不需要标签
    ci.settings.content_index_visual_llm = False

    try:
        _inject_indexes()
        videos = _make_videos(tmp)
        built = await _build_b(tmp, videos)
        questions = _questions_a() + _questions_b(built)
        print(f"题目总数 {len(questions)}（A 组检索层 {len(_questions_a())}，"
              f"B 组端到端 {len(questions) - len(_questions_a())}）"
              f"{'，含嵌入' if args.embed else '，关键词+结构化'}")

        results: list[Result] = []
        for q in questions:
            results.append(await _run_question(q, args.embed))

        print()
        for grp in ("A", "B"):
            sub = [r for r in results if r.q.group == grp]
            if not sub:
                continue
            print(f"--- {grp} 组 ---")
            for r in sub:
                flag = "OK " if r.ok else "MISS"
                print(f"  [{flag}] {r.q.text}   （{r.q.kind}）")
                if not r.ok:
                    print(f"         命中：{r.detail}")
            hits = sum(1 for r in sub if r.ok)
            print(f"  {grp} 组命中 {hits}/{len(sub)} = {hits / len(sub) * 100:.1f}%")

        total = len(results)
        ok = sum(1 for r in results if r.ok)
        rate = ok / total if total else 0.0
        print()
        print(f"合计命中 {ok}/{total} = **{rate * 100:.1f}%**（门槛 ≥{args.min_hit * 100:.0f}%）")

        # 按类型细分（便于定位是哪一类检索不行）
        by_kind: dict[str, list[bool]] = {}
        for r in results:
            by_kind.setdefault(r.q.kind, []).append(r.ok)
        print("按类型：" + "；".join(
            f"{k} {sum(v)}/{len(v)}" for k, v in sorted(by_kind.items())))

        if rate < args.min_hit:
            print()
            print("CONTENT QUERY VERIFY FAIL")
            return 1
        print()
        print("CONTENT QUERY VERIFY PASS")
        return 0
    finally:
        if args.keep:
            print(f"[keep] {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
