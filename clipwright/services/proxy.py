"""代理工作流 — 生成低分辨率代理文件。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger


class ProxyGenerator:
    """为高分辨率视频生成低分辨率代理。"""

    PROXY_HEIGHTS = {  # 代理高度 → 标签
        360: "_proxy_360p",
        480: "_proxy_480p",
        540: "_proxy_540p",
        720: "_proxy_720p",
    }

    @staticmethod
    async def generate(
        input_path: str,
        proxy_height: int = 720,
        output_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """生成代理文件。

        Args:
            input_path: 原始素材路径
            proxy_height: 代理视频高度（默认 720）
            output_dir: 输出目录，默认同目录

        Returns:
            {"proxy_path": str, "original": str, "height": int}
        """
        src = Path(input_path)
        if not src.exists():
            return {"error": f"文件不存在: {input_path}"}

        suffix = ProxyGenerator.PROXY_HEIGHTS.get(proxy_height, f"_proxy_{proxy_height}p")
        out_dir = Path(output_dir) if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        proxy_path = str(out_dir / f"{src.stem}{suffix}{src.suffix}")

        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-vf", f"scale=-2:{proxy_height}",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "aac", "-ar", "44100", "-ac", "2",
                 "-movflags", "+faststart", proxy_path],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                return {"error": f"代理生成失败: {result.stderr[:300]}"}
            logger.info("代理生成: %s → %s (height=%d)", input_path, proxy_path, proxy_height)
            return {
                "proxy_path": proxy_path,
                "original": input_path,
                "height": proxy_height,
                "file_size": Path(proxy_path).stat().st_size,
            }
        except FileNotFoundError:
            return {"error": "ffmpeg not found"}
        except subprocess.TimeoutExpired:
            return {"error": "代理生成超时"}
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def switch_to_proxy(timeline_json: dict, proxy_suffix: str = "_proxy_720p") -> dict:
        """将 Timeline 中的素材路径替换为代理路径。"""
        import copy
        tl = copy.deepcopy(timeline_json)
        for track in tl.get("tracks", []):
            for clip in track.get("clips", []):
                asset_id = clip.get("asset_id", "")
                if asset_id and not asset_id.startswith("proxy_"):
                    p = Path(asset_id)
                    proxy_name = f"{p.stem}{proxy_suffix}{p.suffix}"
                    proxy_candidate = p.parent / proxy_name
                    if proxy_candidate.exists():
                        clip["asset_id"] = str(proxy_candidate)
        return tl
