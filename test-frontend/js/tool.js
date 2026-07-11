/* Tool 模块 */
async function listTools() {
  const { ok, data } = await api('GET', '/api/tool/list');
  if (!ok) { document.getElementById('toolList').innerHTML = '<div class="tag tag-error">获取失败</div>'; return; }
  const avail = data.filter(t => t.available).length;
  let html = `<div class="text-muted" style="margin-bottom:8px;font-size:12px">共 ${data.length} 个工具，${avail} 个可用</div>`;
  for (const t of data) {
    const tag = t.available ? 'tag-success' : 'tag-pending';
    const label = t.available ? '可用' : '需 ffmpeg';
    const paramsHtml = Object.entries(t.parameters||{}).map(([k,v]) => `<span class="text-muted">${k}: ${v.type}${v.required?' *':''}</span>`).join(' ');
    html += `<div class="knowledge-item"><span class="name">${t.name}</span><span><span class="tag ${tag}">${label}</span></span></div>
      <div style="font-size:11px;color:var(--text2);padding:2px 12px 8px;border-bottom:1px solid var(--border)">${t.description||''} ${paramsHtml ? '| '+paramsHtml : ''}</div>`;
  }
  document.getElementById('toolList').innerHTML = html;
}
async function execTool() {
  const name = document.getElementById('toolExecName').value.trim();
  let params = {};
  try { params = JSON.parse(document.getElementById('toolExecParams').value || '{}'); } catch {}
  const el = document.getElementById('toolExecResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">执行中...</span></div>';
  const res = await fetch(API_BASE() + `/api/tool/execute?name=${encodeURIComponent(name)}`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({params})
  });
  const data = await res.json();
  el.innerHTML = `<div class="result-box"><div class="result-header"><span>${name}</span><span class="tag ${res.ok?'tag-success':'tag-error'}">${data.status||'done'}</span></div><pre>${JSON.stringify(data, null, 2)}</pre></div>`;
}
async function execToolBatch() {
  let calls = [];
  try { calls = JSON.parse(document.getElementById('toolBatchCalls').value); } catch {}
  const el = document.getElementById('toolBatchResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">执行中...</span></div>';
  const { ok, data } = await api('POST', '/api/tool/batch', calls);
  el.innerHTML = `<div class="result-box"><div class="result-header"><span>Batch (${(data||[]).length})</span><span class="tag ${ok?'tag-success':'tag-error'}">done</span></div><pre>${JSON.stringify(data, null, 2)}</pre></div>`;
}
