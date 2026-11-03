"""文件读写工具（主 Agent / 子代理的基础能力）。

这套工具决定 agent 到底"能不能干活"：没有 read/write，它只能间接操作媒体，
连看一眼字幕文本、把核查结论落成 md 都做不到。

四个必须成立的安全边界：
1. 路径白名单（越界拒绝）；
2. 体积上限（读 100KB / 写 200KB）；
3. 写文件不自动创建父目录；
4. 父目录也要在白名单内（防 `allowed/../../`）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.tool.fs_tools import ListDirTool, ReadFileTool, WriteFileTool


@pytest.fixture()
def webroot(tmp_path, monkeypatch):
    """把白名单收窄到 tmp_path，避免测试依赖真实项目目录。"""
    import clipwright.security as security

    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr(security, "allowed_media_roots", lambda: [root])
    return root


def test_read_file_returns_content(webroot):
    target = webroot / "note.txt"
    target.write_text("第一行\n第二行", encoding="utf-8")
    out = asyncio.run(ReadFileTool().execute(path=str(target)))
    assert out.status == "success"
    assert "第二行" in out.output["content"]
    assert out.output["truncated"] is False


def test_read_file_truncates_long_text(webroot):
    target = webroot / "big.txt"
    target.write_text("x" * 5000, encoding="utf-8")
    out = asyncio.run(ReadFileTool().execute(path=str(target), max_chars=100))
    assert out.output["truncated"] is True
    assert len(out.output["content"]) == 100


def test_read_file_rejects_outside_allowlist(webroot, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    out = asyncio.run(ReadFileTool().execute(path=str(outside)))
    assert out.status == "error"
    assert "不在允许的目录" in out.error


def test_read_file_missing(webroot):
    out = asyncio.run(ReadFileTool().execute(path=str(webroot / "nope.txt")))
    assert out.status == "not_found"


def test_read_file_size_cap(webroot, monkeypatch):
    target = webroot / "huge.bin"
    target.write_bytes(b"x" * 10)
    monkeypatch.setattr("clipwright.tool.fs_tools._READ_MAX_BYTES", 5)
    out = asyncio.run(ReadFileTool().execute(path=str(target)))
    assert out.status == "error"
    assert "文件过大" in out.error


def test_list_dir_returns_entries(webroot):
    (webroot / "a.txt").write_text("a", encoding="utf-8")
    (webroot / "sub").mkdir()
    out = asyncio.run(ListDirTool().execute(path=str(webroot)))
    names = [e["name"] for e in out.output["entries"]]
    assert "a.txt" in names and "sub" in names
    assert out.output["count"] == 2


def test_write_file_creates_and_overwrites(webroot):
    target = webroot / "out.md"
    first = asyncio.run(WriteFileTool().execute(path=str(target), content="v1"))
    assert first.status == "success"
    assert target.read_text(encoding="utf-8") == "v1"

    second = asyncio.run(WriteFileTool().execute(path=str(target), content="v2"))
    assert second.status == "success"
    assert target.read_text(encoding="utf-8") == "v2"
    assert second.output["bytes_before"] == 2


def test_write_file_append(webroot):
    target = webroot / "log.md"
    asyncio.run(WriteFileTool().execute(path=str(target), content="a"))
    asyncio.run(WriteFileTool().execute(path=str(target), content="b", append=True))
    assert target.read_text(encoding="utf-8") == "ab"


def test_write_file_rejects_outside_allowlist(webroot, tmp_path):
    out = asyncio.run(WriteFileTool().execute(path=str(tmp_path / "x.md"), content="x"))
    assert out.status == "error"
    assert "不在允许的目录" in out.error


def test_write_file_does_not_create_parent(webroot):
    """不自动 mkdir：自动建目录等于给了"任意位置落文件"的能力。"""
    target = webroot / "missing_dir" / "x.md"
    out = asyncio.run(WriteFileTool().execute(path=str(target), content="x"))
    assert out.status == "error"
    assert "父目录不存在" in out.error
    assert not (webroot / "missing_dir").exists()


def test_write_file_parent_traversal_blocked(webroot, tmp_path):
    """allowed/../evil.md —— 父目录不在白名单内，必须拒。"""
    outside_dir = webroot / ".."
    out = asyncio.run(WriteFileTool().execute(
        path=str(outside_dir / "evil.md"), content="x",
    ))
    assert out.status == "error"


def test_write_file_size_cap(webroot, monkeypatch):
    monkeypatch.setattr("clipwright.tool.fs_tools._WRITE_MAX_BYTES", 5)
    out = asyncio.run(WriteFileTool().execute(
        path=str(webroot / "big.md"), content="0123456789",
    ))
    assert out.status == "error"
    assert "内容过大" in out.error
    assert not (webroot / "big.md").exists()


def test_empty_path_rejected():
    for tool in (ReadFileTool(), ListDirTool(), WriteFileTool()):
        out = asyncio.run(tool.execute(path="", content="x") if isinstance(tool, WriteFileTool)
                          else tool.execute(path=""))
        assert out.status == "error"


def test_tools_are_agent_visible_in_registry():
    """三个文件工具必须进注册表并声明 agent_callable（否则主 agent 看不到）。"""
    from clipwright.tool import register_builtin_tools
    from clipwright.tool.registry import ToolRegistry

    register_builtin_tools()
    for name in ("read_file", "list_dir", "write_file"):
        tool = ToolRegistry.get(name)
        assert tool is not None, f"{name} 未注册"
        assert tool.agent_callable is True
        assert tool.is_available() is True
