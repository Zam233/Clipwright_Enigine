"""FontConfig — 跨平台字体发现与解析服务。

统一项目中所有 drawtext 调用的字体查找逻辑，避免每个 tool 各自搜一遍。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from clipwright.config import logger

# Windows 常见中文字体（按优先级排序）
_WINDOWS_CJK_FONTS = [
    "msyh.ttc",      # 微软雅黑
    "Microsoft YaHei UI.ttf",
    "SimSun.ttc",    # 宋体
    "SimSun.ttf",
    "SourceHanSansSC-Regular.otf",  # 思源黑体
    "SourceHanSerifSC-Regular.otf", # 思源宋体
    "yahei.ttf",
    "Deng.ttf",      # 等线
    "DengXian.ttf",
    "FZSTK.TTF",     # 方正舒体
    "FZYTK.TTF",     # 方正姚体
    "SIMLI.TTF",     # 隶书
    "SIMKAI.TTF",    # 楷体
]

# macOS 常见中文字体
_MACOS_CJK_FONTS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

# Linux 常见中文字体
_LINUX_CJK_FONTS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


class FontConfig:
    """字体配置服务。"""

    _cache: dict[str, str] = {}  # name → path 缓存

    @classmethod
    def get_font_path(cls, font_name: str = "") -> str:
        """获取指定字体或默认字体的文件路径。

        Args:
            font_name: 字体文件名（如 "msyh.ttc"），空字符串返回最优可用字体

        Returns:
            字体文件绝对路径，找不到返回空字符串
        """
        if font_name:
            # 指定字体
            cache_key = f"name:{font_name}"
            if cache_key in cls._cache:
                return cls._cache[cache_key]

            # 如果是绝对路径且存在
            if Path(font_name).exists():
                cls._cache[cache_key] = font_name
                return font_name

            # 在系统字体目录搜索
            path = cls._search_in_system_dirs(font_name)
            if path:
                cls._cache[cache_key] = path
                return path

            logger.warning("FontConfig: 字体 '%s' 未找到，回退到默认", font_name)

        # 默认字体
        if "__default__" in cls._cache:
            return cls._cache["__default__"]

        path = cls._find_default_font()
        cls._cache["__default__"] = path
        return path

    @classmethod
    def list_fonts(cls) -> list[dict[str, str]]:
        """扫描系统字体目录，列出所有可用字体。

        Returns:
            [{"name": "微软雅黑", "file": "msyh.ttc", "path": "C:\\Windows\\Fonts\\msyh.ttc"}, ...]
        """
        result: list[dict[str, str]] = []
        seen: set[str] = set()

        for font_dir in cls._get_system_font_dirs():
            if not font_dir.exists():
                continue
            try:
                for f in font_dir.iterdir():
                    if f.suffix.lower() in (".ttf", ".ttc", ".otf"):
                        if f.name not in seen:
                            seen.add(f.name)
                            display_name = f.stem.replace("_", " ").replace("-", " ")
                            result.append({
                                "name": display_name,
                                "file": f.name,
                                "path": str(f.resolve()),
                            })
            except PermissionError:
                continue

        result.sort(key=lambda x: x["file"])
        return result

    @classmethod
    def get_default_font_path(cls) -> str:
        """获取最优可用字体路径。"""
        return cls.get_font_path()

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    # ── 内部方法 ──────────────────────────────────────

    @classmethod
    def _search_in_system_dirs(cls, filename: str) -> str:
        """在系统字体目录中搜索指定文件名。"""
        for font_dir in cls._get_system_font_dirs():
            target = font_dir / filename
            if target.exists():
                return str(target.resolve())
        return ""

    @classmethod
    def _find_default_font(cls) -> str:
        """按平台找最优可用字体。"""
        if os.name == "nt":
            for fname in _WINDOWS_CJK_FONTS:
                path = cls._search_in_system_dirs(fname)
                if path:
                    return path
        elif os.name == "posix":
            if Path("/System/Library/Fonts").exists():  # macOS
                for path_str in _MACOS_CJK_FONTS:
                    if Path(path_str).exists():
                        return path_str
            else:  # Linux
                for path_str in _LINUX_CJK_FONTS:
                    if Path(path_str).exists():
                        return path_str

        logger.warning("FontConfig: 未找到任何中文字体")
        return ""

    @classmethod
    def _get_system_font_dirs(cls) -> list[Path]:
        """返回当前系统的字体目录列表。"""
        if os.name == "nt":
            return [Path(p) for p in [
                os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts",
                os.environ.get("LOCALAPPDATA", "") + "\\Microsoft\\Windows\\Fonts",
            ] if p]
        elif os.name == "posix":
            dirs = [
                "/System/Library/Fonts",
                "/Library/Fonts",
                os.path.expanduser("~/Library/Fonts"),
                "/usr/share/fonts",
                "/usr/local/share/fonts",
            ]
            return [Path(d) for d in dirs]
        return []

    @classmethod
    def ffmpeg_fontspec(cls, font_path: str) -> str:
        """将字体路径转为 FFmpeg drawtext 的 fontfile 参数。

        Returns:
            如 ":fontfile=C:/Windows/Fonts/msyh.ttc"，空字符串表示无法指定
        """
        if not font_path or not Path(font_path).exists():
            return ""
        escaped = font_path.replace("\\", "/").replace(":", "\\\\:")
        return f":fontfile={escaped}"
