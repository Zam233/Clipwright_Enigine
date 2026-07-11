/* 素材库模块 — 网格浏览 + 搜索 + 上传 + 识图导入 */
let _matView = 'grid', _matData = [];

function switchMatView(view) {
  _matView = view;
  document.getElementById('matGrid').style.display = view === 'grid' ? 'grid' : 'none';
  document.getElementById('matList').style.display = view === 'list' ? 'block' : 'none';
  document.getElementById('matViewGrid').className = view === 'grid' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm';
  document.getElementById('matViewList').className = view === 'list' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm';
  if (_matData.length) renderMatView();
}

// ── 加载素材源列表 ──
async function listMaterialSources() {
  const el = document.getElementById('matSourceList');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">加载中...</span></div>';
  const { ok, data } = await api('GET', '/api/material/sources');
  if (!ok || !data || !data.length) {
    el.innerHTML = '<div class="text-muted" style="font-size:11px;padding:8px">无注册素材源</div>';
    return;
  }
  el.innerHTML = data.map(s =>
    `<div class="knowledge-item" style="cursor:pointer;margin-bottom:2px;padding:6px 8px" onclick="searchSource('${s.id}')">
      <span class="name" style="font-size:11px">${s.name}</span>
      <span class="badge">${s.id}</span>
    </div>`
  ).join('');
}

// ── 搜索 ──
async function searchMaterial() {
  const q = document.getElementById('matSearchQuery').value.trim();
  if (!q) { await listAllMaterials(); return; }
  const el = document.getElementById('matGrid');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">搜索中...</span></div>';
  const res = await fetch(API_BASE() + `/api/material/search?query=${encodeURIComponent(q)}&top_k=20`, { method: 'POST' });
  const data = await res.json();
  if (!Array.isArray(data)) { el.innerHTML = '<div class="text-muted">无结果</div>'; return; }
  _matData = data.map(r => r.asset || r);
  document.getElementById('matCount').textContent = `(${_matData.length})`;
  renderMatView();
}

async function searchSource(sourceId) {
  document.getElementById('matSearchQuery').value = sourceId;
  await searchMaterial();
}

// ── 列出全部素材（通过资产列表或搜索空串） ──
async function listAllMaterials() {
  const el = document.getElementById('matGrid');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">加载中...</span></div>';
  const { ok, data } = await api('GET', '/api/asset/list');
  if (ok && data && data.length) {
    _matData = data;
    document.getElementById('matCount').textContent = `(${_matData.length})`;
    renderMatView();
  } else {
    el.innerHTML = '<div class="text-muted" style="padding:20px;text-align:center">暂无素材，点击左上角上传</div>';
  }
}

// ── 渲染 ──
function renderMatView() {
  const grid = document.getElementById('matGrid');
  const list = document.getElementById('matList');
  if (_matView === 'grid') {
    grid.innerHTML = _matData.map(a => {
      const card = document.createElement('div');
      card.style.cssText = 'background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;cursor:pointer;transition:border-color 0.15s';
      card.onmouseenter = () => card.style.borderColor = 'var(--accent)';
      card.onmouseleave = () => card.style.borderColor = 'var(--border)';
      card.onclick = () => showMatDetail(a);

      const thumb = a.thumbnail_path
        ? `<img src="${API_BASE()}/api/asset/${a.asset_id}/thumbnail" style="width:100%;height:100px;object-fit:cover;display:block">`
        : `<div style="height:100px;display:flex;align-items:center;justify-content:center;background:var(--surface2);font-size:28px">${typeIcon(a.type||'video')}</div>`;

      const tagHtml = (a.tags||[]).slice(0,3).map(t => `<span style="font-size:9px;background:var(--surface2);padding:1px 5px;border-radius:3px;color:var(--text2)">${t}</span>`).join('');

      card.innerHTML = `${thumb}<div style="padding:8px">
        <div style="font-size:11px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${a.title||a.filename||a.asset_id}</div>
        <div style="font-size:10px;color:var(--text2);margin-top:2px">${a.type}${a.duration_sec?' | '+a.duration_sec+'s':''}</div>
        <div style="margin-top:4px">${tagHtml}</div>
      </div>`;
      return card.outerHTML;
    }).join('');
  } else {
    list.innerHTML = _matData.map(a =>
      `<div class="knowledge-item" style="cursor:pointer" onclick="showMatDetail(${JSON.stringify(a).replace(/"/g,"&quot;")})">
        <span class="name" style="font-size:11px">${a.title||a.filename||a.asset_id}</span>
        <span class="badge">${a.type} ${a.duration_sec?a.duration_sec+'s':''}</span>
      </div>`
    ).join('');
  }
}

function typeIcon(t) {
  return {video:'🎬',audio:'🎵',image:'🖼',text:'📝'}[t] || '📁';
}

// ── 素材详情 ──
function showMatDetail(a) {
  const html = `<div class="result-box"><pre>${JSON.stringify(a, null, 2)}</pre></div>`;
  const grid = document.getElementById('matGrid');
  // Append detail below grid
  const existing = grid.querySelector('.mat-detail');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.className = 'mat-detail';
  div.style.cssText = 'grid-column:1/-1';
  div.innerHTML = html;
  grid.appendChild(div);
}

// ── 上传 ──
async function uploadMaterial(event) {
  const files = event.target.files;
  if (!files.length) return;
  for (const file of files) {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(API_BASE() + '/api/asset/upload', { method: 'POST', body: formData });
      const data = await res.json();
      if (data.asset_id) {
        addMatLog(`上传成功: ${data.filename} (${(data.file_size/1024).toFixed(0)}KB)`);
      }
    } catch(e) {
      addMatLog(`上传失败: ${file.name}`);
    }
  }
  event.target.value = '';
  setTimeout(listAllMaterials, 500);
}

// ── 识图导入 ──
function openVisionImport() {
  const dlg = document.getElementById('visionDialog');
  dlg.style.display = 'flex';
  document.getElementById('visionResult').textContent = '等待识别...';
}
function closeVisionDialog() {
  document.getElementById('visionDialog').style.display = 'none';
}
async function analyzeAndImport() {
  const path = document.getElementById('visionPath').value.trim();
  if (!path) return;
  const el = document.getElementById('visionResult');
  el.textContent = '识别中...';
  const { ok, data } = await api('POST', '/api/vision/analyze', { image_path: path });
  if (!ok) { el.textContent = '识别失败'; return; }
  el.textContent = JSON.stringify(data, null, 2);
  // Auto import
  const imp = await api('POST', '/api/vision/import', { image_path: path });
  if (imp.ok) {
    addMatLog('已导入素材库');
    setTimeout(listAllMaterials, 500);
  }
}

// ── 日志 ──
function addMatLog(msg) {
  const el = document.getElementById('matCount');
  const log = document.createElement('div');
  log.style.cssText = 'font-size:10px;color:var(--text2);padding:1px 0';
  log.textContent = msg;
  el.parentNode.appendChild(log);
  setTimeout(() => log.remove(), 3000);
}

// ── Init ──
listMaterialSources();
setTimeout(listAllMaterials, 1000);
