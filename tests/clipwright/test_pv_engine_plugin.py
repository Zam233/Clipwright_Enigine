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
from plugins.pv_engine import main as pv_main
from plugins.pv_engine.main import (
    PvEngineAuditTool,
    PvEngineInitTool,
    PvEnginePlugin,
    PvEngineProbeTool,
    PvEngineRenderTool,
    PvEngineReviewSkill,
    PvEngineStillsTool,
    PvEngineTimelineToProjectTool,
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
        reason = adapter.EnginePaths(tmp_path).validate()
        # 二期：两种布局都不像时，两条候选原因都要给出
        assert "engine/cli.py" in reason and "_build/audit.py" in reason

    def test_missing_build(self, tmp_path) -> None:
        (tmp_path / "_build").mkdir()
        (tmp_path / "_build" / "audit.py").write_text("", encoding="utf-8")
        reason = adapter.EnginePaths(tmp_path).validate()
        assert "engine/cli.py" in reason and "_build/pv/build.py" in reason

    def test_valid(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "engine")
        assert adapter.EnginePaths(root).validate() == ""

    def test_package_layout(self, tmp_path) -> None:
        """``engine/cli.py`` 存在即 package 布局（``K:\\video engine`` 那种）。"""
        root = tmp_path / "engine_pkg"
        (root / "engine").mkdir(parents=True)
        (root / "engine" / "cli.py").write_text("", encoding="utf-8")
        paths = adapter.EnginePaths(root)
        assert paths.layout == adapter.LAYOUT_PACKAGE
        assert paths.validate() == ""
        assert paths.build_dir == root
        assert adapter.probe_args("p.json", paths.layout) == [
            "-m", "engine.cli", "info", "p.json",
        ]


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
    #: 二期新增的能力面（v1 只有 3 工具 + 1 技能）
    TOOL_IDS = {
        "pv_engine_doctor", "pv_engine_init", "pv_engine_probe", "pv_engine_audit",
        "pv_engine_stills", "pv_engine_render", "pv_engine_beats",
        "pv_engine_align", "pv_engine_timeline_to_project",
    }
    SKILL_IDS = {"pv_engine_review", "pv_engine_authoring"}

    def test_plugin_contributions(self) -> None:
        contributions = PvEnginePlugin().contributions()
        ids = {item.id for item in contributions}
        assert ids == self.TOOL_IDS | self.SKILL_IDS
        kinds = {item.kind.value for item in contributions}
        assert kinds == {"tool", "skill"}

    def test_manifest_declares_same_contributions(self) -> None:
        manifest = (Path(__file__).resolve().parents[2] / "plugins" / "pv_engine" / "plugin.yaml")
        text = manifest.read_text(encoding="utf-8")
        for cid in self.TOOL_IDS | self.SKILL_IDS:
            assert cid in text

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
        # 二期：内省只读、脚手架/转换写工作区、渲染与音频分析 costly
        assert TIER_MAP["pv_engine_doctor"] == "read"
        assert TIER_MAP["pv_engine_init"] == "write_local"
        assert TIER_MAP["pv_engine_timeline_to_project"] == "write_local"
        for name in ("pv_engine_render", "pv_engine_beats", "pv_engine_align"):
            assert TIER_MAP[name] == "costly"


class TestAgentCallable:
    """agent 能不能看见 pv 工具：doctor 恒可见（诊断用），其余看引擎是否配好。"""

    GATED = {
        "pv_engine_align", "pv_engine_audit", "pv_engine_beats", "pv_engine_init",
        "pv_engine_probe", "pv_engine_render", "pv_engine_stills",
        "pv_engine_timeline_to_project",
    }

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
            names = {t.name for t in registry.list_agent_callable()}
            # 只剩 doctor：目录填错时它必须能回答"错在哪"
            assert {n for n in names if n.startswith("pv_engine")} == {"pv_engine_doctor"}
        finally:
            for tool in tools:
                registry.unregister(tool.name)

    def test_visible_when_engine_configured(self, tmp_path, monkeypatch) -> None:
        root = _fake_engine(tmp_path / "engine")
        monkeypatch.setenv(adapter.ENGINE_ENV, str(root))
        tools, registry = self._register(PvEnginePlugin())
        try:
            names = sorted(n for n in (t.name for t in registry.list_agent_callable())
                           if n.startswith("pv_engine"))
            assert set(names) == self.GATED | {"pv_engine_doctor"}
        finally:
            for tool in tools:
                registry.unregister(tool.name)

    def test_config_from_plugin_config_not_env(self, tmp_path, monkeypatch) -> None:
        """二期 F2：配置页填的 engine_dir 必须生效（v1 只认环境变量）。"""
        monkeypatch.delenv(adapter.ENGINE_ENV, raising=False)
        root = _fake_engine(tmp_path / "engine")
        tools, registry = self._register(PvEnginePlugin())
        try:
            for tool in tools:
                if tool.name.startswith("pv_engine"):
                    tool.config = {"engine_dir": str(root)}
            names = {t.name for t in registry.list_agent_callable()}
            assert "pv_engine_probe" in names
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


# ────────────────────────────── 二期（v2） ──────────────────────────────


def _package_engine(root: Path) -> Path:
    """``engine/cli.py`` 存在的 package 布局（不依赖真渲染/素材）。"""
    (root / "engine").mkdir(parents=True, exist_ok=True)
    (root / "engine" / "__init__.py").write_text("", encoding="utf-8")
    (root / "engine" / "cli.py").write_text("", encoding="utf-8")
    return root


def _audit_engine(root: Path, stdout: str, exit_code: int = 0) -> Path:
    """一个只会打印审计文本的 build 布局假引擎。"""
    build = root / "_build"
    (build / "pv").mkdir(parents=True, exist_ok=True)
    (build / "audit.py").write_text(
        "import sys\nprint(%r)\nsys.exit(%d)\n" % (stdout, exit_code),
        encoding="utf-8",
    )
    (build / "pv" / "__init__.py").write_text("", encoding="utf-8")
    (build / "pv" / "build.py").write_text("", encoding="utf-8")
    return root


class TestAuditParsing:
    def test_padded_levels_are_parsed(self) -> None:
        """真实输出是 ``[  OK]`` / ``[WARN]`` / ``[FAIL]``（带填充），必须都认。"""
        stdout = (
            "[  OK] R01 时间轴连续              无空洞\n"
            "[WARN] R04 字号下限               镜@0.00 「竖屏字幕」64px\n"
            "[FAIL] R05 文字不出画/参数在界内    镜@0.00 「x」越出镜头\n"
            "\n2 shot(s), 2 cue(s), 1 FAIL\n"
        )
        parsed = adapter.parse_audit(stdout)
        assert parsed["parsed"] is True
        assert parsed["counts"] == {"FAIL": 1, "WARN": 1, "OK": 1}
        assert [r["id"] for r in parsed["rules"]] == ["R01", "R04", "R05"]

    def test_unparsed_is_not_faked_as_pass(self) -> None:
        parsed = adapter.parse_audit("something exploded")
        assert parsed["parsed"] is False
        assert parsed["counts"]["FAIL"] == 0 and parsed["rules"] == []

    @pytest.mark.asyncio
    async def test_warn_only_is_pass_with_warnings(self, tmp_path) -> None:
        root = _audit_engine(tmp_path / "e", "[WARN] R04 字号下限 12px\n", 0)
        tool = PvEngineAuditTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(shots="s.json")
        assert result.status == "success"
        assert result.output["verdict"] == "pass_with_warnings"


class TestDoctor:
    def test_build_layout_is_honest(self, tmp_path) -> None:
        """上游 build 布局不提供模块内省：必须如实说"不支持"，不返回手抄快照。"""
        root = _fake_engine(tmp_path / "e")
        report = adapter.run_doctor(adapter.EnginePaths(root))
        assert report["available"] is False
        assert "build" in report["error"]

    def test_doctor_args_is_reflection_script(self) -> None:
        args = adapter.doctor_args()
        assert args[0] == "-c" and "engine.block" in args[1]


class TestTimelineToProject:
    @pytest.mark.asyncio
    async def test_maps_cues_and_tiles_shots(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pv_main, "_plugin_data", lambda: tmp_path / "data")
        root = _package_engine(tmp_path / "engine")
        tool = PvEngineTimelineToProjectTool()
        tool.config = {"engine_dir": str(root)}
        timeline = {
            "id": "t1", "width": 1080, "height": 1920, "fps": 30, "duration_sec": 6,
            "tracks": [{"id": "cap", "kind": "caption", "clips": [
                {"id": "c1", "kind": "caption", "text": "第一句",
                 "start_sec": 0.2, "duration_sec": 2.0, "font_size": 48,
                 "font_color": "#ffffff", "text_align": "center",
                 "metadata": {"position": "bottom",
                              "pv": {"band": 0.8, "glyph": "你×1.2↑4"}}},
                {"id": "c2", "kind": "caption", "text": "第二句",
                 "start_sec": 2.5, "duration_sec": 2.0,
                 "metadata": {"position": "top"}},
            ]}],
        }
        result = await tool.execute(timeline=timeline, job_id="t")
        assert result.status == "success"
        project = result.output["project"]
        shots = project["shots"]
        # 镜头首尾相接、覆盖整条时间轴（R01）
        assert shots[0]["t0"] == 0.0
        assert all(abs(shots[i]["t1"] - shots[i + 1]["t0"]) < 1e-6 for i in range(len(shots) - 1))
        assert shots[-1]["t1"] == 6.0
        cue = shots[0]["text"][0]
        assert cue["layout"] == "low_center"      # bottom + center
        assert cue["px"] == 48 and cue["color"] == "#ffffff"
        assert cue["band"] == 0.8 and cue["kind"] == "band"
        # glyph 字符串被转成 dict 列表（直接写字符串会让引擎炸）
        assert isinstance(cue["glyph"], list) and cue["glyph"][0]["s"] == 1.2
        # 第二条按 position=top 映射到 up_center
        assert shots[-1]["text"][0]["layout"] == "up_center"

    @pytest.mark.asyncio
    async def test_empty_timeline_is_error(self, tmp_path) -> None:
        root = _package_engine(tmp_path / "engine")
        tool = PvEngineTimelineToProjectTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(timeline={"tracks": []})
        assert result.status == "error"


class TestRenderBudget:
    @pytest.mark.asyncio
    async def test_rejects_over_budget_without_running(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pv_main, "_plugin_data", lambda: tmp_path / "data")
        root = _package_engine(tmp_path / "engine")
        project = tmp_path / "project.json"
        project.write_text(json.dumps({"meta": {"fps": 30, "duration": 100}, "shots": []}),
                           encoding="utf-8")
        tool = PvEngineRenderTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(shots=str(project), max_frames=10, job_id="r")
        assert result.status == "error"
        assert "超出预算" in (result.error or "")
        assert result.output["est_frames"] == 3000


class TestInitGuard:
    @pytest.mark.asyncio
    async def test_init_requires_package_layout(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "e")
        tool = PvEngineInitTool()
        tool.config = {"engine_dir": str(root)}
        result = await tool.execute(preset="lyric")
        assert result.status == "error"
        assert "engine/" in (result.error or "")


class TestConfigResolution:
    def test_engine_available_uses_plugin_config(self, tmp_path) -> None:
        root = _fake_engine(tmp_path / "e")
        assert pv_main.engine_available({"engine_dir": str(root)}) == ""
        assert pv_main.engine_available({}) != ""


class TestLoaderInjection:
    """F2 端到端：插件配置页保存的 engine_dir 必须真的到工具手里。"""

    def test_save_config_reaches_tool_instance(self, tmp_path) -> None:
        from clipwright.plugins.loader import PluginLoader
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.registry import ToolRegistry

        if not ToolRegistry.list():
            register_builtin_tools()
        engine = _fake_engine(tmp_path / "engine")
        loader = PluginLoader(
            plugin_dir=Path(__file__).resolve().parents[2] / "plugins",
            data_dir=tmp_path / "data",
        )
        loader.set_enabled("pv_engine", True)
        loader.save_config("pv_engine", {"fields": {
            "engine_dir": {"type": "string", "value": str(engine), "label": "PV 引擎目录"},
        }})
        loader.load("pv_engine")
        try:
            probe = ToolRegistry.get("pv_engine_probe")
            assert probe is not None
            assert (probe.config or {}).get("engine_dir") == str(engine)
            assert probe.is_configured() is True
        finally:
            loader.unload("pv_engine")
