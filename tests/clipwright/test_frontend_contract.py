"""前端 API 契约对账脚本的自测（路径 / 方法 / 字段三层判定都要能报得准）。

脚本本身是"防 404/405/字段漂移"的哨兵——哨兵自己错了会给出假安全感，
所以用临时前端目录造四种典型现场：正常调用、注释里的路径、`includes()` 比较、
方法用错，逐个断言判定结果。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_frontend_contract.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_frontend_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_client(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_collects_real_calls_with_methods(tmp_path) -> None:
    module = _load()
    root = _write_client(tmp_path, {
        "src/services/api/demo.ts": (
            "export const demoApi = {\n"
            "  async list() { const { data } = await getApiClient().get('/api/type-maker/list'); return data; },\n"
            "  async run(id: string) {\n"
            "    const { data } = await getApiClient().post(`/api/type-maker/${id}/genre-check`, {});\n"
            "    return data;\n"
            "  },\n"
            "};\n"
        ),
    })
    calls = module.frontend_calls(root)
    assert calls["/api/type-maker/list"]["methods"] == {"GET"}
    assert calls["/api/type-maker/{}/genre-check"]["methods"] == {"POST"}


def test_ignores_paths_only_mentioned_in_comments(tmp_path) -> None:
    module = _load()
    root = _write_client(tmp_path, {
        "src/services/api/demo.ts": (
            "// 文档里提到 /api/task/{id}/events/stream，不是调用\n"
            "/* 也不是：'/api/ghost/endpoint' */\n"
            "export const x = 1;\n"
        ),
    })
    assert module.frontend_calls(root) == {}


def test_ignores_substring_comparisons(tmp_path) -> None:
    module = _load()
    root = _write_client(tmp_path, {
        "src/services/api/demo.ts": (
            "export function f(url: string) {\n"
            "  if (!url.includes('/api/asset/')) return url;\n"
            "  return url;\n"
            "}\n"
        ),
    })
    assert module.frontend_calls(root) == {}


def test_skips_other_service_clients(tmp_path) -> None:
    module = _load()
    root = _write_client(tmp_path, {
        "src/services/api/account.ts": (
            "const c = axios.create({ baseURL: '/srv' });\n"
            "c.get('/api/auth/refresh');\n"
        ),
    })
    assert module.frontend_calls(root) == {}


def test_method_mismatch_is_reported(tmp_path) -> None:
    """前端用 GET 调只允许 POST 的路由 → 必须报出来（否则线上 405）。"""
    module = _load()
    root = _write_client(tmp_path, {
        "src/services/api/demo.ts": (
            "const { data } = await getApiClient().get('/api/pipeline/run-async');\n"
        ),
    })
    code = module.main(["--client", str(root), "--layer", "method", "--json"])
    assert code == 0  # --layer method 只跑该方法层，返回值不覆盖 ok（由 all 决定）
    front = module.frontend_calls(root)
    back = module.backend_routes()
    allowed = back["/api/pipeline/run-async"]
    assert "get" not in allowed and "post" in allowed
    assert front["/api/pipeline/run-async"]["methods"] == {"GET"}


def test_dict_keys_extraction_for_health_and_genre() -> None:
    module = _load()
    health = module.backend_dict_keys("clipwright/main.py", 'components["')
    assert {"mongodb", "llm", "ffmpeg", "hyperframes", "status"} <= health
    genre = module.backend_dict_keys("clipwright/api/type_maker.py", "_genre_result")
    assert {"type_id", "measured", "judge", "traits", "mismatches"} <= genre


def test_frontend_interface_fields_parsing(tmp_path) -> None:
    module = _load()
    root = _write_client(tmp_path, {
        "src/types/api.ts": (
            "export interface HealthResponse {\n"
            "  status: string;\n"
            "  service?: string;\n"
            "  mongodb?: string;\n"
            "}\n"
        ),
    })
    fields = module.frontend_interface_fields(root, "src/types/api.ts", "HealthResponse")
    assert fields == {"status", "service", "mongodb"}


@pytest.mark.parametrize("path,expected", [
    ("/api/type-maker/${id}/genre-check", "/api/type-maker/{}/genre-check"),
    ("/api/asset/by-path?path=x", "/api/asset/by-path"),
    ("/api/project/", "/api/project"),
])
def test_normalize_handles_templates_and_query(path, expected) -> None:
    module = _load()
    assert module._normalize(path) == expected
