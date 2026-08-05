/* 模板管理 */

async function tmplLoadList() {
  const el = document.getElementById('tmplList');
  if (!el) return;
  try {
    const r = await api('GET', '/api/template/list');
    const list = r.data || [];
    if (!list.length) { el.innerHTML = '<div style="font-size:11px;color:var(--text2);padding:20px;text-align:center">暂无模板</div>'; return; }
    el.innerHTML = list.map(t =>
      `<div style="padding:6px 8px;cursor:pointer;border-bottom:1px solid var(--border)" onclick="tmplEdit('${t.template_id}')">
        <div style="font-size:12px">${t.name}</div>
        <div style="font-size:9px;color:var(--text2)">${t.template_id} · ${t.persona_id||'-'}</div>
      </div>`
    ).join('');
  } catch(_) { el.innerHTML = '<div style="color:var(--red);padding:10px">加载失败</div>'; }
}

function tmplNew() {
  document.getElementById('tmplEditId').value = '';
  document.getElementById('tmplId').value = '';
  document.getElementById('tmplName').value = '';
  document.getElementById('tmplTopic').value = '';
  document.getElementById('tmplScript').value = '';
  document.getElementById('tmplEditorTitle').textContent = '新建模板';
}

async function tmplEdit(id) {
  try {
    const r = await api('GET', '/api/template/get/' + id);
    const t = r.data;
    if (!t) return;
    document.getElementById('tmplEditId').value = t.template_id;
    document.getElementById('tmplId').value = t.template_id;
    document.getElementById('tmplName').value = t.name || '';
    document.getElementById('tmplTopic').value = t.topic_template || '';
    document.getElementById('tmplScript').value = t.script_template || '';
    document.getElementById('tmplEditorTitle').textContent = '编辑: ' + (t.name || t.template_id);
  } catch(_) { alert('加载失败'); }
}

async function tmplSave() {
  const id = document.getElementById('tmplId').value.trim();
  if (!id) { alert('请输入模板 ID'); return; }
  const editId = document.getElementById('tmplEditId').value;
  const data = {
    template_id: id,
    name: document.getElementById('tmplName').value.trim() || id,
    topic_template: document.getElementById('tmplTopic').value.trim(),
    script_template: document.getElementById('tmplScript').value.trim(),
    persona_id: document.getElementById('tmplPersona')?.value || '',
    category_plugin_id: document.getElementById('tmplPlugin')?.value || 'knowledge_longform',
    resolution: document.getElementById('tmplRes')?.value || '1920x1080',
  };
  try {
    const r = editId ? await api('PUT', '/api/template/update/' + editId, data) : await api('POST', '/api/template/create', data);
    if (r.ok) { tmplLoadList(); document.getElementById('tmplEditId').value = id; }
    else alert('保存失败: ' + JSON.stringify(r.data));
  } catch(ex) { alert('保存异常: ' + ex.message); }
}

async function tmplDelete() {
  const id = document.getElementById('tmplEditId').value;
  if (!id || !confirm('删除模板 ' + id + '?')) return;
  try { await api('DELETE', '/api/template/delete/' + id); tmplNew(); tmplLoadList(); }
  catch(_) { alert('删除失败'); }
}

async function tmplRender() {
  const id = document.getElementById('tmplEditId').value;
  if (!id) { document.getElementById('tmplRenderResult').textContent = '请先选择模板'; return; }
  try {
    const r = await api('POST', '/api/template/render/' + id, {topic: '测试主题', script: '测试文稿内容...'});
    document.getElementById('tmplRenderResult').textContent = '渲染成功: ' + JSON.stringify(r.data?.rendered?.topic || '').slice(0, 60);
  } catch(_) { document.getElementById('tmplRenderResult').textContent = '渲染失败'; }
}

// 片头/片尾
async function ioLoadList() {
  const el = document.getElementById('introOutroList');
  if (!el) return;
  try {
    const r = await api('GET', '/api/template/intro-outro/list');
    const list = r.data || [];
    if (!list.length) { el.innerHTML = '<div style="color:var(--text2);padding:8px">无片头/片尾</div>'; return; }
    el.innerHTML = list.map(i => `<div style="padding:4px 0;font-size:10px">${i.name} (${i.kind}, ${i.duration_sec}s)</div>`).join('');
  } catch(_) { el.innerHTML = '<div style="color:var(--text2);padding:8px">加载失败</div>'; }
}
