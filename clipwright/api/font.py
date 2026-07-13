"""字体 API — 查询系统字体、解析字体路径。"""

from __future__ import annotations

from fastapi import APIRouter

from clipwright.services.fontconfig import FontConfig

router = APIRouter(prefix="/api/fonts", tags=["fonts"])


@router.get("/list")
async def list_fonts() -> dict:
    """列出系统所有可用字体。"""
    fonts = FontConfig.list_fonts()
    return {"fonts": fonts, "count": len(fonts)}


@router.get("/default")
async def get_default_font() -> dict:
    """获取默认字体路径。"""
    path = FontConfig.get_default_font_path()
    fontspec = FontConfig.ffmpeg_fontspec(path)
    return {"path": path, "fontspec": fontspec, "available": bool(path)}


@router.get("/resolve")
async def resolve_font(name: str = "") -> dict:
    """解析字体名或路径，返回真实的字体路径和 ffmpeg 参数。

    Args:
        name: 字体文件名（如 msyh.ttc）或空字符串（返回默认）
    """
    path = FontConfig.get_font_path(name)
    fontspec = FontConfig.ffmpeg_fontspec(path)
    return {"name": name or "(default)", "path": path, "fontspec": fontspec, "found": bool(path)}


@router.post("/clear-cache")
async def clear_cache() -> dict:
    """清除字体缓存（安装新字体后调用）。"""
    FontConfig.clear_cache()
    return {"status": "cache cleared"}
