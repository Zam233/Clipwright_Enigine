"""ChatForge v1 会话迁移：补 revision、附件移出 JSON、dry-run/backup/report。

用法：
  py -3.14 scripts/migrate_chatforge_sessions.py --dry-run --report reports/migrate.json
  py -3.14 scripts/migrate_chatforge_sessions.py --backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from clipwright.services.chat_forge import ChatForge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--sessions-dir", default=str(ChatForge.SESSIONS_DIR))
    args = ap.parse_args()

    root = Path(args.sessions_dir)
    files = sorted(root.glob("*.json")) if root.exists() else []
    forge = ChatForge()
    report = {"scanned": len(files), "migrated": 0, "failed": [], "attachments": 0}

    backup_dir = root / "_backup" / time.strftime("%Y%m%d-%H%M%S")
    if args.backup and files and not args.dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, backup_dir / "sessions", dirs_exist_ok=True)

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = str(data.get("session_id") or path.stem)
            changed = False
            data.setdefault("revision", 0)
            for msg in data.get("messages", []):
                atts = msg.get("attachments") or []
                if any(isinstance(a, dict) and a.get("data_url") for a in atts):
                    msg["attachments"] = forge._materialize_attachments(sid, atts)
                    report["attachments"] += len(msg["attachments"])
                    changed = True
            if changed:
                data["schema_version"] = 2
            if not args.dry_run:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(path)
            report["migrated"] += 1
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"file": path.name, "reason": str(exc)[:300]})

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
