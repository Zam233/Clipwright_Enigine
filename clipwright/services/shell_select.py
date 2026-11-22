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


def select_shell() -> ShellSpec:
    if os.name == "nt":
        pwsh = _which("pwsh.exe", "pwsh")
        if pwsh:
            return ShellSpec(
                "pwsh", pwsh,
                (pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
            )
        powershell = _which("powershell.exe", "powershell")
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
    sh = _which("sh")
    if sh:
        return ShellSpec("sh", sh, (sh, "-lc"), fallback=True)
    raise ShellUnavailable("未找到 bash 或 sh")
