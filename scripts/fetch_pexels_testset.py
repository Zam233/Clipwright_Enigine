"""抓取 Pexels 真实素材作为评测集（R10 交付的"真实素材"入口）。

R1–R10 的所有验收都是合成素材 + 自写夹具，真实素材上的表现一直标注为"未验证"。
这个脚本就是补那块验证的第一步：把真实拍摄片段拉到本地，之后所有验收脚本
（`verify_all_metrics.py` 里的 R2/R4/R9、`demo_content_understanding.py`）都能直接吃它。

设计原则：

- **API key 只从环境变量读**（`PEXELS_API_KEY`），绝不写进仓库、绝不打进日志；
- **manifest 记录来源**（Pexels 页面、作者、原文件地址），素材不是"来路不明的东西"；
- **按需限高/限长**：真实素材动辄 4K/30s，索引一次要几分钟；
  `--max-height`/`--trim-sec` 让测试集保持"真实但可控"（裁剪是**真素材的截取**，
  不是合成，会如实写进 manifest 的 `trimmed_sec`）；
- Pexels 许可：免费用于商业与非商业，无需署名（仍建议保留作者信息）。

用法：

    $env:PEXELS_API_KEY = "..."          # PowerShell
    python scripts/fetch_pexels_testset.py --query "soccer match" --query "city night" \
        --per-query 2 --max-height 720 --trim-sec 10 --out .\\data\\pexels_testset
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "https://api.pexels.com/videos/search"


def pick_file(video: dict, max_height: int) -> tuple[dict | None, bool]:
    """挑一个不超过 `max_height` 的最高清版本。返回 `(文件, 是否超出上限)`。

    Pexels 每个视频给多档分辨率（竖屏时"高度"很大，如 2160×3840）。
    若**所有**版本都超过上限（常见于只提供大尺寸的竖屏素材），
    退回**最小的那一档**并如实标记 `capped=False`——不能像第一版那样
    在兜底分支里按高度升序取最后一个（那等于默默下载 155MB 的 4K 文件）。
    """
    mp4 = [f for f in (video.get("video_files") or [])
           if str(f.get("file_type", "")).startswith("video/mp4")]
    if not mp4:
        return None, False

    def _long_side(f: dict) -> int:
        return max(int(f.get("height") or 0), int(f.get("width") or 0))

    fitting = [f for f in mp4 if _long_side(f) <= max_height]
    if fitting:
        fitting.sort(key=_long_side)
        return fitting[-1], True
    mp4.sort(key=_long_side)
    return mp4[0], False


def search(query: str, per_page: int, orientation: str) -> list[dict]:
    import httpx

    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        raise SystemExit("缺少环境变量 PEXELS_API_KEY（不要把 key 写进仓库）")
    params = {"query": query, "per_page": max(1, per_page), "size": "medium"}
    if orientation:
        params["orientation"] = orientation
    resp = httpx.get(API, params=params,
                     headers={"Authorization": key}, timeout=30.0)
    if resp.status_code == 401:
        raise SystemExit("Pexels 返回 401：API key 无效")
    resp.raise_for_status()
    return list(resp.json().get("videos") or [])


def download(url: str, dest: Path) -> int:
    import httpx

    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
    return dest.stat().st_size


def trim(src: Path, seconds: float) -> bool:
    """只保留前 N 秒（真素材的截取；失败则保留原文件，不假装裁过）。"""
    if not shutil.which("ffmpeg"):
        return False
    tmp = src.with_name(src.stem + "_trim.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-t", f"{seconds}",
           "-i", str(src), "-c", "copy", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        # `-c copy` 在关键帧边界上可能失败，退化为重编码
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-t", f"{seconds}",
               "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
               "-pix_fmt", "yuv420p", "-an", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not tmp.exists():
            return False
    src.unlink(missing_ok=True)
    tmp.rename(src)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", required=True,
                    help="检索词（英文命中率更高，可重复多次）")
    ap.add_argument("--per-query", type=int, default=2)
    ap.add_argument("--max-height", type=int, default=720,
                    help="下载分辨率上限（越大索引越慢）")
    ap.add_argument("--trim-sec", type=float, default=0.0,
                    help="只保留前 N 秒（0=不裁）；真素材常 30s+，索引一次要几分钟")
    ap.add_argument("--orientation", default="", choices=("", "landscape", "portrait", "square"))
    ap.add_argument("--out", default="data/pexels_testset")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    entries: list[dict] = []
    if manifest_path.exists():
        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []

    print(f"输出目录：{out_dir}")
    for query in args.query:
        print(f"\n[检索] {query}（最多 {args.per_query} 条）")
        try:
            videos = search(query, args.per_query, args.orientation)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  检索失败：{exc}")
            continue
        if not videos:
            print("  没有结果")
            continue
        for video in videos:
            file_info, within_cap = pick_file(video, args.max_height)
            if not file_info:
                print(f"  跳过 id={video.get('id')}（没有 mp4 变体）")
                continue
            long_side = max(int(file_info.get("height") or 0), int(file_info.get("width") or 0))
            name = f"pexels_{video['id']}_{long_side}p.mp4"
            dest = out_dir / name
            if dest.exists():
                print(f"  已存在：{name}")
                continue
            if not within_cap:
                print(f"  注意 id={video.get('id')}：该素材没有低于 {args.max_height}p 的版本，"
                      f"取最小档 {long_side}p")
            try:
                size = download(str(file_info["link"]), dest)
            except Exception as exc:  # noqa: BLE001
                print(f"  下载失败 id={video.get('id')}：{exc}")
                continue
            trimmed = False
            if args.trim_sec > 0:
                trimmed = trim(dest, args.trim_sec)
            entries.append({
                "file": name,
                "query": query,
                "pexels_id": video.get("id"),
                "url": video.get("url"),
                "photographer": video.get("user", {}).get("name"),
                "duration_sec": video.get("duration"),
                "width": file_info.get("width"), "height": file_info.get("height"),
                "bytes": dest.stat().st_size,
                "trimmed_sec": args.trim_sec if trimmed else None,
                "license": "Pexels License（免费商用；建议保留作者信息）",
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            print(f"  OK {name}  {dest.stat().st_size / 1e6:.2f}MB"
                  f"{f'（已截取前 {args.trim_sec}s）' if trimmed else ''}")

    manifest_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"\nmanifest：{manifest_path}（累计 {len(entries)} 条）")
    print("素材来源与作者已记录；Pexels License 允许免费使用，建议保留作者信息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
