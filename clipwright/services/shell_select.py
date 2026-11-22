"""平台 shell 选择（PRD §7.5）。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


class ShellUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ShellSpec:
    name: str
    executable: str
    prefix: tuple[str, ...]
    fallback: bool = False


def _which(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return ""


def _explicit(profile: str, fallback: bool) -> ShellSpec | None:
    name = str(profile or "auto").lower()
    if name in ("", "auto"):
        return None
    exe = {"pwsh": ("pwsh.exe", "pwsh"),
           "powershell": ("powershell.exe", "powershell"),
           "bash": ("bash",),
           "sh": ("sh",)}[name]
    path = _which(*exe)
    if path:
        prefix = {
            "pwsh": (path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
            "powershell": (path, "-NoProfile", "-NonInteractive", "-Command"),
            "bash": (path, "-lc"),
            "sh": (path, "-lc"),
        }[name]
        return ShellSpec(name, path, prefix)
    if not fallback:
        raise ShellUnavailable(f"指定 shell 不可用: {name}")
    return None


def select_shell(*, profile: str = "auto", fallback: bool = True) -> ShellSpec:
    explicit = _explicit(profile, fallback)
    if explicit is not None:
        return explicit
    if os.name == "nt":
        pwsh = _which("pwsh.exe", "pwsh")
        if pwsh:
            return ShellSpec(
                "pwsh", pwsh,
                (pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
            )
        powershell = _which("powershell.exe", "powershell") if fallback else ""
        if powershell:
            return ShellSpec(
                "powershell", powershell,
                (powershell, "-NoProfile", "-NonInteractive", "-Command"),
                fallback=True,
            )
        raise ShellUnavailable("未找到 PowerShell 7（pwsh）或 Windows PowerShell 5.1（powershell）")
    bash = _which("bash")
    if bash:
        return ShellSpec("bash", bash, (bash, "-lc"))
    sh = _which("sh") if fallback else ""
    if sh:
        return ShellSpec("sh", sh, (sh, "-lc"), fallback=True)
    raise ShellUnavailable("未找到 bash 或 sh")
