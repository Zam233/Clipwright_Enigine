"""波形可视化 — 从音频生成振幅峰值数据。"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from clipwright.config import logger


class WaveformGenerator:
    """生成音频波形数据（振幅峰值序列）。"""

    @staticmethod
    async def generate(
        audio_path: str,
        samples: int = 200,
    ) -> dict[str, Any]:
        """从音频文件生成波形采样数据。

        Args:
            audio_path: 音频文件路径
            samples: 波形采样点数（时间轴渲染精度）

        Returns:
            {"samples": [0.0-1.0], "peaks": [0.0-1.0], "duration_sec": float}
        """
        if samples < 10:
            samples = 10
        if samples > 10000:
            samples = 10000

        try:
            # 使用 FFmpeg astats 提取音量数据
            result = subprocess.run(
                ["ffmpeg", "-i", audio_path, "-af",
                 f"astats=measure_perchannel=0:length=0.01",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )

            # 从 stderr 解析音量值
            raw_values: list[float] = []
            for line in (result.stderr or "").split("\n"):
                if "RMS_level" in line or "Peak_level" in line:
                    try:
                        val = float(line.split(":")[-1].strip().replace(" dB", ""))
                        # 将 dB (-inf ~ 0) 映射到 0-1
                        normalized = max(0, min(1, (val + 60) / 60))
                        raw_values.append(normalized)
                    except (ValueError, IndexError):
                        pass

            if not raw_values:
                return await WaveformGenerator._fallback(audio_path, samples)

            return WaveformGenerator._resample(raw_values, samples)

        except FileNotFoundError:
            logger.warning("FFmpeg 不可用，无法生成波形")
            return {"samples": [], "peaks": [], "duration_sec": 0}
        except subprocess.TimeoutExpired:
            logger.warning("波形生成超时")
            return {"samples": [], "peaks": [], "duration_sec": 0}
        except Exception as e:
            logger.debug("波形生成异常: %s", e)
            return {"samples": [], "peaks": [], "duration_sec": 0}

    @staticmethod
    async def _fallback(audio_path: str, samples: int) -> dict[str, Any]:
        """保底方案：生成占位波形。"""
        return {
            "samples": [0.5] * samples,
            "peaks": [0.6] * samples,
            "duration_sec": 0,
        }

    @staticmethod
    def _resample(values: list[float], target: int) -> dict[str, Any]:
        """将音频数据重采样到目标采样点数。"""
        if not values:
            return {"samples": [], "peaks": [], "duration_sec": 0}

        step = max(1, len(values) // target)
        sampled: list[float] = []
        peaks: list[float] = []

        for i in range(0, len(values), step):
            chunk = values[i : i + step]
            avg = sum(chunk) / len(chunk)
            peak = max(chunk)
            sampled.append(avg)
            peaks.append(peak)
            if len(sampled) >= target:
                break

        return {
            "samples": sampled[:target],
            "peaks": peaks[:target],
        }
