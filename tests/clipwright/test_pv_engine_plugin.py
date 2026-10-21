"""PV 引擎插件单测：路径解析、命令构造、退出码映射、缺引擎时的诚实失败。

**测试策略**：用**假引擎目录**（stub 脚本）覆盖适配层与工具逻辑，不依赖真引擎的
素材/依赖；另有一个「真引擎存在时才跑」的集成用例（设 CLIPWRIGHT_PV_ENGINE_DIR）。

为什么不拿真引擎跑全量：引擎仓库不含素材（MET 公版版画与 AI 人物层都在 `.gitignore`），
`stills`/`frames` 会因缺图失败。这一点在插件文档里写明，测试也不假装它能跑。
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from plugins.pv_engine import adapter
from plugins.pv_engine.main import (
    PvEngineAuditTool,
    PvEnginePlugin,
    PvEngineProbeTool,
    PvEngineReviewSkill,
    PvEngineStillsTool,
)


def _fake_engine(root: Path, *, audit_exit: int = 0, stills: bool = True) -> Path:
    """造一个"引擎目录"：有 _build/audit.py 与 _build/pv/build.py 两个 stub。"""
    build = root / "_build"
    (build / "pv").mkdir(parents=True)
    (build / "audit.py").write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            print(json.dumps({{"rules": 36, "fail": 0 if {audit_exit} == 0 else 1}}))
            sys.exit({audit_exit})
            """
        ),
        encoding="utf-8",
    )
    (build / "pv" / "__init__.py").write_text("", encoding="utf-8")
    (build / "pv" / "build.py").write_text(
        textwrap.dedent(
            f"""\
            import argparse, os, sys
            from PIL import Image
            ap = argparse.ArgumentParser()
            ap.add_argument("mode", choices=["stills", "frames", "probe"])
            ap.add_argument("--shots", default="")
            ap.add_argument("--times", default="")
            ap.add_argument("--stills-dir", default="stills")
            ap.add_argument("--out", default="")
            ap.add_argument("--start", type=int, default=0)
            ap.add_argument("--end", type=int, default=0)
            ap.add_argument("--workers", type=int, default=1)
            ap.add_argument("--crf", default="17")
            ap.add_argument("--preset", default="medium")
            a = ap.parse_args()
            if a.mode == "probe":
                print("3 shots 12.0 s covered")
                print("gaps: []")
            elif a.mode == "stills":
                os.makedirs(a.stills_dir, exist_ok=True)
                if not {stills}:
                    print("missing assets", file=sys.stderr)
                    sys.exit(2)
                for t in [x for x in a.times.split(",") if x]:
                    Image.new("RGB", (8, 8), (10, 10, 10)).save(
                        os.path.join(a.stills_dir, "still_%07.2f.png" % float(t)))
                    print("rendered", t)
            """
        ),
        encoding="utf-8",
    )
    return root


class TestResolve:
    def test_explicit_wins(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(adapter.ENGINE_ENV, str(tmp_path / "env"))
        paths = adapter.resolve_engine(explicit=str(tmp_path / "explicit"))
        assert paths is not None and paths.root.name == "explicit"

    def test_config_then_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(adapter.ENGINE_ENV, str(tmp_path / "env"))
        paths = adapter.resolve_engine(config_dir=str(tmp_path / "cfg"))
        assert paths is not None and paths.root.name == "cfg"

    def test_env_fallback(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(adapter.ENGINE_ENV, str(tmp_path / "env"))
        paths = adapter.resolve_engine()
        assert paths is not None and paths.root.name == "env"

    def test_none_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv(adapter.ENGINE_ENV, raising=False)
        assert adapter.resolve_engine() is None


class TestValidate:
    def test_missing_root(self, tmp_path) -> None:
        assert "不存在" in adapter.EnginePaths(tmp_path / "nope").validate()

    def test_missing_audit(self, tmp_path) -> None:
        (tmp_path / "_build").mkdir()
        assert "审计脚本" in adapter.EnginePaths(tmp_path).validate()

    def test_missing_build(self, tmp_path) -> None:
        (tmp_path / "_build").mkdir()
        (tmp_path / "_build" / "audit.py").write_text("", encoding="utf-8")
        assert "渲染入口" in adapter.EnginePaths(tmp_path).validate()

    def test_valid(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        assert adapter.EnginePaths(root).validate() == ""


class TestCommandArgs:
    def test_probe(self) -> None:
        assert adapter.probe_args("shots.json") == ["-m", "pv.build", "probe", "--shots", "shots.json"]

    def test_audit_optional_flags(self) -> None:
        assert adapter.audit_args("s.json") == ["audit.py", "--shots", "s.json"]
        assert "--video" in adapter.audit_args("s.json", video="f.mp4")
        assert "--scan" in adapter.audit_args("s.json", scan=True)

    def test_stills_joins_times(self) -> None:
        args = adapter.stills_args("s.json", [20, 90.5], "out")
        assert "--times" in args and args[args.index("--times") + 1] == "20,90.5"
        assert args[args.index("--stills-dir") + 1] == "out"


class TestRunEngine:
    def test_probe_runs_in_build_dir(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        result = adapter.run_engine(adapter.EnginePaths(root), adapter.probe_args("s.json"), timeout=60)
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert "shots" in result["stdout"]
        assert result["cwd"].endswith("_build")

    def test_nonzero_exit_is_reported_not_raised(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine", audit_exit=3)
        result = adapter.run_engine(adapter.EnginePaths(root), adapter.audit_args("s.json"), timeout=60)
        assert result["ok"] is False
        assert result["exit_code"] == 3

    def test_missing_engine_returns_error(self, tmp_path) -> None:
        result = adapter.run_engine(adapter.EnginePaths(tmp_path / "nope"), ["-m", "pv.build"], timeout=10)
        assert result["ok"] is False and "不存在" in result["error"]

    def test_timeout_is_reported(self, tmp_path) -> None:
        root = tmp_path / "engine"
        build = root / "_build" / "pv"
        build.mkdir(parents=True)
        (root / "_build" / "audit.py").write_text("import time; time.sleep(5)", encoding="utf-8")
        (build / "build.py").write_text("", encoding="utf-8")
        result = adapter.run_engine(
            adapter.EnginePaths(root), ["audit.py", "--shots", "s.json"], timeout=0.5,
        )
        assert result["ok"] is False and "超时" in result["error"]


class TestTools:
    @pytest.mark.asyncio
    async def test_probe_tool_success(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        tool = PvEngineProbeTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(shots="s.json")
        assert result.status == "success"
        assert result.output["ok"] is True
        assert result.output["engine_root"] == str(root)

    @pytest.mark.asyncio
    async def test_probe_tool_without_engine_is_honest(self, monkeypatch) -> None:
        monkeypatch.delenv(adapter.ENGINE_ENV, raising=False)
        tool = PvEngineProbeTool()
        tool.config = {}
        result = await tool.execute(shots="s.json")
        assert result.status == "error"
        assert "CLIPWRIGHT_PV_ENGINE_DIR" in (result.error or "")

    @pytest.mark.asyncio
    async def test_audit_tool_maps_exit_code_to_verdict(self, tmp_path) -> None:
        failing = _fake_engine(tmp_path / "bad", audit_exit=1)
        tool = PvEngineAuditTool()
        tool.config = {"engine_dir": str(failing)}
        result = await tool.execute(shots="s.json")
        assert result.status == "error"
        assert result.output["verdict"] == "fail"
        assert result.output["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_stills_tool_writes_frames(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        tool = PvEngineStillsTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(times=[1.0, 2.5], shots="s.json", out_dir="stills")
        assert result.status == "success"
        assert len(result.output["still_files"]) == 2

    @pytest.mark.asyncio
    async def test_stills_tool_requires_times(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        tool = PvEngineStillsTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(times=[], shots="s.json")
        assert result.status == "error"
        assert "times" in (result.error or "")

    @pytest.mark.asyncio
    async def test_stills_failure_is_not_faked_as_success(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine", stills=False)
        tool = PvEngineStillsTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(times=[1.0], shots="s.json", out_dir="stills")
        assert result.status == "error"
        assert "assets" in (result.error or "")


class TestPluginAndSkill:
    def test_plugin_contributions(self) -> None:
        contributions = PvEnginePlugin().contributions()
        ids = {item.id for item in contributions}
        assert ids == {"pv_engine_probe", "pv_engine_audit", "pv_engine_stills", "pv_engine_review"}
        kinds = {item.kind.value for item in contributions}
        assert kinds == {"tool", "skill"}

    def test_manifest_declares_same_contributions(self) -> None:
        manifest = (Path(__file__).resolve().parents[2] / "plugins" / "pv_engine" / "plugin.yaml")
        text = manifest.read_text(encoding="utf-8")
        for tool_id in ("pv_engine_probe", "pv_engine_audit", "pv_engine_stills"):
            assert tool_id in text
        assert "pv_engine_review" in text

    @pytest.mark.asyncio
    async def test_skill_runs_probe_and_audit(self, tmp_path, monkeypatch) -> None:
        from clipwright.tool.registry import ToolRegistry

        root = _fake_engine(tmp_path / "engine")
        probe = PvEngineProbeTool()
        audit = PvEngineAuditTool()
        for tool in (probe, audit):
            tool.config = {"engine_dir": str(root)}
            ToolRegistry.register(tool)
        try:
            skill = PvEngineReviewSkill()
            result = await skill.execute(shots="s.json")
            assert result.status == "success"
            assert result.output["audit"]["output"]["verdict"] == "pass"
        finally:
            for name in ("pv_engine_probe", "pv_engine_audit"):
                ToolRegistry.unregister(name)


class TestTiers:
    def test_tiers_registered(self) -> None:
        from clipwright.services.capability_matrix import TIER_MAP

        assert TIER_MAP["pv_engine_probe"] == "read"
        assert TIER_MAP["pv_engine_audit"] == "read"
        assert TIER_MAP["pv_engine_stills"] == "write_local"


class TestAgentCallable:
    """agent/子 agent 能不能看见这三个工具，取决于引擎有没有配好。"""

    def _register(self, plugin):
        from clipwright.tool.registry import ToolRegistry

        tools = [c.object for c in plugin.contributions() if c.kind.value == "tool"]
        for tool in tools:
            ToolRegistry.register(tool)
        return tools, ToolRegistry

    def test_hidden_when_engine_missing(self, monkeypatch) -> None:
        monkeypatch.delenv(adapter.ENGINE_ENV, raising=False)
        tools, registry = self._register(PvEnginePlugin())
        try:
            names = [t.name for t in registry.list_agent_callable()]
            assert not [n for n in names if n.startswith("pv_engine")]
        finally:
            for tool in tools:
                registry.unregister(tool.name)

    def test_visible_when_engine_configured(self, tmp_path, monkeypatch) -> None:
        root = _fake_engine(tmp_path / "engine")
        monkeypatch.setenv(adapter.ENGINE_ENV, str(root))
        tools, registry = self._register(PvEnginePlugin())
        try:
            names = [t.name for t in registry.list_agent_callable()]
            assert sorted(n for n in names if n.startswith("pv_engine")) == [
                "pv_engine_audit", "pv_engine_probe", "pv_engine_stills",
            ]
        finally:
            for tool in tools:
                registry.unregister(tool.name)



@pytest.mark.skipif(
    not (sys.platform and adapter.resolve_engine() is not None),
    reason="未配置真引擎（CLIPWRIGHT_PV_ENGINE_DIR）",
)
class TestRealEngine:
    """真引擎存在时才跑：只跑离线安全的 probe。"""

    @pytest.mark.asyncio
    async def test_probe_on_real_engine(self) -> None:
        paths = adapter.resolve_engine()
        assert paths is not None
        if paths.validate():
            pytest.skip(paths.validate())
        result = adapter.run_engine(paths, adapter.probe_args("shots_v20.json"), timeout=120)
        if result.get("ok") is False and "No such file" in str(result.get("stderr", "")):
            pytest.skip("引擎里没有 shots_v20.json")
        assert result["ok"] is True, json.dumps(result, ensure_ascii=False)[:400]
