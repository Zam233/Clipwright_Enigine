"""Render the 5 agentic MG samples to MP4 via local preview API."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "renders" / "agentic-samples"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8080"


def main() -> int:
    for name in ("01_title", "02_data", "03_compare", "04_flow", "05_quote"):
        mg = json.loads((ROOT / "renders" / "agent-demo" / f"{name}-agentic.json").read_text(encoding="utf-8"))
        params = {k: str((v or {}).get("default", "")) for k, v in (mg.get("params") or {}).items() if isinstance(v, dict)}
        t0 = time.time()
        r = httpx.post(f"{BASE}/api/animation/preview", json={"mg_json": mg, "params": params}, timeout=1800)
        r.raise_for_status()
        data = r.json()
        src = ROOT / str(data.get("path") or "")
        dst = OUT / f"{name}.mp4"
        if src.exists():
            shutil.copy2(src, dst)
        print(json.dumps({"name": name, "ok": dst.exists(), "path": str(dst),
                          "url": data.get("url"), "duration_sec": data.get("duration_sec"),
                          "render_sec": round(time.time() - t0, 1)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
