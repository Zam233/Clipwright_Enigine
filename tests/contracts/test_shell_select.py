"""shell_select：pwsh 优先，Windows 回退 5.1，POSIX bash。"""

from __future__ import annotations

import clipwright.services.shell_select as ss


def test_pwsh_preferred(monkeypatch) -> None:
    monkeypatch.setattr(ss.os, "name", "nt")
    monkeypatch.setattr(ss, "_which", lambda *names: "pwsh.exe" if "pwsh" in names[0] else "")
    spec = ss.select_shell()
    assert spec.name == "pwsh" and spec.fallback is False


def test_windows_falls_back_to_5_1(monkeypatch) -> None:
    monkeypatch.setattr(ss.os, "name", "nt")
    monkeypatch.setattr(ss, "_which", lambda *names: "powershell.exe" if "powershell" in names[0] else "")
    spec = ss.select_shell()
    assert spec.name == "powershell" and spec.fallback is True


def test_posix_bash(monkeypatch) -> None:
    monkeypatch.setattr(ss.os, "name", "posix")
    monkeypatch.setattr(ss, "_which", lambda *names: "/bin/bash" if names[0] == "bash" else "")
    spec = ss.select_shell()
    assert spec.name == "bash" and spec.prefix[-1] == "-lc"


def test_explicit_profile(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_which", lambda *names: "/bin/bash" if names[0] == "bash" else "")
    spec = ss.select_shell(profile="bash", fallback=False)
    assert spec.name == "bash"


def test_explicit_profile_missing_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_which", lambda *names: "")
    import pytest

    with pytest.raises(ss.ShellUnavailable):
        ss.select_shell(profile="bash", fallback=False)
