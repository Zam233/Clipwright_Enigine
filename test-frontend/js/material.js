/* Material 模块 */
async function searchMaterial() {
  const q = document.getElementById('matSearchQuery').value.trim();
  const topK = parseInt(document.getElementById('matSearchTopK').value) || 5;
  const el = document.getElementById('materialResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">搜索中...</span></div>';
  const res = await fetch(API_BASE() + `/api/material/search?query=${encodeURIComponent(q)}&top_k=${topK}`, { method: 'POST' });
  const data = await res.json();
  if (!Array.isArray(data)) { el.innerHTML = '<div class="tag tag-error">搜索失败</div>'; return; }
  let html = `<div class="text-muted" style="font-size:12px;margin-bottom:8px">找到 ${data.length} 个结果</div>`;
  for (const r of data) {
    const a = r.asset || {};
    html += `<div class="knowledge-item"><span class="name">${a.title||a.id}</span><span><span class="tag ${r.score>0.5?'tag-success':'tag-pending'}">${r.score.toFixed(2)}</span></span></div>
      <div style="font-size:11px;color:var(--text2);padding:2px 12px 8px;border-bottom:1px solid var(--border)">${a.type} | ${a.url?'URL: '+(a.url||'').slice(0,50):''}${a.local_path?' PATH: '+a.local_path:''} ${a.tags?' | tags: '+(a.tags||[]).join(', '):''} ${a.duration_sec?' | '+a.duration_sec+'s':''}</div>`;
  }
  el.innerHTML = html;
}
async function listMaterialSources() {
  const { ok, data } = await api('GET', '/api/material/sources');
  document.getElementById('materialResult').innerHTML = ok
    ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>`
    : '<div class="tag tag-error">获取失败</div>';
}
