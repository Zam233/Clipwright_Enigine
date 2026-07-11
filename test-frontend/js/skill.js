/* Skill 模块 */
async function listSkills() {
  const { ok, data } = await api('GET', '/api/skill/list');
  if (!ok) { document.getElementById('skillList').innerHTML = '<div class="tag tag-error">获取失败</div>'; return; }
  let html = `<div class="text-muted" style="margin-bottom:8px;font-size:12px">共 ${data.length} 个技能</div>`;
  for (const s of data) {
    const tag = s.available ? 'tag-success' : 'tag-pending';
    const label = s.available ? '可用' : '依赖缺失';
    html += `<div class="knowledge-item"><span class="name">${s.name}</span><span><span class="tag ${tag}">${label}</span></span></div>
      <div style="font-size:11px;color:var(--text2);padding:2px 12px 8px;border-bottom:1px solid var(--border)">依赖工具: ${(s.required_tools||[]).join(', ')||'无'} | ${s.description}</div>`;
  }
  document.getElementById('skillList').innerHTML = html;
}
async function execSkill() {
  const name = document.getElementById('skillExecName').value.trim();
  let params = {};
  try { params = JSON.parse(document.getElementById('skillExecParams').value || '{}'); } catch {}
  const el = document.getElementById('skillExecResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">执行中...</span></div>';
  const res = await fetch(API_BASE() + `/api/skill/execute?name=${encodeURIComponent(name)}`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({params})
  });
  const data = await res.json();
  el.innerHTML = `<div class="result-box"><div class="result-header"><span>${name}</span><span class="tag ${res.ok?'tag-success':'tag-error'}">${data.status||'done'}</span></div><pre>${JSON.stringify(data, null, 2)}</pre></div>`;
}
