"""Tiered frame extraction for visual material analysis."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("clipwright.tool.frame_extractor")


def _asset_value(asset: Any, name: str) -> Any:
    """Read a field from an object or dict-like material asset."""
    if hasattr(asset, name):
        return getattr(asset, name)
    getter = getattr(asset, "get", None)
    if callable(getter):
        return getter(name)
    return None


async def _extract_frame(source: str, timestamp: float, output_path: Path) -> bool:
    """Extract one frame with FFmpeg and report whether it succeeded."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        source,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=25)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        logger.debug("FFmpeg frame extraction timed out: source=%s ts=%.3f", source, timestamp)
        return False

    if process.returncode != 0:
        logger.debug(
            "FFmpeg frame extraction failed: source=%s ts=%.3f error=%s",
            source,
            timestamp,
            stderr.decode(errors="replace").strip(),
        )
        return False
    return output_path.exists()


async def _extract_from_source(
    source: str,
    duration: float,
    frame_count: int,
    output_dir: Path,
) -> list[str]:
    """Extract requested random frames from one video source."""
    frame_paths: list[str] = []
    for _ in range(frame_count):
        timestamp = random.uniform(0.1 * duration, 0.9 * duration)
        output_path = output_dir / f"frame_{uuid.uuid4().hex[:8]}.jpg"
        try:
            if await _extract_frame(source, timestamp, output_path):
                frame_paths.append(str(output_path))
            elif output_path.exists():
                output_path.unlink()
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("FFmpeg frame extraction error: %s", exc)
            if output_path.exists():
                output_path.unlink()
    return frame_paths


async def extract_frames(
    asset: Any,
    frame_count: int = 3,
    temp_dir: str | None = None,
) -> list[str]:
    """Extract representative frames using thumbnail, remote, then local tiers."""
    output_dir = Path(temp_dir or tempfile.gettempdir())
    output_dir.mkdir(parents=True, exist_ok=True)

    thumbnail_url = _asset_value(asset, "thumbnail_url")
    if thumbnail_url:
        thumbnail_path = output_dir / f"thumbnail_{uuid.uuid4().hex[:8]}.jpg"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(str(thumbnail_url), timeout=25.0)
                response.raise_for_status()
            thumbnail_path.write_bytes(response.content)
            return [str(thumbnail_path)]
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("Thumbnail download failed: %s", exc)
            try:
                os.remove(thumbnail_path)
            except FileNotFoundError:
                pass

    url = _asset_value(asset, "url")
    local_path = _asset_value(asset, "local_path")
    valid_local_path = (
        str(local_path) if local_path and Path(str(local_path)).is_file() else None
    )
    if not url and valid_local_path is None or frame_count <= 0:
        return []

    duration_value = _asset_value(asset, "duration_sec")
    if not duration_value:
        duration_value = _asset_value(asset, "duration")
    try:
        duration = float(duration_value or 60.0)
    except (TypeError, ValueError):
        duration = 60.0
    if duration <= 0:
        duration = 60.0

    if url:
        remote_frames = await _extract_from_source(
            str(url), duration, frame_count, output_dir
        )
        if remote_frames:
            return remote_frames
    if valid_local_path is not None:
        return await _extract_from_source(
            valid_local_path, duration, frame_count, output_dir
        )
    return []
