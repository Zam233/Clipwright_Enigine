/* FontConfig 模块 */
let _fcFonts = [];

async function listFonts() {
  const { ok, data } = await api('GET', '/api/fonts/list');
  const el = document.getElementById('fcFontList');
  if (!ok) { el.innerHTML = '<span class="tag tag-error">查询失败</span>'; return; }
  _fcFonts = data.fonts || [];
  const count = document.getElementById('fcFontCount');
  if (count) count.textContent = `(${_fcFonts.length} 个)`;
  renderFonts(_fcFonts);
}

async function resolveFont() {
  const name = document.getElementById('fcFontName')?.value?.trim() || '';
  const { ok, data } = await api('GET', `/api/fonts/resolve?name=${encodeURIComponent(name)}`);
  const el = document.getElementById('fcResolveResult');
  if (el) el.innerHTML = ok
    ? `<pre style="margin:0;white-space:pre-wrap">${JSON.stringify(data, null, 2)}</pre>`
    : '<span class="tag tag-error">查询失败</span>';
}

async function loadDefaultFont() {
  const { ok, data } = await api('GET', '/api/fonts/default');
  const el = document.getElementById('fcDefaultResult');
  if (el) el.innerHTML = ok
    ? `<pre style="margin:0;white-space:pre-wrap">${JSON.stringify(data, null, 2)}</pre>`
    : '<span class="tag tag-error">查询失败</span>';
}

function renderFonts(fonts) {
  const el = document.getElementById('fcFontList');
  if (!el) return;
  el.innerHTML = fonts.length
    ? fonts.map(f =>
        `<div style="padding:4px 6px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
          <span>${f.name}</span>
          <span style="color:var(--text2);font-size:10px">${f.file}</span>
        </div>`
      ).join('')
    : '<div class="text-muted">无字体</div>';
}

function filterFonts() {
  const q = (document.getElementById('fcSearch')?.value || '').toLowerCase();
  renderFonts(q ? _fcFonts.filter(f => f.name.toLowerCase().includes(q) || f.file.toLowerCase().includes(q)) : _fcFonts);
}

// section 切换时自动加载
if (document.getElementById('section-fontconfig')) {
  listFonts();
  loadDefaultFont();
}
