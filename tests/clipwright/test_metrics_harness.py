"""R10：验收编排的回归守卫。

这里不重复测业务，只守三件**容易在后续改动里悄悄坏掉**的事：

1. `verify_all_metrics.py` 的指标表覆盖了 R1–R9 且脚本文件真实存在
   （改文件名/挪目录后汇总表会静默少一轮——那是最坏的情况：看上去"全绿"，其实少跑了）；
2. 每个指标都带**非空**的「验证边界」——这是本项目的硬要求：
   数字必须和"这个数字不代表什么"一起出现，否则汇总表会被当成效果证明；
3. 每个验收脚本都能被解析出 `VERIFY PASS/FAIL` 结论（脚本自己至少要能 import 且
   有明确的成功标记，否则编排会永远判 FAIL 却没人发现）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def load_registry():
    """从 `verify_all_metrics.py` 里静态读出指标表（不执行脚本，避免副作用）。"""
    src = (SCRIPTS / "verify_all_metrics.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    entries: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "Metric"):
            continue
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if len(args) < 4:
            continue
        entries.append({
            "round": str(args[0]), "script": str(args[1]),
            "metric": str(args[2]), "boundary": str(args[3]),
        })
    return entries


class TestMetricsRegistry:
    def test_registry_is_not_empty(self):
        assert load_registry()

    def test_covers_every_round(self):
        """R1–R9 一轮都不能少（少一轮 = 静默漏测）。"""
        rounds = {e["round"] for e in load_registry()}
        assert rounds == {f"R{i}" for i in range(1, 10)}, sorted(rounds)

    def test_every_script_exists(self):
        for entry in load_registry():
            path = SCRIPTS / entry["script"]
            assert path.exists(), f"{entry['round']} 的脚本不存在：{path}"

    def test_every_boundary_is_non_empty(self):
        """「验证边界」不能空——它是防止数字被误读的唯一手段。"""
        for entry in load_registry():
            assert len(entry["boundary"]) >= 8, f"{entry['round']} 的验证边界太短：{entry['boundary']!r}"

    def test_every_metric_is_descriptive(self):
        for entry in load_registry():
            assert len(entry["metric"]) >= 4, entry

    def test_no_duplicate_scripts(self):
        scripts = [e["script"] for e in load_registry()]
        assert len(scripts) == len(set(scripts))


class TestScriptsAreDiscoverable:
    @pytest.mark.parametrize("name", [
        "verify_all_metrics.py", "demo_content_understanding.py",
    ])
    def test_r10_scripts_exist(self, name):
        assert (SCRIPTS / name).exists()

    def test_verify_scripts_announce_pass_or_fail(self):
        """编排靠匹配 `VERIFY PASS` 判成功——脚本必须打印这个标记。"""
        missing = []
        for entry in load_registry():
            src = (SCRIPTS / entry["script"]).read_text(encoding="utf-8")
            if "VERIFY PASS" not in src:
                missing.append(entry["script"])
        assert not missing, f"这些脚本没有打印 VERIFY PASS 标记：{missing}"

    def test_verify_scripts_have_fail_marker(self):
        missing = []
        for entry in load_registry():
            src = (SCRIPTS / entry["script"]).read_text(encoding="utf-8")
            if "VERIFY FAIL" not in src:
                missing.append(entry["script"])
        assert not missing, f"这些脚本没有 FAIL 标记（失败会被当成通过）：{missing}"

    def test_demo_accepts_real_material_flag(self):
        """demo 必须支持真实素材——这是本轮"真实素材 demo"交付物的实质。"""
        src = (SCRIPTS / "demo_content_understanding.py").read_text(encoding="utf-8")
        assert "--material" in src
        assert "合成素材" in src        # 没有真实素材时如实说明用的是合成的


class TestBenchmarkDoc:
    DOC = ROOT / "docs" / "content-understanding-benchmark.md"

    def test_doc_exists(self):
        assert self.DOC.exists()

    def test_doc_lists_all_rounds(self):
        text = self.DOC.read_text(encoding="utf-8")
        for i in range(1, 11):
            assert re.search(rf"R{i}\b", text), f"基准集文档缺少 R{i}"

    def test_doc_states_the_honesty_boundaries(self):
        """文档必须写明"合成素材/无真实素材"这件事，不能只给数字。"""
        text = self.DOC.read_text(encoding="utf-8")
        assert "合成" in text
        assert "真实素材" in text
        assert "验证边界" in text

    def test_doc_points_at_the_runner(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "verify_all_metrics.py" in text

    def test_metrics_json_is_regenerable_and_committed(self):
        """`--json` 产物应存在（它是"文档数字与脚本同源"的证据）。"""
        path = ROOT / "docs" / "content-metrics-latest.json"
        assert path.exists()
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, list) and len(payload) >= 9
        assert all(item.get("boundary") for item in payload)
