"""P9 单测：协议编解码 + 权限判定器（不依赖子进程）。"""
from __future__ import annotations

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

    def test_error_from_dict_tolerates_shapes(self):
        assert RpcError.from_dict({"code": "X", "message": "m"}).code == "X"
        assert RpcError.from_dict("boom").code == "PLUGIN_ERROR"


class TestPermissionGuard:
    def _guard(self, tmp_path, perms):
        return PermissionGuard(perms, plugin_id="plug_x", data_dir=tmp_path / "data")

    def test_read_only_open_allowed(self, tmp_path):
        guard = self._guard(tmp_path, [])
        guard.check_event("open", ("/etc/hosts", "r"))  # 不抛

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
