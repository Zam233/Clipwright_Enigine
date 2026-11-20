"""X02 维护窗口探针（离线可跑）：版本指纹 + 关键路由 + 契约 fixture。

不依赖真实 provider / MongoDB 内容；用 TestClient 直接打应用，作为
维护窗口里“后端起来了、契约是 v2、关键面没掉”的最小证据。

用法：
    python scripts/x02_probe.py
退出码：0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def run_probes(client: Any) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 — 探针失败要报出来，不是崩掉
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))

    def _health():
        r = client.get("/health")
        body = r.json() if r.status_code == 200 else {}
        ok = r.status_code == 200 and str(body.get("contract_version")) == "2"
        return ok, f"status={r.status_code} contract_version={body.get('contract_version')}"

    def _routes():
        spec = client.get("/openapi.json").json()
        paths = set(spec.get("paths") or {})
        need = {
            "/api/task/{task_id}/runs",
            "/api/workspace/{project_id}/reports",
            "/api/workspace/{project_id}/reports/{seq}",
            "/api/eval/reports",
        }
        missing = sorted(need - paths)
        return not missing, ("ok" if not missing else f"缺少 {missing}")

    def _task_runs():
        r = client.get("/api/task/__probe_missing__/runs")
        return r.status_code in (200, 404), f"status={r.status_code}"

    def _reports():
        r = client.get("/api/workspace/__probe_missing__/reports")
        return r.status_code in (200, 404), f"status={r.status_code}"

    def _eval():
        r = client.get("/api/eval/reports")
        return r.status_code == 200, f"status={r.status_code}"

    check("health.contract_version=2", _health)
    check("openapi.key_routes", _routes)
    check("task.runs.addressing", _task_runs)
    check("workspace.reports.v2", _reports)
    check("eval.reports", _eval)
    return results


def main() -> int:
    from fastapi.testclient import TestClient

    from clipwright.main import app

    results = run_probes(TestClient(app))
    failed = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(results) - failed}/{len(results)} probes passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
