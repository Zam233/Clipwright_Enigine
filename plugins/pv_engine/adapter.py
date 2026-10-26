"""PV 引擎适配层：定位引擎目录、以子进程方式调用其 CLI / 内省接口。

**为什么用子进程而不是 import**：

1. 引擎是独立仓库，依赖（numpy/Pillow/OpenCV/SciPy，GPU 可选）不该绑死宿主进程；
2. 引擎的 `audit.py` 以**退出码**表达结论（0 = 全过），子进程是对这一契约最直接的映射；
3. 崩了/超时只影响子进程，不会把主进程带走。

**双布局（二期）**：引擎有两种合法形态，适配层都要认：

============  ==================================================  =======================
布局          识别特征                                            命令
============  ==================================================  =======================
``package``   ``<root>/engine/cli.py``                             ``python -m engine.cli``（cwd=root）
``build``     ``<root>/_build/audit.py`` + ``_build/pv/build.py``   ``python audit.py`` / ``-m pv.build``（cwd=_build）
============  ==================================================  =======================

v1 只认 ``build``（那是上游 PV 仓库的内部目录），于是把**抽出来的通用引擎**
（``K:\\video engine``，只有 ``engine/`` 包）判成"缺少审计脚本"。二期起按特征探测，
两者都认；认不出来时如实返回**两条**候选原因（不是只报第一条）。

引擎路径解析优先级：工具入参 ``engine_dir`` > 插件配置 ``engine_dir`` > 环境变量
``CLIPWRIGHT_PV_ENGINE_DIR``。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

#: 环境变量名（与 README 的用法一致）
ENGINE_ENV = "CLIPWRIGHT_PV_ENGINE_DIR"
#: 旧布局（上游 PV 仓库）两个关键入口
AUDIT_REL = Path("_build") / "audit.py"
BUILD_REL = Path("_build") / "pv" / "build.py"
#: 新布局（抽出来的通用引擎）入口
CLI_REL = Path("engine") / "cli.py"

LAYOUT_PACKAGE = "package"
LAYOUT_BUILD = "build"
LAYOUT_UNKNOWN = ""

#: 默认超时（秒）：审计是纯数据检查，卡住就是出错
DEFAULT_AUDIT_TIMEOUT = 300.0
DEFAULT_STILLS_TIMEOUT = 900.0
DEFAULT_RENDER_TIMEOUT = 3600.0
#: 内省（doctor）只 import 引擎模块，不渲染，给短超时即可
DEFAULT_DOCTOR_TIMEOUT = 120.0


@dataclass(frozen=True)
class EnginePaths:
    root: Path

    # ---------------------------------------------------------------- layout
    @property
    def layout(self) -> str:
        """``package`` / ``build`` / 空串（两者都不像）。"""
        if (self.root / CLI_REL).is_file():
            return LAYOUT_PACKAGE
        if self.root.is_dir() and (self.root / AUDIT_REL).is_file() \
                and (self.root / BUILD_REL).is_file():
            return LAYOUT_BUILD
        return LAYOUT_UNKNOWN

    @property
    def build_dir(self) -> Path:
        """向上游兼容的名字：命令的工作目录。

        package 布局在仓库根（``python -m engine.cli`` 必须能 import 到 engine 包），
        build 布局在 ``_build``（``audit.py`` 与 ``pv`` 包都在那儿）。
        """
        return self.root if self.layout == LAYOUT_PACKAGE else self.root / "_build"

    @property
    def audit_script(self) -> Path:
        return self.root / AUDIT_REL

    @property
    def build_module(self) -> Path:
        return self.root / BUILD_REL

    @property
    def cli_script(self) -> Path:
        return self.root / CLI_REL

    def validate(self) -> str:
        """返回空串表示可用；否则返回**人话原因**（两条候选都写出来）。"""
        if not self.root.is_dir():
            return f"引擎目录不存在：{self.root}"
        layout = self.layout
        if layout == LAYOUT_PACKAGE:
            return ""
        if layout == LAYOUT_BUILD:
            return ""
        return (
            f"未识别为 PV 引擎目录：{self.root}\n"
            f"  · 既没有 {CLI_REL.as_posix()}（抽出来的通用引擎）\n"
            f"  · 也没有 {AUDIT_REL.as_posix()} + {BUILD_REL.as_posix()}（上游 PV 仓库）"
        )

    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "layout": self.layout or "unknown",
            "cwd": str(self.build_dir),
            "audit_script": str(self.audit_script),
            "build_module": str(self.build_module),
            "cli_script": str(self.cli_script),
        }


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


# ------------------------------------------------------------------ process
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
    """在引擎的工作目录跑一条命令，返回结构化结果（不抛业务异常）。"""
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
        "layout": paths.layout,
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


# ------------------------------------------------------------------ argv
def _cli(kind: str) -> list[str]:
    return ["-m", "engine.cli"] if kind == LAYOUT_PACKAGE else ["-m", "pv.build"]


def probe_args(shots: str, kind: str = LAYOUT_BUILD) -> list[str]:
    """项目体检：package 布局走 ``info``，build 布局走 ``probe``。"""
    if kind == LAYOUT_PACKAGE:
        return ["-m", "engine.cli", "info", shots]
    return ["-m", "pv.build", "probe", "--shots", shots]


def audit_args(shots: str, *, video: str = "", scan: bool = False,
               kind: str = LAYOUT_BUILD) -> list[str]:
    """规则审计。``video``/``scan`` 只有上游 build 布局支持。"""
    if kind == LAYOUT_PACKAGE:
        return ["-m", "engine.cli", "check", shots]
    args = ["audit.py", "--shots", shots]
    if video:
        args += ["--video", video]
    if scan:
        args.append("--scan")
    return args


def stills_args(shots: str, times: Sequence[float], out_dir: str,
                kind: str = LAYOUT_BUILD) -> list[str]:
    joined = ",".join(str(t) for t in times)
    if kind == LAYOUT_PACKAGE:
        return ["-m", "engine.cli", "stills", shots, "--times", joined, "--out", out_dir]
    return ["-m", "pv.build", "stills", "--shots", shots, "--times", joined,
            "--stills-dir", out_dir]


def render_args(
    shots: str,
    out: str = "",
    *,
    codec: str = "nvenc",
    crf: int | str = 18,
    workers: int = 8,
    start: int = 0,
    end: int = 0,
    preset: str = "medium",
    kind: str = LAYOUT_PACKAGE,
) -> list[str]:
    args = ["-m", "engine.cli", "render", shots]
    if out:
        args += ["--out", out]
    args += ["--workers", str(int(workers)), "--crf", str(crf),
             "--codec", str(codec), "--preset", str(preset)]
    if int(start or 0) > 0:
        args += ["--start", str(int(start))]
    if int(end or 0) > 0:
        args += ["--end", str(int(end))]
    return args


def beats_args(audio: str, out: str = "", kind: str = LAYOUT_PACKAGE) -> list[str]:
    args = ["-m", "engine.cli", "beats", audio]
    if out:
        args += ["--out", out]
    return args


def init_args(directory: str, preset: str = "lyric", force: bool = False,
              kind: str = LAYOUT_PACKAGE) -> list[str]:
    args = ["-m", "engine.cli", "init", directory, "--preset", preset]
    if force:
        args.append("--force")
    return args


# ------------------------------------------------------------------ audit parse
_RULE_RE = re.compile(r"^\[\s*(FAIL|WARN|OK)\s*\]\s+(R\d+[^\s]*)\s+(.*)$")


def parse_audit(stdout: str) -> dict[str, Any]:
    """把引擎审计的人类文本解析成结构（失败时 ``parsed=False``，绝不伪造通过）。"""
    rules: list[dict[str, str]] = []
    for line in (stdout or "").splitlines():
        m = _RULE_RE.match(line.strip())
        if not m:
            continue
        rules.append({"level": m.group(1), "id": m.group(2), "message": m.group(3).strip()})
    counts = {"FAIL": 0, "WARN": 0, "OK": 0}
    for r in rules:
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    return {
        "parsed": bool(rules),
        "rules": rules,
        "counts": counts,
        "summary": _summary_line(stdout),
    }


def _summary_line(stdout: str) -> str:
    for line in reversed((stdout or "").splitlines()):
        if "shot(s)" in line or "FAIL" in line and "shot" in line:
            return line.strip()
    return ""


# ------------------------------------------------------------------ doctor
#: 内省脚本：**反射引擎代码**，不抄文档。任何一项拿不到就写进 ``errors``，
#: 由调用方如实展示（宁可空，不可假）。
DOCTOR_SCRIPT = textwrap.dedent(r'''
import ast, json, os

out = {"layouts": {}, "kinds": [], "cam_presets": [], "palettes": {},
       "procedural": [], "bg_modes": [], "layer_types": [],
       "post_default": {}, "grade_default": {}, "cue_defaults": {},
       "audit_rules": [], "cli": [], "errors": []}

def _try(fn, key):
    try:
        out[key] = fn()
    except Exception as exc:
        out["errors"].append("%s: %s" % (key, exc))

def _kinds(path, var):
    """收集 ``<var> == "x"`` / ``<var> in ("a","b")`` 里的字符串常量。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        name = left.id if isinstance(left, ast.Name) else (
            getattr(getattr(left, "func", None), "attr", None))
        if name != var:
            continue
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                found.append(comp.value)
            elif isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                for e in comp.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        found.append(e.value)
    return sorted(set(found))

def _audit_rules():
    """审计规则：规则 id 取第二个实参，级别取第一个实参里出现过的字符串常量。

    ``audit.py`` 的写法是 ``add("FAIL" if gaps else "OK", "R01 时间轴连续", ...)``，
    第一个实参是条件表达式——只看 Constant 会把 11 条里漏掉 8 条。
    R10 有两处 ``add``（WARN/OK 两个分支），按 id 合并级别。
    """
    tree = ast.parse(open("engine/audit.py", encoding="utf-8").read())
    merged = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "add" or len(node.args) < 2:
            continue
        rid = node.args[1].value if isinstance(node.args[1], ast.Constant) else ""
        if not isinstance(rid, str) or not rid.startswith("R"):
            continue
        levels = {n.value for n in ast.walk(node.args[0])
                  if isinstance(n, ast.Constant) and n.value in ("FAIL", "WARN", "OK")}
        key = rid.split()[0]
        entry = merged.setdefault(key, {"id": key, "rule": rid, "levels": []})
        entry["levels"] = sorted(set(entry["levels"]) | levels)
    return [merged[k] for k in sorted(merged)]

def _cli():
    """CLI 子命令名（``add_parser("x", ...)`` 的第一个实参；参数表由引擎自己维护）。"""
    tree = ast.parse(open("engine/cli.py", encoding="utf-8").read())
    cmds = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") != "add_parser":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            cmds.append({"name": str(node.args[0].value)})
    return sorted(cmds, key=lambda c: c["name"])


def _param_default(path, func_name, param):
    """取函数签名里某参数的默认字面量（``plate_variant(..., mode="plate")``）。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        args = node.args.args[-len(node.args.defaults):] if node.args.defaults else []
        for arg, default in zip(args, node.args.defaults):
            if arg.arg == param and isinstance(default, ast.Constant):
                return default.value
    return None

def _cue_defaults():
    """``_cue()`` 里的 setdefault（只看这个函数，否则会混入图层字段）。"""
    tree = ast.parse(open("engine/project.py", encoding="utf-8").read())
    out_ = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name != "_cue":
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "setdefault":
                if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant):
                    try:
                        out_[node.args[0].value] = ast.literal_eval(node.args[1])
                    except Exception:
                        out_[node.args[0].value] = "?"
    return out_

_try(lambda: {k: dict(v) for k, v in __import__("engine.block", fromlist=["x"]).LAYOUTS.items()}, "layouts")
_try(lambda: sorted(__import__("engine.project", fromlist=["x"]).CAM_PRESETS), "cam_presets")
_try(lambda: {k: {"lo": list(v["lo"]), "hi": list(v["hi"])}
              for k, v in __import__("engine.scene", fromlist=["x"]).PALETTES.items()}, "palettes")
_try(lambda: dict(__import__("engine.project", fromlist=["x"]).POST_DEFAULT), "post_default")
_try(lambda: {k: (list(v) if isinstance(v, tuple) else v)
              for k, v in __import__("engine.project", fromlist=["x"]).GRADE_DEFAULT.items()},
     "grade_default")
_try(lambda: _kinds("engine/block.py", "kind"), "kinds")
_try(lambda: _kinds("engine/assets.py", "kind"), "procedural")
_try(lambda: sorted(set(_kinds("engine/scene.py", "mode"))
                    | {str(_param_default("engine/scene.py", "plate_variant", "mode") or "")} - {""}),
     "bg_modes")
_try(lambda: sorted(set(_kinds("engine/puppet.py", "k")) | {"type"}), "layer_types")
_try(_cue_defaults, "cue_defaults")
_try(_audit_rules, "audit_rules")
_try(_cli, "cli")
print(json.dumps(out, ensure_ascii=False))
''')


def doctor_args() -> list[str]:
    """``python -c <内省脚本>``（cwd=引擎根目录，需能 import engine 包）。"""
    return ["-c", DOCTOR_SCRIPT]


def run_doctor(paths: EnginePaths, *, timeout: float = DEFAULT_DOCTOR_TIMEOUT) -> dict[str, Any]:
    """跑内省脚本并解析 JSON；失败时返回 ``available=False`` + 原因。"""
    if paths.layout != LAYOUT_PACKAGE:
        return {
            "available": False,
            "error": ("当前引擎是 build 布局（上游 PV 仓库），不提供模块内省；"
                      "doctor 需要 engine/ 包布局（``engine/cli.py``）"),
            "layout": paths.layout or "unknown",
        }
    result = run_engine(paths, doctor_args(), timeout=timeout)
    if not result.get("ok"):
        return {"available": False, "error": result.get("error") or result.get("stderr") or "",
                "layout": paths.layout, "raw": result}
    raw = (result.get("stdout") or "").strip()
    try:
        data = json.loads(raw.splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"内省输出不是 JSON：{exc}",
                "layout": paths.layout, "raw": raw[:2000]}
    data["available"] = True
    data["layout"] = paths.layout
    return data


__all__ = [
    "AUDIT_REL",
    "BUILD_REL",
    "CLI_REL",
    "DEFAULT_AUDIT_TIMEOUT",
    "DEFAULT_DOCTOR_TIMEOUT",
    "DEFAULT_RENDER_TIMEOUT",
    "DEFAULT_STILLS_TIMEOUT",
    "DOCTOR_SCRIPT",
    "ENGINE_ENV",
    "EnginePaths",
    "LAYOUT_BUILD",
    "LAYOUT_PACKAGE",
    "audit_args",
    "beats_args",
    "doctor_args",
    "init_args",
    "parse_audit",
    "probe_args",
    "render_args",
    "resolve_engine",
    "run_doctor",
    "run_engine",
    "stills_args",
]
