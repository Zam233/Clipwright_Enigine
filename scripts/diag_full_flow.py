"""curl 全流程测试：init → chat(简报) → chat(确认) → 规划书"""
import json
import time
import urllib.request

BASE = "http://localhost:8000"
SCRIPT = "有一个经典名场面，在每年高考前后的那段时间总会上演。文科与理科的对立已经成为对立金字塔中的前几名了。大家好，这里是扎姆。这里我首先站在一个中立的姿态上。" * 3


def post(path: str, body: dict, timeout: int = 60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 1. init
print("[1] init ...")
r = post("/api/requirements/init", {
    "topic": "文理之争",
    "script_text": SCRIPT,
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "audio_duration_sec": 300,
}, timeout=30)
sid = r["session_id"]
print("    session:", sid)

# 2. chat 简报
print("[2] chat 简报 ...")
t0 = time.time()
r = post("/api/requirements/chat", {"session_id": sid, "message": f"我的选题是：文理之争。文稿：{SCRIPT}。预估时长：300秒。请帮我生成创意简报。"}, timeout=300)
print(f"    耗时 {round(time.time()-t0,1)}s, status={r.get('status')}, brief={bool(r.get('creative_brief'))}")
if not r.get("creative_brief"):
    print("    FAIL 简报未生成")
    raise SystemExit(1)

# 3. chat 确认 → 规划书
print("[3] chat 确认 → 规划书 ...")
t0 = time.time()
r = post("/api/requirements/chat", {"session_id": sid, "message": "确认，请生成完整的制作规划书。"}, timeout=300)
elapsed = round(time.time() - t0, 1)
print(f"    耗时 {elapsed}s, status={r.get('status')}, plan={bool(r.get('production_plan'))}")
if r.get("production_plan"):
    p = r["production_plan"]
    print("    场景数:", p.get("scene_count"), "总时长:", p.get("total_duration_sec"))
    print("    markdown 前 200 字:", (p.get("markdown_content") or "")[:200].replace("\n", " "))
else:
    print("    FAIL 规划书未生成")
    raise SystemExit(1)

print("\n=== 全流程成功 ===")
