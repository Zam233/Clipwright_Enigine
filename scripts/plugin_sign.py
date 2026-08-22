#!/usr/bin/env python
"""插件 manifest 签名/校验 CLI（Phase 4.4）。

复用在主项目插件系统中与运行时**完全一致**的解析与签名函数
（``clipwright.plugins.loader`` 的 ``_parse_manifest`` / ``sign_manifest`` /
``verify_manifest_signature``），保证 CLI 签出的插件能被运行时正确验证。

用法：
    python scripts/plugin_sign.py sign <plugin_dir> --key=<hex|utf8> [--write]
    python scripts/plugin_sign.py verify <plugin_dir> --key=<hex|utf8>
    python scripts/plugin_sign.py genkey > plugin_signing_key.txt

- ``verify`` 只打印校验结果，不改文件。
- ``sign`` 默认打印待写入的 signature（不落盘）；加 ``--write`` 才写回 plugin.yaml。
- key 可用任意字符串；建议用 ``genkey`` 生成 32 字节十六进制随机串并存入
  环境变量 CLIPWRIGHT_PLUGIN_SIGNATURE_KEY。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def _get_loader(plugin_root: Path, signature_key: str):
    """构建运行时 PluginLoader（key 注入 settings，与 verify_manifest_signature 同源）。"""
    from clipwright.config import settings
    from clipwright.plugins.loader import PluginLoader, sign_manifest, verify_manifest_signature

    settings.plugin_signature_key = signature_key
    loader = PluginLoader(plugin_dir=plugin_root)
    return loader, sign_manifest, verify_manifest_signature


def sign(plugin_dir: Path, key: bytes, write: bool) -> int:
    key_str = key.decode("utf-8", errors="replace")
    loader, sign_fn, _verify = _get_loader(plugin_dir.parent, key_str)
    manifest = loader._parse_manifest(plugin_dir.name, plugin_dir)
    sig = sign_fn(manifest, key_str)
    if write:
        _write_signature(plugin_dir, sig)
    else:
        print(sig)
    return 0


def verify(plugin_dir: Path, key: bytes) -> int:
    key_str = key.decode("utf-8", errors="replace")
    loader, _sign, verify_fn = _get_loader(plugin_dir.parent, key_str)
    manifest = loader._parse_manifest(plugin_dir.name, plugin_dir)
    ok = verify_fn(manifest)
    print(f"[verify] {'OK' if ok else 'FAIL'}  (plugin={plugin_dir.name})")
    return 0 if ok else 1


def _write_signature(plugin_dir: Path, sig: str) -> None:
    f = plugin_dir / "plugin.yaml"
    if not f.exists():
        f = plugin_dir / "manifest.yaml"
    if not f.exists():
        raise FileNotFoundError("未找到可写的 plugin.yaml/manifest.yaml")
    raw = f.read_text(encoding="utf-8")
    lines = raw.splitlines()
    out = []
    replaced = False
    for ln in lines:
        if ln.strip().startswith("signature:"):
            out.append(f"signature: {sig}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"signature: {sig}")
    f.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[sign] 已写回 {f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="ClipWright 插件 manifest 签名/校验 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_sign = sub.add_parser("sign", help="对插件 manifest 计算签名")
    p_sign.add_argument("plugin_dir")
    p_sign.add_argument("--key", required=True, help="签名密钥（任意字符串，建议 32 字节十六进制）")
    p_sign.add_argument("--write", action="store_true", help="把 signature 写回 plugin.yaml")
    p_verify = sub.add_parser("verify", help="校验插件签名")
    p_verify.add_argument("plugin_dir")
    p_verify.add_argument("--key", required=True)
    sub.add_parser("genkey", help="生成签名密钥（32 字节十六进制）")
    args = ap.parse_args()

    if args.cmd == "genkey":
        print(hashlib.sha256(__import__("os").urandom(32)).hexdigest())
        return 0
    key = args.key.encode("utf-8")
    d = Path(args.plugin_dir).resolve()
    if not d.is_dir():
        print(f"插件目录不存在: {d}", file=sys.stderr)
        return 2
    return sign(d, key, bool(getattr(args, "write", False))) if args.cmd == "sign" \
        else verify(d, key)


if __name__ == "__main__":
    raise SystemExit(main())
