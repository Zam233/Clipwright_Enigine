"""V8：项目决策记忆 `memory.md` 四段式（Strategy / Decisions / Reasoning / Outstanding）。

为什么要有这一层：项目已有**机器可读**历史（时间线版本 / Agent 状态 / 学习事件），
缺的是**人可读的决策日志**——"当时的判断与理由"。本组测试覆盖：
- 解析/序列化往返（含人工手改后的再解析）；
- 追加语义（不覆盖、不吞掉人工编辑）；
- 中文段落别名归一化；
- 摘要（喂 LLM 的紧凑形态）；
- API 三端点（GET/PUT/POST append）+ 归档往返 + 复制项目带记忆。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.schema.tool import ToolStatus
from clipwright.services.project_memory import (
    SECTIONS,
    ProjectMemory,
    append_entries,
    load_memory,
    memory_path,
    parse_memory,
    render_summary,
    save_memory,
)


class TestSerialize:
    def test_empty_memory_renders_four_sections(self) -> None:
        text = ProjectMemory().to_markdown()
        for section in SECTIONS:
            assert f"## {section}" in text or f"## {section} ·" in text
        assert text.count("（暂无）") == 4

    def test_roundtrip_preserves_entries(self) -> None:
        mem = ProjectMemory()
        mem.add("Strategy", "做 3 分钟竖屏科普，节奏前紧后松")
        mem.add("Decisions", "片头不用品牌卡，直接进冲突")
        mem.add("Reasoning", "前 3 秒留存优先")
        mem.add("Outstanding", "第 4 分场素材仍是备选")
        text = mem.to_markdown()

        back = parse_memory(text)
        assert back.count() == 4
        assert back.entries["Strategy"][0].text == "做 3 分钟竖屏科普，节奏前紧后松"
        assert back.entries["Outstanding"][0].text == "第 4 分场素材仍是备选"

    def test_timestamp_and_source_survive_roundtrip(self) -> None:
        mem = ProjectMemory()
        mem.add("Decisions", "改用 b-roll 覆盖口播", source="edit_agent")
        back = parse_memory(mem.to_markdown())
        entry = back.entries["Decisions"][0]
        assert entry.text == "改用 b-roll 覆盖口播"
        assert entry.at                      # 时间戳自动打上
        assert entry.source == "edit_agent"  # 来源可还原

    def test_markdown_is_human_editable(self) -> None:
        """人工手改（非 `- ` 前缀、加粗、乱序段落）也要能解析回来。"""
        text = "\n".join([
            "# 我的项目记忆",
            "",
            "随手写的前言。",
            "",
            "## Reasoning · 理由",
            "这一条没有短横线",
            "- **加粗**的条目",
            "",
            "## Strategy",
            "- 补的一条策略",
            "",
        ])
        mem = parse_memory(text)
        assert [e.text for e in mem.entries["Reasoning"]] == ["这一条没有短横线", "**加粗**的条目"]
        assert [e.text for e in mem.entries["Strategy"]] == ["补的一条策略"]
        assert "随手写的前言" in mem.preamble

    def test_preamble_survives(self) -> None:
        mem = ProjectMemory()
        mem.preamble = "本片是客户 A 的系列第 2 集。"
        mem.add("Strategy", "延续第 1 集的视觉")
        back = parse_memory(mem.to_markdown())
        assert "客户 A" in back.preamble


class TestSectionNames:
    @pytest.mark.parametrize("alias,expect", [
        ("Strategy", "Strategy"), ("strategy", "Strategy"),
        ("策略", "Strategy"), ("方向", "Strategy"),
        ("Decisions", "Decisions"), ("决策", "Decisions"), ("决定", "Decisions"),
        ("Reasoning", "Reasoning"), ("理由", "Reasoning"), ("依据", "Reasoning"),
        ("Outstanding", "Outstanding"), ("待办", "Outstanding"), ("风险", "Outstanding"),
    ])
    def test_aliases_normalize(self, alias: str, expect: str) -> None:
        mem = ProjectMemory()
        mem.add(alias, f"来自 {alias}")
        assert mem.entries[expect][0].text == f"来自 {alias}"

    def test_unknown_section_raises(self) -> None:
        with pytest.raises(ValueError, match="未知段落"):
            ProjectMemory().add("Thoughts", "x")

    def test_append_entries_rejects_unknown_section(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            append_entries(tmp_path, {"Thoughts": ["x"]})


class TestAppendSemantics:
    def test_append_does_not_clobber_human_edits(self, tmp_path) -> None:
        """核心不变量：程序追加不能把人工加的内容冲掉。"""
        mem = ProjectMemory()
        mem.add("Strategy", "程序写的第一条")
        save_memory(tmp_path, mem)

        # 人手工编辑：加一句前言 + 一条决策
        text = memory_path(tmp_path).read_text(encoding="utf-8")
        text = text.replace("# 项目决策记忆", "# 项目决策记忆\n\n人工补充的说明。")
        text = text.replace("## Decisions · 决策\n", "## Decisions · 决策\n\n- 人工写的决策\n")
        memory_path(tmp_path).write_text(text, encoding="utf-8")

        after = append_entries(tmp_path, {"Decisions": ["程序追加的决策"]})
        texts = [e.text for e in after.entries["Decisions"]]
        assert texts == ["人工写的决策", "程序追加的决策"]  # 人工那条还在
        assert "人工补充的说明" in after.preamble

    def test_bare_string_is_one_entry_not_chars(self, tmp_path) -> None:
        """回归：字符串可迭代——`{"Strategy": "竖屏科普"}` 必须是一条，不是四个单字。"""
        mem = append_entries(tmp_path, {"Strategy": "竖屏科普"})
        assert [e.text for e in mem.entries["Strategy"]] == ["竖屏科普"]

    def test_append_accepts_pairs_and_dict(self, tmp_path) -> None:
        append_entries(tmp_path, [("Strategy", "a"), ("Reasoning", "b")])
        mem = append_entries(tmp_path, {"Outstanding": ["c", "d"]})
        assert mem.count() == 4
        assert [e.text for e in mem.entries["Outstanding"]] == ["c", "d"]

    def test_append_to_missing_project_creates_file(self, tmp_path) -> None:
        target = tmp_path / "proj_x"
        mem = append_entries(target, {"Strategy": "新项目第一条"})
        assert mem.count() == 1
        assert memory_path(target).is_file()

    def test_load_missing_project_is_empty_not_error(self, tmp_path) -> None:
        mem = load_memory(tmp_path / "nope")
        assert mem.is_empty()  # 新项目没有记忆是正常的，不该抛异常

    def test_load_corrupt_file_does_not_raise(self, tmp_path) -> None:
        (tmp_path / "memory.md").write_bytes(b"\xff\xfe\x00bad")  # 非法 UTF-8
        assert load_memory(tmp_path).is_empty()

    def test_empty_text_is_not_recorded(self, tmp_path) -> None:
        mem = append_entries(tmp_path, {"Strategy": ["", "   ", "有效一条"]})
        assert [e.text for e in mem.entries["Strategy"]] == ["有效一条"]

    def test_save_is_atomic_no_tmp_left(self, tmp_path) -> None:
        append_entries(tmp_path, {"Strategy": "x"})
        assert not (tmp_path / "memory.md.tmp").exists()
        assert memory_path(tmp_path).read_text(encoding="utf-8").count("# 项目决策记忆") == 1


class TestSummary:
    def test_empty_summary_is_empty_string(self) -> None:
        assert render_summary(ProjectMemory()) == ""

    def test_summary_keeps_latest_and_counts_overflow(self) -> None:
        mem = ProjectMemory()
        for i in range(8):
            mem.add("Decisions", f"决策 {i}")
        summary = render_summary(mem, limit=3)
        assert "决策 7" in summary and "决策 5" in summary
        assert "决策 4" not in summary
        assert "另有 5 条更早记录" in summary

    def test_summary_omits_empty_sections(self) -> None:
        mem = ProjectMemory()
        mem.add("Outstanding", "只剩这一条")
        summary = render_summary(mem)
        assert summary.count("## ") == 1
        assert "Outstanding" in summary


class TestProjectMemoryTool:
    def _run(self, **kwargs):
        import asyncio
        from clipwright.tool.project_memory import ProjectMemoryTool
        return asyncio.run(ProjectMemoryTool().execute(**kwargs))

    def test_registered(self) -> None:
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.registry import ToolRegistry
        register_builtin_tools()
        tools = ToolRegistry.list()
        names = [getattr(t, "name", str(t)) for t in (tools.values() if isinstance(tools, dict) else tools)]
        assert "project_memory" in names

    def test_read_and_append_roundtrip(self, tmp_path) -> None:
        res = self._run(action="append", project_dir=str(tmp_path),
                        entries={"Decisions": ["改用 b-roll"]}, source="test")
        assert res.status == ToolStatus.SUCCESS
        assert res.output["appended"] == 1

        res = self._run(action="read", project_dir=str(tmp_path))
        assert res.status == ToolStatus.SUCCESS
        assert res.output["sections"]["Decisions"] == ["改用 b-roll"]
        assert "改用 b-roll" in res.output["summary"]

    def test_accepts_pairs(self, tmp_path) -> None:
        res = self._run(action="append", project_dir=str(tmp_path),
                        entries=[["策略", "竖屏"], ["待办", "补片尾"]])
        assert res.output["appended"] == 2
        assert res.output["sections"]["Strategy"] == ["竖屏"]

    def test_requires_target(self, tmp_path) -> None:
        res = self._run(action="read")
        assert res.status == ToolStatus.ERROR
        assert "project_id 或 project_dir" in (res.error or "")

    def test_missing_dir_is_error(self, tmp_path) -> None:
        res = self._run(action="read", project_dir=str(tmp_path / "ghost"))
        assert res.status == ToolStatus.ERROR

    def test_unknown_action(self, tmp_path) -> None:
        res = self._run(action="delete", project_dir=str(tmp_path))
        assert res.status == ToolStatus.ERROR
        assert "未知 action" in (res.error or "")

    def test_bad_section_is_callers_fault(self, tmp_path) -> None:
        res = self._run(action="append", project_dir=str(tmp_path), entries={"Thoughts": ["x"]})
        assert res.status == ToolStatus.ERROR
        assert "未知段落" in (res.error or "")

    def test_llm_schema(self) -> None:
        from clipwright.tool.project_memory import ProjectMemoryTool
        schema = ProjectMemoryTool().to_llm_tool()
        assert schema["name"] == "project_memory"
        assert "action" in schema["input_schema"]["properties"]


# ── API ──

@pytest.fixture()
def api(tmp_path, monkeypatch):
    from clipwright.api import project as project_api
    from clipwright.services.project_manager import ProjectManager

    monkeypatch.setattr(project_api, "_manager", ProjectManager(projects_dir=tmp_path / "projects"))
    app = FastAPI()
    app.include_router(project_api.router)
    client = TestClient(app)
    created = client.post("/api/project", json={"name": "记忆测试"}).json()
    return client, created["id"], tmp_path / "projects" / created["id"]


class TestMemoryApi:
    def test_get_empty_memory_is_not_404(self, api) -> None:
        client, pid, _ = api
        r = client.get(f"/api/project/{pid}/memory")
        assert r.status_code == 200
        body = r.json()
        assert body["empty"] is True and body["count"] == 0
        for section in SECTIONS:
            assert section in body["sections"]

    def test_get_unknown_project_404(self, api) -> None:
        client, _, _ = api
        assert client.get("/api/project/proj_ghost/memory").status_code == 404

    def test_append_then_get(self, api) -> None:
        client, pid, proj_dir = api
        r = client.post(f"/api/project/{pid}/memory/append",
                        json={"entries": {"Decisions": ["片头砍掉品牌卡"]},
                              "source": "edit_agent"})
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert (proj_dir / "memory.md").is_file()  # 落在项目目录里

        body = client.get(f"/api/project/{pid}/memory").json()
        assert body["sections"]["Decisions"] == ["片头砍掉品牌卡"]
        assert body["entries"]["Decisions"][0]["source"] == "edit_agent"

    def test_append_dict_of_lists_and_pairs(self, api) -> None:
        client, pid, _ = api
        r = client.post(f"/api/project/{pid}/memory/append",
                        json={"entries": {"待办": ["补片尾字幕", "确认版权"]}})
        assert r.json()["count"] == 2
        r = client.post(f"/api/project/{pid}/memory/append",
                        json={"entries": [["Strategy", "竖屏科普"]]})
        assert r.json()["count"] == 3

    def test_append_rejects_bad_payload(self, api) -> None:
        client, pid, _ = api
        assert client.post(f"/api/project/{pid}/memory/append",
                           json={"entries": "nope"}).status_code == 400
        assert client.post(f"/api/project/{pid}/memory/append",
                           json={"entries": {}}).status_code == 400
        r = client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Thoughts": ["x"]}})
        assert r.status_code == 400 and "未知段落" in r.json()["detail"]

    def test_put_replaces_whole_document(self, api) -> None:
        client, pid, _ = api
        client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Strategy": "旧的"}})
        text = "\n".join([
            "# 项目决策记忆", "", "## Strategy · 策略", "- 人工重写的策略", "",
            "## Outstanding · 待办", "- 人工加的待办", "",
        ])
        r = client.put(f"/api/project/{pid}/memory", json={"content": text})
        assert r.status_code == 200
        body = r.json()
        assert body["sections"]["Strategy"] == ["人工重写的策略"]
        assert body["sections"]["Outstanding"] == ["人工加的待办"]
        assert body["count"] == 2

    def test_summary_endpoint_limit(self, api) -> None:
        client, pid, _ = api
        for i in range(7):
            client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Decisions": [f"d{i}"]}})
        body = client.get(f"/api/project/{pid}/memory/summary", params={"limit": 2}).json()
        assert body["count"] == 7
        assert body["summary"].count("- d") == 2
        assert "另有 5 条更早记录" in body["summary"]

    def test_summary_empty_when_no_memory(self, api) -> None:
        client, pid, _ = api
        body = client.get(f"/api/project/{pid}/memory/summary").json()
        assert body["summary"] == ""

    def test_archive_roundtrip_carries_memory(self, api) -> None:
        client, pid, _ = api
        client.post(f"/api/project/{pid}/memory/append",
                    json={"entries": {"Strategy": "归档要保持的策略", "待办": ["归档要保持的待办"]}})
        r = client.get(f"/api/project/{pid}/archive")
        assert r.status_code == 200
        assert b"memory.md" in r.content  # zip 内确有该条目

        imp = client.post("/api/project/import-archive",
                          files={"file": ("a.zip", r.content, "application/zip")})
        assert imp.status_code == 200, imp.text
        assert imp.json()["memory_restored"] is True
        new_id = imp.json()["id"]
        body = client.get(f"/api/project/{new_id}/memory").json()
        assert body["sections"]["Strategy"] == ["归档要保持的策略"]
        assert body["sections"]["Outstanding"] == ["归档要保持的待办"]

    def test_archive_without_memory_has_no_entry(self, api) -> None:
        client, pid, _ = api
        r = client.get(f"/api/project/{pid}/archive")
        assert b"memory.md" not in r.content
        imp = client.post("/api/project/import-archive",
                          files={"file": ("a.zip", r.content, "application/zip")})
        assert imp.json()["memory_restored"] is False

    def test_duplicate_carries_memory(self, api) -> None:
        client, pid, _ = api
        client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Strategy": "副本也要的策略"}})
        dup = client.post(f"/api/project/{pid}/duplicate").json()
        body = client.get(f"/api/project/{dup['id']}/memory").json()
        assert body["sections"]["Strategy"] == ["副本也要的策略"]

    def test_purge_removes_memory(self, api) -> None:
        client, pid, proj_dir = api
        client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Strategy": "会被删掉"}})
        assert (proj_dir / "memory.md").is_file()
        client.post(f"/api/project/{pid}/trash")
        assert client.delete(f"/api/project/{pid}/trash").status_code == 200
        assert not proj_dir.exists()  # 彻底删除时记忆随之消失（无残留）

    def test_soft_delete_keeps_memory(self, api) -> None:
        """回收站是软删除——记忆必须留着，否则「恢复」会丢决策历史。"""
        client, pid, proj_dir = api
        client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Strategy": "恢复后还要在"}})
        client.post(f"/api/project/{pid}/trash")
        client.post(f"/api/project/{pid}/restore")
        assert (proj_dir / "memory.md").is_file()
        body = client.get(f"/api/project/{pid}/memory").json()
        assert body["sections"]["Strategy"] == ["恢复后还要在"]

    def test_memory_not_stored_inside_project_json(self, api) -> None:
        """记忆是独立文件，不塞进 project.json（否则每次保存时间线都重写它）。"""
        client, pid, proj_dir = api
        client.post(f"/api/project/{pid}/memory/append", json={"entries": {"Strategy": "独立文件"}})
        data = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
        assert "memory" not in data
        assert (proj_dir / "memory.md").is_file()
