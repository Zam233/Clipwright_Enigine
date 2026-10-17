"""给 .ps1 补 UTF-8 BOM，并做语法检查。

为什么需要它：本机一键启动用的是 **Windows PowerShell 5.1**（不是 pwsh 7）。
5.1 读 .ps1 **只看 BOM** 判断编码——没有 BOM 就按系统 ANSI 代码页（中文 Windows = cp936）
解码。脚本里全是中文提示，于是一个括号/引号被解成乱码，直接 **ParseException**，
连"哪一行错了"都读不出来。实测踩过一次：用编辑器重写 start.ps1 / check_env.ps1
（编辑器默认不带 BOM）→ 两个脚本全废。

用法：
    python -X utf8 scripts\\fix_ps1_bom.py          # 检查并修复
    python -X utf8 scripts\\fix_ps1_bom.py --check  # 只检查，有问题则退出码 1（可进门禁）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"
ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = ROOT / "scripts"


def parse_errors(path: Path) -> str:
    """用 PowerShell 自己的解析器检查语法（不执行脚本）。"""
    cmd = (
        "$errs=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$errs)"
        "|Out-Null;"
        "if($errs){$errs|ForEach-Object{$_.Message}}"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def main() -> int:
    check_only = "--check" in sys.argv
    files = sorted(TARGET_DIR.glob("*.ps1"))
    if not files:
        print("没有找到 .ps1 文件")
        return 1

    problems = 0
    for f in files:
        raw = f.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            print(f"[FAIL] {f.name}: 不是合法 UTF-8 — {e}")
            problems += 1
            continue

        has_bom = raw[:3] == BOM
        if not has_bom:
            if check_only:
                print(f"[FAIL] {f.name}: 缺 UTF-8 BOM（PowerShell 5.1 会按 cp936 解码 → 中文乱码 / ParseException）")
                problems += 1
                continue
            f.write_bytes(BOM + raw)
            print(f"[FIX ] {f.name}: 已补 UTF-8 BOM")

        err = parse_errors(f)
        if err:
            print(f"[FAIL] {f.name}: PowerShell 解析错误 →\n{err}")
            problems += 1
        else:
            print(f"[OK]   {f.name}: BOM 就位，语法解析通过")

    if problems:
        print(f"\n{problems} 个脚本有问题")
        return 1
    print(f"\n{len(files)} 个脚本全部就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
