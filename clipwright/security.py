"""安全工具 — ID 校验与路径安全检查（防路径遍历）。"""

from __future__ import annotations

import re
from pathlib import Path

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SecurityViolation(ValueError):
    """安全校验失败（非法 ID / 路径遍历），API 层应返回 400。"""


def is_safe_id(value: str) -> bool:
    """校验 ID 是否安全：仅字母/数字/下划线/连字符/点，且以字母或数字开头。"""
    return bool(value) and bool(_ID_PATTERN.match(value))


def validate_id(value: str, name: str = "id") -> str:
    """校验 ID，非法时抛 SecurityViolation。"""
    if not is_safe_id(value):
        raise SecurityViolation(
            f"非法 {name}: {value!r}（仅允许字母、数字、下划线、连字符、点，且以字母或数字开头）"
        )
    return value


def is_within(base: Path, target: Path) -> bool:
    """判断 target 解析后是否位于 base 内。"""
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    return target_resolved == base_resolved or base_resolved in target_resolved.parents


def safe_join(base: Path, user_path: str) -> Path:
    """将用户路径拼接到基目录并校验结果仍在基目录内，否则抛 SecurityViolation。"""
    target = base / user_path
    if not is_within(base, target):
        raise SecurityViolation(f"路径超出允许目录: {user_path!r}")
    return target.resolve()


def allowed_media_roots() -> list[Path]:
    """媒体/文件 API 的白名单目录（防任意文件读写）。"""
    from clipwright.config import settings

    # 锚定到项目根目录（clipwright 包的父目录），避免 CWD 依赖
    _base = Path(__file__).resolve().parent.parent
    return [
        _base / "renders",
        _base / "library",
        _base / "editor_projects",
        _base / "projects",
        _base / "PluginData",
        Path(settings.persona_dir).resolve(),
        Path(settings.tts_output_dir).resolve(),
    ]


def assert_allowed_path(path: Path) -> Path:
    """校验路径落在白名单目录之一内，否则抛 SecurityViolation（API 层返回 400）。"""
    if not any(is_within(root, path) for root in allowed_media_roots()):
        raise SecurityViolation("路径不在允许的目录内")
    return path


def assert_public_url(url: str) -> None:
    """校验 URL 不指向回环/私网/链路本地/元数据地址（防 SSRF）。

    解析主机名的全部 A/AAAA 记录，任一地址属于受限范围即拒绝。
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise SecurityViolation("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SecurityViolation(f"无法解析主机名: {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # is_global 白名单判定：拒绝回环/私网(含 CGNAT 100.64/10)/链路本地(含元数据
        # 169.254.169.254)/多播/保留/未指定等一切非全球可路由地址
        if not ip.is_global:
            raise SecurityViolation(f"禁止访问内网/回环地址: {ip}")
