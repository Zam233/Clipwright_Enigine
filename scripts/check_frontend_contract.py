"""前端 API 契约对账（路径 + HTTP 方法 + 关键模型字段）。

**为什么写这个**：前端 bug 里最难靠单测发现的一类是"调了不存在的端点 / 用错方法 /
字段名对不上"——组件测试都打桩，类型检查也发现不了字符串路径或字段写错。

三层检查：
1. **路径**：前端调用的 `/api/...` 是否都在后端路由表里（缺 = 必定 404）；
2. **方法**：前端用的 `get/post/put/delete/patch` 是否被该后端路由允许（错 = 405）；
3. **字段**：核心模型的**后端响应字段**与**前端 TS 接口字段**是否对得上（漂移 = 静默
   undefined）。模型对由 `MODEL_PAIRS` 显式登记——这是人工确认过的配对，不猜。

用法::

    python scripts/check_frontend_contract.py            # 全部三层
    python scripts/check_frontend_contract.py --json     # 机器可读
    python scripts/check_frontend_contract.py --layer path   # 只查一层
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 前端仓库默认位置（与本仓库同级）
CLIENT_DEFAULT = Path("J:/Clipweight-Client")

#: 路径起点：字符串/模板串里的 `/api/`（终点靠 `_extract_path` 扫描，才不会被
#: 模板串内嵌的引号截断，例如 `` `/api/asset/${id}/thumbnail${q ? '?a=1' : ''}` ``）。
_PATH_RE = re.compile(r"""['"`](/api/)""")
#: 前端调用点的方法：`.get<T>(` / `.post(` / `.request({ method: 'POST' ...`
_CALL_RE = re.compile(r"\.(get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(")
_METHOD_OPT_RE = re.compile(r"""method:\s*['"]([A-Za-z]+)['"]""")

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

#: 模型对：人工确认过的配对，只查**字段名**是否双向覆盖。
#: kind=class → 后端 Pydantic 类；kind=dict → 某函数 `return {...}` 的字面 key。
MODEL_PAIRS: list[dict[str, str]] = [
    {
        "model": "HealthResponse",
        "kind": "dict",
        "backend_file": "clipwright/main.py",
        "backend_symbol": 'components["',
        "frontend_file": "src/types/api.ts",
    },
    {
        "model": "GenreCheckResult",
        "kind": "dict",
        "backend_file": "clipwright/api/type_maker.py",
        "backend_symbol": "_genre_result",
        "frontend_file": "src/services/api/typeMaker.ts",
    },
    {
        "model": "Timeline",
        "kind": "class",
        "backend_file": "clipwright/schema/timeline.py",
        "backend_symbol": "Timeline",
        "frontend_file": "src/types/timeline.ts",
    },
]


def _is_other_service(text: str) -> bool:
    """文件是否是"另一个服务"的客户端（baseURL 指向 /srv 等）。"""
    return bool(re.search(r"baseURL:\s*['\"]/(srv|account|market)", text))


def _collapse_templates(path: str) -> str:
    """把 `${...}`（**含嵌套模板串/对象字面量**）统一替换成 `{}`。

    不能用 `\\$\\{[^}]*\\}`：插值里常嵌着内层模板串（`${a ? `?x=${b}` : ''}`），
    第一次遇到 `}` 就停会把尾巴留在路径里，造出假 404。
    """
    out: list[str] = []
    index = 0
    length = len(path)
    while index < length:
        if path[index] == "$" and index + 1 < length and path[index + 1] == "{":
            depth = 0
            scan = index + 1
            while scan < length:
                if path[scan] == "{":
                    depth += 1
                elif path[scan] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                scan += 1
            out.append("{}")
            index = scan + 1
            continue
        out.append(path[index])
        index += 1
    return "".join(out)


def _normalize(path: str) -> str:
    """把 `${id}` / 拼接参数统一成 `{}`，去掉查询串与尾部斜杠。"""
    cleaned = _collapse_templates(path)
    cleaned = re.split(r"[?#]", cleaned, maxsplit=1)[0]
    cleaned = cleaned.rstrip("/")
    return cleaned or "/"


def _method_before(text: str, position: int) -> str:
    """找该路径**紧邻**的调用方法（同一语句内最后一个 `.get(`/`.post(` 之类）。"""
    window = text[max(0, position - 240):position]
    matches = list(_CALL_RE.finditer(window))
    if matches:
        return matches[-1].group(1).upper()
    opt = list(_METHOD_OPT_RE.finditer(window))
    if opt:
        value = opt[-1].group(1).upper()
        return value if value.lower() in HTTP_METHODS else "?"
    return "?"


def _extract_path(text: str, start: int) -> str:
    """从 `/api/` 起点往后扫出路径。

    规则：花括号层级 >0 时（`${...}` 插值内部，可含嵌套模板串与引号）一律忽略
    引号，只在层级 0 遇到引号/反引号才结束。这样
    `` `/api/asset/${id}/thumbnail${q ? `?x=${id}` : ''}` `` 才能被完整取到，
    而不是在插值里的第一个引号处截断（早期版本正是在这里截断，造出 3 个假 404）。
    """
    depth = 0
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
        elif depth == 0 and char in "'\"`":
            break
        index += 1
    return text[start:index]


def frontend_calls(client_root: Path) -> dict[str, dict]:
    """返回 {路径模板: {files, methods}}（只含指向本后端的**真实调用**）。

    两类字符串会被排除，否则必出假阳性：
    - **注释**（`// /api/task/{id}/events/stream` 这类文档说明不是调用）；
    - **子串比较**（`url.includes('/api/asset/')` 是判断，不是请求）。
    """
    found: dict[str, dict] = {}
    api_dir = client_root / "src"
    if not api_dir.is_dir():
        return found
    for path in api_dir.rglob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue
        text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        if _is_other_service(text):
            continue
        for match in _PATH_RE.finditer(text):
            start = match.start(1)
            normalized = _normalize(_extract_path(text, start))
            if not normalized.startswith("/api/"):
                continue
            if _is_comparison(text, start):
                continue
            entry = found.setdefault(normalized, {"files": set(), "methods": set()})
            entry["files"].add(str(path.relative_to(client_root)))
            method = _method_before(text, start)
            if method != "?":
                entry["methods"].add(method)
    return found


def _match_candidates(normalized: str) -> list[str]:
    """把前端路径变体都试一遍（前端常把查询串拼进模板串里）。

    例：`/api/plugin/contributions{}`（实际是 `?kind=...`）与后端
    `/api/plugin/contributions` 是同一条路由；`/api/asset/{}/thumbnail{}`（尾部
    拼了 `?project_id=`）同理。只要某个变体命中就不报"缺失"。
    """
    variants = [normalized]
    stripped = normalized
    while stripped.endswith("{}"):
        stripped = stripped[: -len("{}")]
        variants.append(stripped)
    variants.append(normalized.replace("{}", ""))
    variants.append(re.sub(r"/+", "/", normalized.replace("{}", "")))
    seen: list[str] = []
    for item in variants:
        item = item.rstrip("/") or "/"
        if item not in seen:
            seen.append(item)
    return seen


def _is_comparison(text: str, position: int) -> bool:
    """该字符串是否只是被 `includes/startsWith/endsWith/indexOf` 用来比较。"""
    window = text[max(0, position - 48):position]
    return bool(
        re.search(r"(includes|startsWith|endsWith|indexOf|has)\s*\(\s*['\"`]?\s*$", window)
    )


def backend_routes() -> dict[str, set[str]]:
    """{路径模板: {允许的方法}}（来自 FastAPI 路由表）。"""
    from clipwright.main import app

    out: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        normalized = re.sub(r"\{[^}]*\}", "{}", path).rstrip("/") or "/"
        methods = {m.lower() for m in (getattr(route, "methods", set()) or set())}
        out.setdefault(normalized, set()).update(methods)
    return out


# ── 字段层 ──────────────────────────────────────────────────────────────


def _strip_comments(text: str) -> str:
    """去掉行注释与块注释：注释里的路径（如文档字符串里的 `/api/x/{id}`）不是调用。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)//.*$", "", text)
    return text


def backend_model_fields(model_path: str, name: str) -> set[str]:
    """从后端源码里取 Pydantic 模型的顶层字段名（不导入，避免副作用）。"""
    path = ROOT / model_path
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"class\s+{name}\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\n@|\Z)", text, re.S)
    if not match:
        return set()
    body = match.group(1)
    fields: set[str] = set()
    for line in body.splitlines():
        field = re.match(r"\s{4}([a-zA-Z_][a-zA-Z0-9_]*)\s*:", line)
        if field and not field.group(1).startswith("_"):
            fields.add(field.group(1))
    return fields


def backend_dict_keys(model_path: str, symbol: str) -> set[str]:
    """取某函数 `return { "a": ..., "b": ... }` 的字面 key（用于 dict 型响应）。

    ``symbol`` 以 ``components["`` 开头时按"给 components 字典赋 key"的方式提取
    （健康检查就是这么拼的）。
    """
    path = ROOT / model_path
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    if symbol.startswith('components["'):
        keys = set(re.findall(r'components\["([a-z_]+)"\]', text))
        # 初始字面量 `components = {"service": ...}` 也是响应字段
        init = re.search(r"components\s*=\s*\{(.*?)\}", text, re.S)
        if init:
            keys |= set(re.findall(r'"([a-z_]+)":', init.group(1)))
        return keys
    match = re.search(rf"def\s+{symbol}\b.*?return \{{(.*?)\n    \}}", text, re.S)
    if not match:
        return set()
    return set(re.findall(r'"([a-z_]+)":', match.group(1)))


def frontend_interface_fields(client_root: Path, rel_path: str, name: str) -> set[str]:
    """从前端 TS 接口里取顶层字段名。"""
    path = client_root / rel_path
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"(?:interface|type)\s+{name}\b[^{{]*{{(.*?)\n\}}", text, re.S
    )
    if not match:
        return set()
    body = match.group(1)
    fields: set[str] = set()
    for line in body.splitlines():
        field = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\??\s*:", line)
        if field:
            fields.add(field.group(1))
    return fields


def check_models(client_root: Path) -> list[dict]:
    """核心模型字段对账：报**前端读不到**（漂移）与**前端多声明**（多半是拼错）。"""
    results: list[dict] = []
    for pair in MODEL_PAIRS:
        model = pair["model"]
        if pair["kind"] == "class":
            back = backend_model_fields(pair["backend_file"], pair["backend_symbol"])
        else:
            back = backend_dict_keys(pair["backend_file"], pair["backend_symbol"])
        front = frontend_interface_fields(client_root, pair["frontend_file"], model)
        if not back or not front:
            results.append({
                "model": model,
                "status": "unparsed",
                "missing_in_frontend": [],
                "frontend_only": [],
            })
            continue
        missing = sorted(back - front)
        extra = sorted(front - back)
        results.append({
            "model": model,
            "status": "ok" if not missing and not extra else ("drift" if missing else "extra"),
            "missing_in_frontend": missing,
            "frontend_only": extra,
        })
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="前端 API 契约对账（路径/方法/字段）")
    parser.add_argument("--client", default=str(CLIENT_DEFAULT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--layer", choices=["all", "path", "method", "fields"], default="all")
    args = parser.parse_args(argv)

    client_root = Path(args.client)
    if not client_root.is_dir():
        print(f"前端仓库不存在：{client_root}")
        return 2

    front = frontend_calls(client_root)
    back = backend_routes()

    missing = sorted(
        p for p in front if not any(c in back for c in _match_candidates(p))
    )
    unused = sorted(p for p in back if p not in front)

    method_bad: list[dict] = []
    for path, entry in sorted(front.items()):
        allowed: set[str] = set()
        for candidate in _match_candidates(path):
            allowed |= back.get(candidate, set())
        if not allowed or not entry["methods"]:
            continue
        bad = sorted(m for m in entry["methods"] if m.lower() not in allowed)
        if bad:
            method_bad.append({
                "path": path,
                "frontend_methods": sorted(entry["methods"]),
                "backend_methods": sorted(m.upper() for m in allowed if m != "head"),
                "wrong": bad,
                "files": sorted(entry["files"]),
            })

    fields = check_models(client_root) if args.layer in ("all", "fields") else []
    drift = [item for item in fields if item["status"] in ("drift", "extra", "unparsed")]

    report = {
        "ok": (not missing and not method_bad and not drift) if args.layer == "all" else True,
        "frontend_paths": len(front),
        "backend_paths": len(back),
        "missing_in_backend": [{"path": p, "files": sorted(front[p]["files"])} for p in missing],
        "method_mismatch": method_bad,
        "backend_only_count": len(unused),
        "backend_only_sample": unused[:15],
        "model_fields": fields,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    print(f"前端路径 {report['frontend_paths']} 条 / 后端路由 {report['backend_paths']} 条")

    if args.layer in ("all", "path"):
        if missing:
            print("\n【前端调了但后端没有 —— 必定 404】")
            for item in report["missing_in_backend"]:
                print(f"  {item['path']}   ← {', '.join(item['files'])}")
        else:
            print("\n路径：前端调用的每个 /api 路径后端都有 ✅")
        print(f"后端有但前端未调：{report['backend_only_count']} 条（信息，不算 bug）")

    if args.layer in ("all", "method"):
        if method_bad:
            print("\n【方法不匹配 —— 必定 405】")
            for item in method_bad:
                print(
                    f"  {item['path']}  前端 {item['frontend_methods']} / "
                    f"后端 {item['backend_methods']}   ← {', '.join(item['files'])}"
                )
        else:
            print("方法：前端用到的 HTTP 方法后端都允许 ✅")

    if args.layer in ("all", "fields"):
        print("\n核心模型字段：")
        for item in fields:
            if item["status"] == "ok":
                print(f"  {item['model']}: 完全一致 ✅")
            elif item["status"] == "unparsed":
                print(f"  {item['model']}: 解析失败（检查 MODEL_PAIRS 路径）⚠️")
            else:
                print(
                    f"  {item['model']}: 前端缺 {item['missing_in_frontend']} / "
                    f"前端多 {item['frontend_only']}"
                )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
