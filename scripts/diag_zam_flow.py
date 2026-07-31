"""API 复现浏览器场景：Zam + 完整长文稿。"""
import json
import re
import time
import urllib.request

content = open(r"D:\clipweight client\文理\content.md", encoding="utf-8").read()
m = re.split(r"\*\*文案[：:]\*\*", content)[1].strip()
script = m[:10000]


def post(path: str, body: dict, timeout: int):
    req = urllib.request.Request(
        "http://localhost:8000" + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("[1] init (Zam + long script)...")
r = post("/api/requirements/init", {
    "topic": "文理之争",
    "script_text": script,
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "audio_duration_sec": 815,
}, 30)
sid = r["session_id"]
print("    session:", sid)

print("[2] chat 简报...")
t0 = time.time()
r = post("/api/requirements/chat", {
    "session_id": sid,
    "message": f"我的选题是：文理之争。文稿：{script}。预估时长：815秒。请帮我生成创意简报。",
}, 300)
print(f"    耗时 {round(time.time()-t0,1)}s, brief={bool(r.get('creative_brief'))}")

print("[3] chat 确认 → 规划书...")
t0 = time.time()
r = post("/api/requirements/chat", {
    "session_id": sid,
    "message": "确认，请生成完整的制作规划书。",
}, 300)
el = round(time.time() - t0, 1)
print(f"    耗时 {el}s, status={r.get('status')}, plan={bool(r.get('production_plan'))}")
if r.get("production_plan"):
    p = r["production_plan"]
    print("    场景数:", p.get("scene_count"), "总时长:", p.get("total_duration_sec"))
    md = (p.get("markdown_content") or "").replace("\n", " ")
    print("    markdown 前150字:", md[:150])
