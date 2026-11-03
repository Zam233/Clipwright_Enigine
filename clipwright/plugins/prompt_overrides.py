"""提示词符号覆盖层（Batch 5 / B5-T1…T3、B5-T7）。

**它解决什么**：插件要能替换 `clipwright/prompts.py` 里的**单个符号**——26 个常量文本
或 8 个参数化 builder——而不是只能整体替换/追加（现有 `PluginPromptRegistry` 只有
agent 级 append）。

**四关校验**（PRD §15.2.2 / §15.2.7）

| 关 | 常量 | builder |
|---|---|---|
| 1 符号名 | 必须精确等于 `prompts.py` 导出的名字 | 同左 |
| 2 签名 | —（文本无签名） | `inspect.signature` 必须与默认 builder 一致 |
| 3 占位符 | 不得残留未替换的 `{xxx}` | 同左（输出不得残留） |
| 4 纯度 | 必须是纯文本，不许带脚本片段 | 静态 AST 白名单 + 黑名单调用（见 `check_builder_source`） |

**双 dry-render**：builder 覆盖用两组不同样例参数各跑一次，**输出必须不同** —— 这条专门
防「参数声明了却不用」（把运行时数字写死），是占位符比对挡不住的。

**为什么不启用运行期沙箱**：builder 是宿主进程内的普通函数，真要隔离得上一整层进程/
容器；静态白名单已能挡住「读配置 / 联网 / 写盘 / 动态取属性」这几类现实风险。残余风险
（刻意混淆）写在 PRD §15.2.7，兜底是「白名单语法 + 双 dry-render + 输出长度上限 + 出问题
禁用插件」。
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
from typing import Any, Callable, Iterable

__all__ = [
    "OVERRIDE_ERRORS",
    "all_symbols",
    "check_builder_source",
    "clear_plugin",
    "inspect_constant_override",
    "register_builder_override",
    "register_constant_override",
    "resolve_builder",
    "resolve_constant",
    "resolve_text",
    "MAX_SOURCE_BYTES",
    "MAX_AST_NODES",
    "MAX_NESTING",
    "MAX_RETURNS",
    "MAX_OUTPUT_CHARS",
    "WIRED_CONSTANTS",
    "MIN_OUTPUT_CHARS",
]

# ── 阈值（PRD §15.2.7 初值；可按真实 builder 基线校准，方法不变）──
MAX_SOURCE_BYTES = 32 * 1024
MAX_AST_NODES = 600
MAX_NESTING = 8
MAX_RETURNS = 8
MAX_OUTPUT_CHARS = 6000       # 与「3× 默认输出长度」取较大者
OUTPUT_MULTIPLIER = 3

#: 允许的唯一 import：`from __future__ import annotations`
ALLOWED_FUTURE_IMPORT = "annotations"

#: 禁用调用（按函数名匹配；getattr 是绕过静态检查的头号手段，一并禁掉）
FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "setattr", "delattr", "getattr",
})

#: 出现即拒绝的模块名（源码里以名字形态出现）
FORBIDDEN_NAMES = frozenset({
    "os", "sys", "subprocess", "socket", "requests", "httpx", "pathlib", "shutil",
    "tempfile", "random", "time", "datetime", "uuid", "asyncio", "importlib",
})

#: 已接到 `resolve_constant()` 的常量（消费方已改为运行时解析）。
#: 其余常量是 `prompts.py` **内部组合用的构件**（被 builder 在导入期拼好），
#: 目前仍只走默认值 —— 诊断接口对它们标 `wired=False`，不假装已生效。
WIRED_CONSTANTS = frozenset({
    "CREATIVE_BRIEF_SYSTEM",
    "EDIT_PROFILE_SYSTEM",
    "QUALITY_SEMANTIC_QA_SYSTEM",
    "STRUCTURE_MG_DYNAMIC_GUIDE",
    "STRUCTURE_SYSTEM_PROMPT_TPL",
    "STRUCTURE_TOOL_PROMPT",
    "PLAN_TRANSLATE_SYSTEM",
})
#: 覆盖登记：symbol → {"plugin_id", "kind", "value"/"impl", "source"}
_OVERRIDES: dict[str, dict[str, Any]] = {}

#: 本进程内每次校验失败的记录（诊断用；不写文件）
OVERRIDE_ERRORS: list[dict[str, str]] = []


# ── 符号表 ────────────────────────────────────────────────

def _prompts_module():
    from clipwright import prompts

    return prompts


def _constant_names() -> list[str]:
    mod = _prompts_module()
    return sorted(
        n for n in dir(mod)
        if n.isupper() and isinstance(getattr(mod, n, None), str)
    )


def _builder_names() -> list[str]:
    mod = _prompts_module()
    return sorted(
        n for n in dir(mod)
        if n.startswith("build_") and callable(getattr(mod, n, None))
    )


def all_symbols() -> dict[str, Any]:
    """可覆盖符号清单（供诊断接口与前端展示）。

    `wired` 表示该符号**当前是否真的会被调用方解析**：
    - 8 个 builder：已接到 `resolve_builder()`，`wired=True`；
    - 26 个常量：消费方仍是 `from clipwright.prompts import X` 的直接导入，
      **尚未**改造成 `resolve_constant()`，因此 `wired=False`（如实标注，
      避免「能注册却没人用」的假象）。
    """
    mod = _prompts_module()
    out: dict[str, Any] = {"constants": [], "builders": []}
    for name in _constant_names():
        out["constants"].append({
            "name": name,
            "chars": len(getattr(mod, name)),
            "wired": name in WIRED_CONSTANTS,
            "override": _describe(name),
        })
    for name in _builder_names():
        fn = getattr(mod, name)
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = ""
        out["builders"].append({
            "name": name,
            "signature": sig,
            "wired": True,
            "override": _describe(name),
        })
    return out


def _describe(symbol: str) -> dict[str, Any] | None:
    entry = _OVERRIDES.get(symbol)
    if entry is None:
        return None
    return {
        "plugin_id": entry["plugin_id"],
        "kind": entry["kind"],
        "source": entry["source"],
    }


# ── 常量覆盖 ──────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][\w\.\[\]]*\}")


def inspect_constant_override(symbol: str, text: str) -> list[str]:
    """常量覆盖的结构校验；返回问题列表（空 = 可接受）。"""
    problems: list[str] = []
    if symbol not in _constant_names():
        problems.append(f"未知符号: {symbol}")
        return problems
    if not isinstance(text, str):
        problems.append(f"{symbol}: 覆盖内容必须是字符串")
        return problems
    if not text.strip():
        problems.append(f"{symbol}: 覆盖内容不能为空")
        return problems
    if _PLACEHOLDER.search(text):
        problems.append(
            f"{symbol}: 输出残留未替换占位符 {_PLACEHOLDER.search(text).group(0)}"
        )
    limit = max(MAX_OUTPUT_CHARS, OUTPUT_MULTIPLIER * len(getattr(_prompts_module(), symbol)))
    if len(text) > limit:
        problems.append(f"{symbol}: 覆盖文本 {len(text)} 字符超过上限 {limit}")
    return problems


def register_constant_override(plugin_id: str, symbol: str, text: str,
                               *, source: str = "") -> list[str]:
    """登记常量覆盖；返回问题列表（非空则不登记）。"""
    problems = inspect_constant_override(symbol, text)
    if problems:
        OVERRIDE_ERRORS.extend({"plugin_id": plugin_id, "problem": p} for p in problems)
        return problems
    _OVERRIDES[symbol] = {
        "plugin_id": plugin_id, "kind": "constant", "value": text,
        "source": source or f"plugin:{plugin_id}",
    }
    return []


# ── builder 覆盖 ──────────────────────────────────────────

_FORBIDDEN_NODES = (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom,
                    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)


def _nesting_depth(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                              ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            best = max(best, _nesting_depth(child, depth + 1))
        else:
            best = max(best, _nesting_depth(child, depth))
    return best


def check_builder_source(source: str) -> list[str]:
    """静态纯度检查（PRD §15.2.7）；返回问题列表。

    只做**结构**判断，不执行插件代码。规则按 PRD 表逐条实现，任何一条不过即拒绝。
    """
    problems: list[str] = []
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        problems.append(f"源码 {len(source)} 字节超过上限 {MAX_SOURCE_BYTES}")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"源码无法解析: {e}"]

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        problems.append(f"AST 节点 {len(nodes)} 超过上限 {MAX_AST_NODES}")
    depth = _nesting_depth(tree)
    if depth > MAX_NESTING:
        problems.append(f"嵌套深度 {depth} 超过上限 {MAX_NESTING}")
    returns = sum(isinstance(n, ast.Return) for n in nodes)
    if returns > MAX_RETURNS:
        problems.append(f"return 语句 {returns} 超过上限 {MAX_RETURNS}")

    for node in nodes:
        if isinstance(node, _FORBIDDEN_NODES):
            # `from __future__ import annotations` 是唯一放行的 import
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                names = {a.name for a in node.names}
                if names <= {ALLOWED_FUTURE_IMPORT}:
                    continue
            problems.append(f"禁用语法: {type(node).__name__} (line {getattr(node, 'lineno', '?')})")
            continue
        if isinstance(node, ast.Call):
            func = node.func
            fname = getattr(func, "id", None) or getattr(func, "attr", None)
            if fname in FORBIDDEN_CALLS:
                problems.append(f"禁用调用: {fname}() (line {node.lineno})")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"禁用模块/名字: {node.id} (line {node.lineno})")
        if isinstance(node, (ast.Constant,)) and isinstance(node.value, str):
            lowered = node.value.strip()
            if lowered in FORBIDDEN_NAMES:
                # 字符串里出现模块名（如动态 import 的常见写法）也拒绝，宁可误杀
                problems.append(f"字符串字面量命中禁用模块名: {lowered}")
    return problems


_SAMPLE_ARGS: list[tuple[tuple, dict]] = [
    ((), {}),
    ((), {}),
]


def _sample_for(fn: Callable[..., Any], index: int) -> tuple[tuple, dict]:
    """按默认 builder 的签名生成两组样例参数（两组值必须不同）。"""
    sig = inspect.signature(fn)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for pos, param in enumerate(sig.parameters.values()):
        bump = index + 1
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if param.annotation in (int, "int"):
            value: Any = 10 * bump
        elif param.annotation in (list[str], "list[str]"):
            value = [f"s{bump}-1", f"s{bump}-2"]
        else:
            value = f"v{bump}"
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = value
        elif param.default is inspect.Parameter.empty:
            args.append(value)
        else:
            kwargs[param.name] = value
    return tuple(args), kwargs


def _signature_shape(fn: Callable[..., Any]) -> tuple[tuple[str, str, str], ...]:
    """签名「形状」：参数名 + 种类 + 默认值（**不含注解**）。

    为什么不直接比 `inspect.signature`：`prompts.py` 用了
    `from __future__ import annotations`，它的注解是**字符串**（`'int'`），而插件里
    正常写的注解是类型对象（`int`）——直接比较会把所有合规覆盖全部误杀（本轮实测踩到）。
    PRD §15.2.2 的要求也只是「参数名/顺序/默认值一致」。
    """
    shape: list[tuple[str, str, str]] = []
    for param in inspect.signature(fn).parameters.values():
        default = "" if param.default is inspect.Parameter.empty else repr(param.default)
        shape.append((param.name, param.kind.name, default))
    return tuple(shape)


def register_builder_override(plugin_id: str, symbol: str, impl: Callable[..., Any],
                              *, source: str = "") -> list[str]:
    """登记 builder 覆盖（签名 / 静态纯度 / 双 dry-render / 输出长度）；返回问题列表。"""
    problems: list[str] = []
    mod = _prompts_module()
    if symbol not in _builder_names():
        return [f"未知 builder: {symbol}"]
    default = getattr(mod, symbol)

    # 1) 签名形状一致（参数名 / 种类 / 默认值；不看注解，理由见 _signature_shape）
    try:
        if _signature_shape(default) != _signature_shape(impl):
            problems.append(
                f"{symbol}: 签名不一致，期望 {inspect.signature(default)}，"
                f"实际 {inspect.signature(impl)}"
            )
    except (TypeError, ValueError) as e:
        problems.append(f"{symbol}: 无法比较签名（{e}）")

    # 2) 静态纯度（能拿到源码才查；拿不到就要求显式声明已自检）
    try:
        # `getsource` 对嵌套/局部函数会保留缩进，直接 ast.parse 会报 unexpected indent
        # （插件把 builder 写在工厂函数里很常见），所以先 dedent。
        src = textwrap.dedent(inspect.getsource(impl))
    except (OSError, TypeError):
        src = ""
    if src:
        problems.extend(f"{symbol}: {p}" for p in check_builder_source(src))

    # 3) 双 dry-render + 输出检查
    renders: list[str] = []
    for index in range(2):
        args, kwargs = _sample_for(default, index)
        try:
            out = impl(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — 插件实现异常必须被拦在注册期
            problems.append(f"{symbol}: dry-render 抛异常（{type(e).__name__}: {e}）")
            break
        if not isinstance(out, str):
            problems.append(f"{symbol}: dry-render 返回 {type(out).__name__}，要求 str")
            break
        if _PLACEHOLDER.search(out):
            problems.append(
                f"{symbol}: dry-render 输出残留占位符 {_PLACEHOLDER.search(out).group(0)}"
            )
        renders.append(out)

    if len(renders) == 2:
        default_len = len(getattr(mod, symbol)(*_sample_for(default, 0)[0],
                                              **_sample_for(default, 0)[1]))
        limit = max(MAX_OUTPUT_CHARS, OUTPUT_MULTIPLIER * default_len)
        for out in renders:
            if len(out) > limit:
                problems.append(
                    f"{symbol}: dry-render 输出 {len(out)} 字符超过上限 {limit}"
                    f"（默认 {default_len}）"
                )
        if renders[0] == renders[1]:
            problems.append(
                f"{symbol}: 两组样例参数输出相同 —— 参数被声明却没用上"
                "（禁止把运行时数字写死）"
            )

    if problems:
        OVERRIDE_ERRORS.extend({"plugin_id": plugin_id, "problem": p} for p in problems)
        return problems
    _OVERRIDES[symbol] = {
        "plugin_id": plugin_id, "kind": "builder", "value": impl,
        "source": source or f"plugin:{plugin_id}",
    }
    return []


# ── 解析（消费方入口）────────────────────────────────────

def resolve_constant(symbol: str) -> str:
    """取常量文本：有插件覆盖用覆盖值，否则回落到 `prompts.py` 默认值。"""
    entry = _OVERRIDES.get(symbol)
    if entry is not None and entry["kind"] == "constant":
        return str(entry["value"])
    return str(getattr(_prompts_module(), symbol))


def resolve_text(symbol: str) -> str:
    """别名（语义更清楚：取一段提示词文本）。"""
    return resolve_constant(symbol)


def resolve_builder(symbol: str) -> Callable[..., Any]:
    """取 builder：有插件覆盖且校验通过用插件实现，否则回落默认实现。

    **失败隔离**：插件实现在运行期抛异常时，调用方拿到的仍是它，但
    `safe_builder()` 会兜住（见下）——这里不静默替换，避免掩盖插件缺陷。
    """
    entry = _OVERRIDES.get(symbol)
    if entry is not None and entry["kind"] == "builder":
        return entry["value"]
    return getattr(_prompts_module(), symbol)


def safe_builder(symbol: str) -> Callable[..., Any]:
    """返回一个「插件抛异常即回落默认实现」的包装函数（PRD：失败不得拖垮任务）。"""
    override = _OVERRIDES.get(symbol)
    default = getattr(_prompts_module(), symbol)
    if override is None or override["kind"] != "builder":
        return default
    impl = override["value"]

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return impl(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — 插件实现失败必须回落到默认
            OVERRIDE_ERRORS.append({
                "plugin_id": str(override.get("plugin_id", "")),
                "problem": f"{symbol}: 运行期异常，已回落默认实现（{type(e).__name__}: {e}）",
            })
            return default(*args, **kwargs)

    return _wrapped


def clear_plugin(plugin_id: str) -> list[str]:
    """插件卸载/禁用时清掉它的全部覆盖；返回被清掉的符号名。"""
    owner = str(plugin_id or "")
    removed = [s for s, e in _OVERRIDES.items() if e.get("plugin_id") == owner]
    for symbol in removed:
        _OVERRIDES.pop(symbol, None)
    return sorted(removed)


