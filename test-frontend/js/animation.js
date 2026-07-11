/* Animation 模块 */
let animData = [];
function switchAnimTab(type, el) {
  document.querySelectorAll('#section-animation .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const filtered = type === 'all' ? animData : animData.filter(a => a.type === type);
  const container = document.getElementById('animListResult');
  if (!filtered.length) { container.innerHTML = '<div class="text-muted">无数据</div>'; return; }
  let html = '';
  for (const a of filtered) {
    const props = Object.entries(a.properties_meta||{}).map(([k,v]) => `${k}:${v.type}`).join(', ');
    html += `<div class="knowledge-item"><span class="name">${a.animation_id}</span><span class="badge">${a.duration_sec}s | ${a.easing}</span></div>
      <div style="font-size:11px;color:var(--text2);padding:2px 12px 8px;border-bottom:1px solid var(--border)">${a.description||a.name||''} ${a.target&&a.target!=='any'?' | target: '+a.target:''} ${a.keyframes?' | keyframes: '+a.keyframes.length:''} ${props?' | props: '+props:''}</div>`;
  }
  container.innerHTML = html;
}
async function listAnimations() {
  const { ok, data } = await api('GET', '/api/animation/list');
  if (!ok) return;
  animData = data || [];
  document.getElementById('animOnscreenCount').textContent = (data||[]).filter(a => a.type === 'onscreen').length;
  document.getElementById('animTextCount').textContent = (data||[]).filter(a => a.type === 'text').length;
  document.getElementById('animTransCount').textContent = (data||[]).filter(a => a.type === 'transition').length;
  switchAnimTab('onscreen', document.querySelector('#section-animation .tab'));
}
async function getAnimation() {
  const id = document.getElementById('animGetId').value.trim();
  const { ok, data } = await api('GET', `/api/animation/get/${id}`);
  document.getElementById('animDetailResult').innerHTML = ok
    ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>`
    : `<div class="tag tag-error">未找到: ${id}</div>`;
}
