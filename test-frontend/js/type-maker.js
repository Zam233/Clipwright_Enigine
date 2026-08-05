/* 类型制作器 — 创建/编辑用户自定义视频类型 */

let _tmTypes = [];

async function loadTypeList() {
  const list = document.getElementById('tmTypeList');
  if (!list) return;
  try {
    const r = await api('GET', '/api/type-maker/list');
    _tmTypes = r.data || [];
    if (!_tmTypes.length) {
      list.innerHTML = '<div class="text-muted" style="font-size:11px;text-align:center;padding:20px">暂无自定义类型<br><span style="font-size:10px">点击 "+ 新建" 创建</span></div>';
      return;
    }
    list.innerHTML = _tmTypes.map(t =>
      `<div style="padding:6px 8px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center"
            onclick="editType('${t.plugin_id}')">
        <div>
          <div style="font-size:12px;color:var(--text)">${t.display_name}</div>
          <div style="font-size:9px;color:var(--text2)">${t.plugin_id}</div>
        </div>
        <span style="font-size:9px;color:var(--text2)">${t.cut_profile}</span>
      </div>`
    ).join('');
  } catch(ex) {
    list.innerHTML = `<div style="font-size:11px;color:var(--red);text-align:center;padding:20px">加载失败: ${ex.message}</div>`;
  }
}

function newTypeForm() {
  document.getElementById('tmEditId').value = '';
  document.getElementById('tmId').value = '';
  document.getElementById('tmName').value = '';
  document.getElementById('tmDesc').value = '';
  document.getElementById('tmCutProfile').value = 'even_flow';
  document.getElementById('tmAnimDensity').value = 'medium';
  document.getElementById('tmShotLow').value = '{"base_shot_ms":8000,"min_shot_ms":2000,"max_shot_ms":15000}';
  document.getElementById('tmShotMid').value = '{"base_shot_ms":5000,"min_shot_ms":1500,"max_shot_ms":10000}';
  document.getElementById('tmShotHigh').value = '{"base_shot_ms":3000,"min_shot_ms":800,"max_shot_ms":8000}';
  document.getElementById('tmTransWeights').value = '{"hard_cut":0.6,"dissolve":0.2,"fade":0.1,"crossfade":0.1}';
  document.getElementById('tmAnnotations').value = '';
  document.getElementById('tmEditorTitle').textContent = '新建类型';
}

async function editType(pluginId) {
  try {
    const r = await api('GET', '/api/type-maker/get/' + pluginId);
    const t = r.data;
    if (!t) return;
    document.getElementById('tmEditId').value = t.plugin_id;
    document.getElementById('tmId').value = t.plugin_id;
    document.getElementById('tmName').value = t.display_name || '';
    document.getElementById('tmDesc').value = t.description || '';
    document.getElementById('tmCutProfile').value = t.cut_profile || 'even_flow';
    document.getElementById('tmAnimDensity').value = t.animation_density || 'medium';
    try {
      const sp = t.shot_params || {};
      document.getElementById('tmShotLow').value = JSON.stringify(sp.low || {});
      document.getElementById('tmShotMid').value = JSON.stringify(sp.medium || {});
      document.getElementById('tmShotHigh').value = JSON.stringify(sp.high || {});
    } catch(_) {}
    document.getElementById('tmTransWeights').value = JSON.stringify(t.transition_weights || {});
    document.getElementById('tmAnnotations').value = (t.annotation_templates || []).join('\n');
    document.getElementById('tmEditorTitle').textContent = `编辑: ${t.display_name}`;
  } catch(ex) {
    alert('加载失败: ' + ex.message);
  }
}

async function saveType() {
  const pluginId = document.getElementById('tmId').value.trim();
  if (!pluginId) { alert('请输入类型 ID'); return; }

  let shotParams;
  try {
    shotParams = {
      low: JSON.parse(document.getElementById('tmShotLow').value || '{}'),
      medium: JSON.parse(document.getElementById('tmShotMid').value || '{}'),
      high: JSON.parse(document.getElementById('tmShotHigh').value || '{}'),
    };
  } catch(_) { alert('镜头参数 JSON 格式错误'); return; }

  let transWeights;
  try {
    transWeights = JSON.parse(document.getElementById('tmTransWeights').value || '{}');
  } catch(_) { alert('转场权重 JSON 格式错误'); return; }

  const config = {
    plugin_id: pluginId,
    display_name: document.getElementById('tmName').value.trim() || pluginId,
    description: document.getElementById('tmDesc').value.trim(),
    cut_profile: document.getElementById('tmCutProfile').value,
    animation_density: document.getElementById('tmAnimDensity').value,
    shot_params: shotParams,
    transition_weights: transWeights,
    annotation_templates: document.getElementById('tmAnnotations').value.split('\n').map(s => s.trim()).filter(s => s),
    audio_bgm_slots: {},
  };

  const editId = document.getElementById('tmEditId').value;

  try {
    let res;
    if (editId) {
      res = await api('PUT', '/api/type-maker/update/' + editId, config);
    } else {
      res = await api('POST', '/api/type-maker/create', config);
    }
    if (res.ok) {
      document.getElementById('tmEditId').value = pluginId;
      document.getElementById('tmEditorTitle').textContent = `编辑: ${config.display_name}`;
      loadTypeList();
    } else {
      alert('保存失败: ' + (res.data?.detail || 'unknown'));
    }
  } catch(ex) {
    alert('保存异常: ' + ex.message);
  }
}

async function deleteType() {
  const pluginId = document.getElementById('tmEditId').value;
  if (!pluginId) { alert('请先选择要删除的类型'); return; }
  if (!confirm(`确定删除类型 "${pluginId}"？`)) return;
  try {
    const r = await api('DELETE', '/api/type-maker/delete/' + pluginId);
    if (r.ok) {
      newTypeForm();
      loadTypeList();
    } else {
      alert('删除失败');
    }
  } catch(ex) {
    alert('删除异常: ' + ex.message);
  }
}
