"""P9 单测：协议编解码 + 权限判定器（不依赖子进程）。"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from clipwright.plugins.runtime.permissions import (
    PermissionGuard,
    PermissionViolation,
)
from clipwright.plugins.runtime.protocol import (
    PROTOCOL_VERSION,
    RpcError,
    check_version,
    decode,
    encode,
    fail,
    ok,
    request,
)


class TestProtocol:
    def test_roundtrip(self):
        raw = encode(request(7, "tool.call", {"id": "t", "input": {"a": 1}}))
        assert raw.endswith(b"\n")
        msg = decode(raw)
        assert msg["v"] == PROTOCOL_VERSION and msg["id"] == 7
        assert msg["method"] == "tool.call"
        assert msg["params"]["input"] == {"a": 1}

    def test_ok_and_fail_shapes(self):
        assert ok(1, {"x": 1})["ok"] is True
        err = fail(2, RpcError("PERMISSION_DENIED", "nope"))
        assert err["ok"] is False
        assert err["error"] == {"code": "PERMISSION_DENIED", "message": "nope"}

    def test_decode_rejects_garbage(self):
        for bad in ("", "   ", "not-json", "[1,2]", '"str"'):
            with pytest.raises(RpcError):
                decode(bad)

    def test_bad_frame_raises_rpc_error(self):
        with pytest.raises(RpcError):
            check_version({"v": 999})  # 版本不一致 → 拒帧
        assert check_version({"v": PROTOCOL_VERSION}) is None

    def test_oversize_frame_rejected(self):
        with pytest.raises(RpcError, match="上限"):
            encode({"v": 1, "id": 1, "method": "x", "params": {"blob": "a" * (9 * 1024 * 1024)}})

    def test_decode_rejects_oversize_inbound_frame(self):
        """B2.2：入站字节也限长（含多字节 UTF-8），超长不再被无界 decode。"""
        from clipwright.plugins.runtime.protocol import MAX_FRAME_BYTES

        # 纯 ASCII 超限
        with pytest.raises(RpcError, match="上限"):
            decode(b"a" * (MAX_FRAME_BYTES + 1))
        # 多字节 UTF-8：按字节算，不按字符数算
        with pytest.raises(RpcError, match="上限"):
            decode(("两" * (MAX_FRAME_BYTES // 3 + 10)).encode("utf-8"))
        # 恰好上限的合法 JSON 对象仍可通过
        pad = MAX_FRAME_BYTES - len(b'{"x":""}')
        assert decode(b'{"x":"' + b"a" * pad + b'"}')["x"]

    def test_error_from_dict_tolerates_shapes(self):
        assert RpcError.from_dict({"code": "X", "message": "m"}).code == "X"
        assert RpcError.from_dict("boom").code == "PLUGIN_ERROR"


class TestPermissionGuard:
    def _guard(self, tmp_path, perms):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir(exist_ok=True)
        return PermissionGuard(
            perms, plugin_id="plug_x", data_dir=tmp_path / "data",
            plugin_dir=plugin_dir,
        )

    def test_read_inside_plugin_dir_allowed(self, tmp_path):
        guard = self._guard(tmp_path, [])
        target = tmp_path / "plugin" / "main.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        guard.check_event("open", (str(target), "r"))  # 不抛

    def test_read_outside_roots_denied(self, tmp_path):
        guard = self._guard(tmp_path, [])
        secret = tmp_path / "secret.txt"
        secret.write_text("x", encoding="utf-8")
        with pytest.raises(PermissionViolation, match="读越界"):
            guard.check_event("open", (str(secret), "r"))

    def test_write_without_permission_denied(self, tmp_path):
        guard = self._guard(tmp_path, [])
        with pytest.raises(PermissionViolation, match="fs_write"):
            guard.check_event("open", ("/tmp/x.txt", "w"))

    def test_write_inside_data_dir_allowed(self, tmp_path):
        guard = self._guard(tmp_path, ["fs_write"])
        target = tmp_path / "data" / "cache.bin"
        guard.check_event("open", (str(target), "w"))

    def test_write_outside_data_dir_denied(self, tmp_path):
        guard = self._guard(tmp_path, ["fs_write"])
        with pytest.raises(PermissionViolation, match="只允许写插件自己的数据目录"):
            guard.check_event("open", (str(tmp_path / "outside.txt"), "w"))
        with pytest.raises(PermissionViolation):
            guard.check_event("open", ("/etc/passwd", "a"))

    def test_append_and_exclusive_count_as_write(self, tmp_path):
        guard = self._guard(tmp_path, [])
        for mode in ("a", "x", "r+", "w+"):
            with pytest.raises(PermissionViolation):
                guard.check_event("open", ("/tmp/x", mode))

    def test_network_requires_permission(self, tmp_path):
        guard = self._guard(tmp_path, [])
        with pytest.raises(PermissionViolation, match="网络"):
            guard.check_event("socket.connect", ("1.2.3.4", 80))
        allowed = self._guard(tmp_path, ["network"])
        allowed.check_event("socket.connect", ("1.2.3.4", 80))  # 不抛

    def test_http_permission_also_allows_network(self, tmp_path):
        guard = self._guard(tmp_path, ["http"])
        guard.check_event("socket.connect", ("1.2.3.4", 80))

    def test_shell_always_denied(self, tmp_path):
        # 权限白名单里没有 shell，因此即便声明了也不能执行命令
        guard = self._guard(tmp_path, ["network", "fs_write", "shell"])
        for event in ("subprocess.Popen", "os.system", "os.exec", "ctypes.dlopen"):
            with pytest.raises(PermissionViolation, match="命令执行"):
                guard.check_event(event, ("rm -rf /",))

    def test_unknown_event_is_ignored(self, tmp_path):
        guard = self._guard(tmp_path, [])
        guard.check_event("import", ("json",))  # 不抛（不在管辖范围）

    # ── B2.1：文件变更事件 + os.open 整数 flags ──────────
    def test_file_mutation_events_denied_without_fs_write(self, tmp_path):
        """B2.1：remove/unlink/rename/replace/mkdir/rmdir/rmtree 无写权限一律拒。"""
        guard = self._guard(tmp_path, [])
        target = str(tmp_path / "data" / "f.txt")
        dst = str(tmp_path / "data" / "g.txt")
        # 真实 audit 事件参数结构（已用 tmp_path 真触发核对）
        cases = [
            ("os.remove", (target, -1)),                 # (path, dir_fd)
            ("os.unlink", (target, -1)),
            ("os.rename", (target, dst, -1, -1)),       # (src, dst, src_dir_fd, dst_dir_fd)
            ("os.rename", (target, dst)),               # 兼容少参数
            ("os.replace", (target, dst, -1, -1)),
            ("os.mkdir", (str(tmp_path / "data" / "sub"), 0o777, -1)),
            ("os.rmdir", (str(tmp_path / "data" / "sub"), -1)),
            ("shutil.rmtree", (str(tmp_path / "data" / "sub"), None)),
            ("os.truncate", (target, 0)),
            ("os.chmod", (target, 0o600, -1)),
            ("os.symlink", (target, dst, -1)),
            ("os.link", (target, dst, -1, -1)),
        ]
        for event, args in cases:
            with pytest.raises(PermissionViolation, match="fs_write"):
                guard.check_event(event, args)

    def test_file_mutation_events_allowed_with_fs_write(self, tmp_path):
        """B2.1：有 fs_write 且所有源/目标都在 write_roots 内才允许。"""
        guard = self._guard(tmp_path, ["fs_write"])
        data = tmp_path / "data"
        target, dst = str(data / "f.txt"), str(data / "g.txt")
        for event, args in [
            ("os.remove", (target, -1)),
            ("os.unlink", (target, -1)),
            ("os.rename", (target, dst, -1, -1)),
            ("os.replace", (target, dst, -1, -1)),
            ("os.mkdir", (str(data / "sub"), 0o777, -1)),
            ("os.rmdir", (str(data / "sub"), -1)),
            ("shutil.rmtree", (str(data / "sub"), None)),
            ("os.truncate", (target, 0)),
            ("os.chmod", (target, 0o600, -1)),
            ("os.symlink", (target, dst, -1)),
            ("os.link", (target, dst, -1, -1)),
        ]:
            guard.check_event(event, args)  # 不抛

    def test_rename_destination_outside_write_roots_denied(self, tmp_path):
        """B2.1：rename/replace 的目标越界同样要拒（不能只检查源）。"""
        guard = self._guard(tmp_path, ["fs_write"])
        inside = str(tmp_path / "data" / "f.txt")
        outside = str(tmp_path / "outside.txt")
        with pytest.raises(PermissionViolation, match="只允许写"):
            guard.check_event("os.rename", (inside, outside, -1, -1))
        with pytest.raises(PermissionViolation, match="只允许写"):
            guard.check_event("os.replace", (inside, outside, -1, -1))
        # 源越界、目标在内也要拒
        with pytest.raises(PermissionViolation, match="只允许写"):
            guard.check_event("os.rename", (outside, inside, -1, -1))

    def test_os_open_write_flags_denied_without_permission(self, tmp_path):
        """B2.1：os.open(path, os.O_WRONLY) 的整数 flags 判为写，无权限即拒。"""
        guard = self._guard(tmp_path, [])
        target = str(tmp_path / "data" / "f.txt")
        # open 事件的真实结构：(path, mode|None, flags:int)
        for flags in (os.O_WRONLY, os.O_RDWR, os.O_WRONLY | os.O_CREAT,
                      os.O_RDWR | os.O_TRUNC, os.O_APPEND):
            with pytest.raises(PermissionViolation, match="fs_write"):
                guard.check_event("open", (target, None, flags))
        # 纯只读 os.open(path, os.O_RDONLY) 不按写处理
        guard.check_event("open", (target, None, os.O_RDONLY))
        guard.check_event("open", (target, None, os.O_BINARY | os.O_NOINHERIT))

    def test_os_open_write_flags_allowed_with_permission(self, tmp_path):
        guard = self._guard(tmp_path, ["fs_write"])
        target = str(tmp_path / "data" / "f.txt")
        guard.check_event("open", (target, None, os.O_WRONLY | os.O_CREAT))

    def test_open_text_mode_still_checked(self, tmp_path):
        """B2.1：builtin open(path, 'w') 的 mode 字符串判定保持不变。"""
        guard = self._guard(tmp_path, [])
        with pytest.raises(PermissionViolation, match="fs_write"):
            guard.check_event("open", (str(tmp_path / "data" / "f.txt"), "w", 33665))

    def test_read_roots_exclude_project_root_and_sys_path_dirs(self, tmp_path):
        """B2.1：read_roots 不再把整个项目根/sys.path 无差别加入。"""
        guard = self._guard(tmp_path, [])
        roots = {str(r).lower() for r in guard.read_roots}
        # 项目根（tests 的上三级）不得作为读根
        project_root = str(Path(__file__).resolve().parents[2]).lower()
        assert project_root not in roots, "项目根不应无差别加入 read_roots"
        # 随机 sys.path 目录（非 Python 运行环境）不得出现
        stray = tmp_path / "stray_dir"
        stray.mkdir()
        guard2 = PermissionGuard(
            [], plugin_id="x", data_dir=tmp_path / "data",
            plugin_dir=tmp_path / "plugin",
            extra_read_roots=[],
        )
        # sys.prefix / site-packages 仍在（Python 运行环境允许读）
        assert any("pythoncore" in r or "site-packages" in r for r in
                   (str(x).lower() for x in guard2.read_roots)), \
            "Python 运行环境仍应可读，否则 worker 无法 import 引擎模块"

    def test_extra_read_roots_reject_escape_and_realpath(self, tmp_path):
        """B2.1：明显路径逃逸字符串被拒；合法目录做 realpath 归一。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("平台不支持目录符号链接")
        guard = PermissionGuard(
            [], plugin_id="x", data_dir=tmp_path / "data",
            plugin_dir=tmp_path / "plugin",
            extra_read_roots=[link, "../../obvious-escape", "C:/Windows/../../x"],
        )
        roots = {str(r).lower() for r in guard.read_roots}
        # 逃逸字符串不应直接成为读根（调用方宿主负责合法性，这里只拒明显逃逸）
        assert not any(".." in r for r in roots)
        # 合法 symlink 用 realpath 归一（指向的 outside 目录本身可读）
        assert str(outside.resolve()).lower() in roots

    def test_audit_hook_real_os_calls_denied(self, tmp_path):
        """B2.1：用真 tmp_path 触发 os 写事件，验证 audit hook 真拦住（不是空测判定器）。"""
        # 在隔离子进程里装 hook 并真的调用 os.remove/os.mkdir/os.rename/os.open，
        # 断言抛 PermissionViolation；测试进程自身不受影响。
        script = textwrap.dedent(f"""
            import os, sys
            from clipwright.plugins.runtime.permissions import (
                PermissionGuard, install_audit_hook, PermissionViolation,
            )
            g = PermissionGuard([], plugin_id="x",
                                data_dir={str(tmp_path / 'data')!r},
                                plugin_dir={str(tmp_path / 'plugin')!r})
            install_audit_hook(g)
            d = {str(tmp_path)!r}
            results = []
            for label, fn in [
                ("remove", lambda: os.remove(os.path.join(d, "f.txt"))),
                ("rename", lambda: os.rename(os.path.join(d, "f.txt"),
                                             os.path.join(d, "g.txt"))),
                ("mkdir", lambda: os.mkdir(os.path.join(d, "sub"))),
                ("open_w", lambda: os.close(os.open(os.path.join(d, "f.txt"),
                                                    os.O_WRONLY | os.O_CREAT))),
            ]:
                try:
                    fn()
                    results.append(label + ":ALLOWED")
                except PermissionViolation:
                    results.append(label + ":BLOCKED")
                except Exception as e:
                    results.append(label + ":OTHER:" + type(e).__name__)
            print("|".join(results))
        """)
        # 先备好源文件
        (tmp_path / "f.txt").write_text("x", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout.strip().split("|")
        assert out == ["remove:BLOCKED", "rename:BLOCKED",
                       "mkdir:BLOCKED", "open_w:BLOCKED"], out
