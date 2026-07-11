/* Plugin 模块 */
async function listPlugins() {
  const { ok, data } = await api('GET', '/api/plugin/list');
  if (!ok) { document.getElementById('pluginList').innerHTML = '<div class="tag tag-error">获取失败</div>'; return; }
  let html = `<div class="text-muted" style="font-size:12px;margin-bottom:8px">已加载 ${data.length} 个插件</div>`;
  for (const p of data) {
    const m = p.manifest || {};
    html += `<div class="knowledge-item"><span class="name">${m.id}</span><span class="badge">v${m.version} | ${m.kind}</span></div>
      <div style="font-size:11px;color:var(--text2);padding:2px 12px 8px;border-bottom:1px solid var(--border)">${m.description} | ${m.author}</div>`;
  }
  document.getElementById('pluginList').innerHTML = html;
}
async function discoverPlugins() {
  const { ok, data } = await api('GET', '/api/plugin/discover');
  document.getElementById('pluginList').innerHTML = ok
    ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>`
    : '<div class="tag tag-error">发现失败</div>';
}
async function loadAllPlugins() {
  const el = document.getElementById('pluginList');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">加载中...</span></div>';
  const { ok, data } = await api('POST', '/api/plugin/load-all');
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : '<div class="tag tag-error">加载失败</div>';
}
async function loadPlugin() {
  const id = document.getElementById('pluginLoadId').value.trim();
  const el = document.getElementById('pluginActionResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">加载中...</span></div>';
  const { ok, data } = await api('POST', `/api/plugin/load/${id}`);
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : `<div class="tag tag-error">${data?.detail||'加载失败'}</div>`;
}
async function unloadPlugin() {
  const id = document.getElementById('pluginLoadId').value.trim();
  const { ok, data } = await api('POST', `/api/plugin/unload/${id}`);
  document.getElementById('pluginActionResult').innerHTML = ok
    ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>`
    : '<div class="tag tag-error">卸载失败</div>';
}
async function getCapabilities() {
  const { ok, data } = await api('GET', '/api/plugin/capabilities');
  if (!ok) return;
  const animRes = await api('GET', '/api/animation/list');
  const summary = {
    tools: `${data.tools?.length} (${data.tools?.filter(t=>t.available).length} available)`,
    skills: data.skills?.length,
    material_sources: data.material_sources?.length,
    animations: animRes.ok ? animRes.data.length : '?',
    plugins: data.plugins?.length,
  };
  document.getElementById('pluginList').innerHTML = `<div class="result-box"><pre>${JSON.stringify(summary, null, 2)}</pre></div>`;
}
