/* Clipwright Test Frontend — 核心工具函数 */
const API_BASE = () => document.getElementById('apiBase').value.replace(/\/+$/, '');

function switchSection(id) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  const target = document.getElementById('section-' + id);
  if (target) target.classList.add('active');
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.remove('active'));
  const link = document.querySelector(`.sidebar nav a[onclick*="${id}"]`);
  if (link) link.classList.add('active');
}

async function api(method, path, body) {
  const url = API_BASE() + path;
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { ok: res.ok, status: res.status, data };
}

function showResult(elId, data, ok) {
  const el = document.getElementById(elId);
  if (!el) return;
  const cls = ok ? 'tag-success' : 'tag-error';
  const label = ok ? 'Success' : 'Error';
  el.innerHTML = `
    <div class="result-box">
      <div class="result-header"><span>Response</span><span class="tag ${cls}">${label}</span></div>
      <pre>${typeof data === 'string' ? data : JSON.stringify(data, null, 2)}</pre>
    </div>`;
}

/* ── Health ── */
async function checkHealth() {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  dot.className = 'status-dot';
  text.textContent = '检查中...';
  try {
    const { ok, data } = await api('GET', '/health');
    if (ok) { dot.className = 'status-dot online'; text.textContent = `已连接 · ${data.service || 'ok'}`; }
    else { dot.className = 'status-dot offline'; text.textContent = '连接失败'; }
    showResult('healthResult', data, ok);
  } catch(e) {
    dot.className = 'status-dot offline';
    text.textContent = '无法连接';
    showResult('healthResult', { error: e.message }, false);
  }
}
