"""eternal-questioning-pv 引擎适配层：定位引擎目录、以子进程方式调用其 CLI。

**为什么用子进程而不是 import**：

1. 引擎是一个独立仓库（`https://github.com/Zam233/eternal-questioning-pv`），
   依赖按需安装（numpy/Pillow/OpenCV/SciPy，GPU 可选），把它 import 进主进程会把
   宿主的依赖版本绑死；
2. 引擎的 `audit.py` 以**退出码**表达结论（0 = 全过），子进程是对这一契约最直接的映射；
3. 崩了/超时只影响子进程，不会把主进程带走。

引擎路径解析优先级：工具入参 `engine_dir` > 插件配置 `engine_dir` > 环境变量
`CLIPWRIGHT_PV_ENGINE_DIR`。引擎目录里必须有 `_build/audit.py` 与 `_build/pv/build.py`。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

#: 环境变量名（与 README 的用法一致）
ENGINE_ENV = "CLIPWRIGHT_PV_ENGINE_DIR"
#: 引擎里两个关键入口（相对引擎根目录）
AUDIT_REL = Path("_build") / "audit.py"
BUILD_REL = Path("_build") / "pv" / "build.py"
#: 默认超时（秒）：审计是纯数据检查，卡住就是出错
DEFAULT_AUDIT_TIMEOUT = 300.0
DEFAULT_STILLS_TIMEOUT = 900.0


@dataclass(frozen=True)
class EnginePaths:
    root: Path

    @property
    def build_dir(self) -> Path:
        return self.root / "_build"

    @property
    def audit_script(self) -> Path:
        return self.root / AUDIT_REL

    @property
    def build_module(self) -> Path:
        return self.root / BUILD_REL

    def validate(self) -> str:
        """返回空串表示可用；否则返回人话原因。"""
        if not self.root.is_dir():
            return f"引擎目录不存在：{self.root}"
        if not self.audit_script.exists():
            return f"缺少审计脚本：{self.audit_script}"
        if not self.build_module.exists():
            return f"缺少渲染入口：{self.build_module}"
        return ""


def resolve_engine(
    *,
    explicit: str | os.PathLike[str] | None = None,
    config_dir: str | os.PathLike[str] | None = None,
) -> EnginePaths | None:
    """按「入参 > 插件配置 > 环境变量」解析引擎目录；都没有返回 None。"""
    for candidate in (explicit, config_dir, os.environ.get(ENGINE_ENV)):
        if candidate:
            return EnginePaths(Path(str(candidate)).expanduser())
    return None


def _tail(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text[-limit:]


def run_engine(
    paths: EnginePaths,
    args: Sequence[str],
    *,
    timeout: float,
    python: str | None = None,
) -> dict[str, Any]:
    """在引擎的 `_build` 目录下跑一条命令，返回结构化结果（不抛业务异常）。"""
    reason = paths.validate()
    if reason:
        return {"ok": False, "error": reason, "command": list(args), "exit_code": None}

    cmd = [python or sys.executable, *args]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(paths.build_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"引擎调用超时（>{timeout:.0f}s）",
            "command": cmd,
            "exit_code": None,
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"无法启动 Python：{exc}", "command": cmd, "exit_code": None}

    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": cmd,
        "cwd": str(paths.build_dir),
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


def probe_args(shots: str) -> list[str]:
    return ["-m", "pv.build", "probe", "--shots", shots]


def audit_args(shots: str, *, video: str = "", scan: bool = False) -> list[str]:
    args = ["audit.py", "--shots", shots]
    if video:
        args += ["--video", video]
    if scan:
        args.append("--scan")
    return args


def stills_args(shots: str, times: Sequence[float], out_dir: str) -> list[str]:
    joined = ",".join(str(t) for t in times)
    return ["-m", "pv.build", "stills", "--shots", shots, "--times", joined,
            "--stills-dir", out_dir]


__all__ = [
    "AUDIT_REL",
    "BUILD_REL",
    "DEFAULT_AUDIT_TIMEOUT",
    "DEFAULT_STILLS_TIMEOUT",
    "ENGINE_ENV",
    "EnginePaths",
    "audit_args",
    "probe_args",
    "resolve_engine",
    "run_engine",
    "stills_args",
]
