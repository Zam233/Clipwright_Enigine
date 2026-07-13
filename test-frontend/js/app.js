/* Clipwright Test Frontend — 核心工具函数 */

const API_BASE = () => document.getElementById('apiBase').value.replace(/\/+$/, '');

// ── 动态面板加载缓存 ──
const _sectionCache = new Map();
let _loadedSection = null;

async function switchSection(id) {
  const content = document.getElementById('mainContent');
  if (!content) return;

  // 高亮侧栏导航
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.remove('active'));
  const link = document.querySelector(`.sidebar nav a[onclick*="${id}"]`) ||
                document.querySelector(`.sidebar nav a[onclick*="'${id}'"]`);
  if (link) link.classList.add('active');

  // 如果已加载过且是同一个面板，不做操作
  if (_loadedSection === id && content.querySelector(`#section-${id}`)) {
    return;
  }

  try {
    let html = _sectionCache.get(id);
    if (!html) {
      // 用绝对路径确保在任何 URL 下都能正确加载
      const base = window.location.pathname.replace(/\/index\.html$/, '/').replace(/\/+$/, '');
      const sectionUrl = `${base}/sections/${id}.html`;
      const resp = await fetch(sectionUrl);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      html = await resp.text();
      _sectionCache.set(id, html);
    }

    // 注入面板 HTML
    content.innerHTML = html;

    // 确保当前面板有 active class（CSS 中 .section.active 才可见）
    const sectionEl = content.querySelector(`#section-${id}`);
    if (sectionEl) sectionEl.classList.add('active');

    _loadedSection = id;

    // 面板加载后的初始化钩子
    if (id === 'edit-workspace' && typeof loadEditSources === 'function') {
      loadEditSources();
    }
    if (id === 'material') {
      if (typeof listMaterialSources === 'function') listMaterialSources();
      if (typeof listAllMaterials === 'function') setTimeout(listAllMaterials, 500);
    }
  } catch (e) {
    console.error(`[Clipwright] 加载面板 ${id} 失败:`, e);
    // 尝试显示详细错误到页面
    const errDetail = e.message + (e.stack ? '\n' + e.stack.split('\n').slice(0, 3).join('\n') : '');
    content.innerHTML = `<div class="section active"><div class="card"><div class="card-header" style="color:var(--red)">加载失败</div><div class="card-body"><p class="text-muted">无法加载面板 <strong>${id}</strong></p><pre style="font-size:11px;color:var(--red);background:var(--surface2);padding:8px;border-radius:var(--radius);margin-top:8px;white-space:pre-wrap">${errDetail}</pre><button class="btn btn-secondary btn-sm mt-2" onclick="switchSection('${id}')">重试</button></div></div></div>`;
  }
}

async function api(method, path, body, timeoutMs = 120000) {
  const url = API_BASE() + path;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const opts = { method, headers: { 'Content-Type': 'application/json' }, signal: controller.signal };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    clearTimeout(timer);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }
    return { ok: res.ok, status: res.status, data };
  } catch(ex) {
    clearTimeout(timer);
    if (ex.name === 'AbortError') throw new Error('请求超时 (' + timeoutMs + 'ms)');
    throw ex;
  }
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
    const resultEl = document.getElementById('healthResult');
    if (resultEl) showResult('healthResult', data, ok);
  } catch(e) {
    dot.className = 'status-dot offline';
    text.textContent = '无法连接';
    const resultEl = document.getElementById('healthResult');
    if (resultEl) showResult('healthResult', { error: e.message }, false);
  }
}
