/* 剪辑工作台模块 */
let _lastTimelineData = null, _tlZoom = 1.0;

function switchEditTab(name, el) {
  document.querySelectorAll('#section-edit-workspace .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('#section-edit-workspace .card-body[id^="edit-"]').forEach(d => d.style.display = 'none');
  document.getElementById('edit-' + name).style.display = 'block';
  if (name === 'timeline' && _lastTimelineData) renderTimeline(_lastTimelineData);
}
function importEditScript(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => { document.getElementById('editScript').value = e.target.result; };
  reader.readAsText(file);
}
async function runEditPipeline() {
  const el = document.getElementById('editPipelineResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">管线运行中...</span></div>';
  const { ok, data } = await api('POST', '/api/pipeline/run', {
    persona_id: document.getElementById('editPersonaId').value,
    category_plugin_id: document.getElementById('editPluginId').value,
    topic: document.getElementById('editScript').value.trim(),
    dry_run: false,
  });
  if (!ok || !data) { el.innerHTML = '<div class="tag tag-error">管线运行失败</div>'; return; }
  _lastTimelineData = data.shared_data?.final_timeline || null;
  let html = '<div class="result-box">';
  html += `<div class="result-header"><span>${data.status} | ${data.pipeline_id}</span></div><pre>`;
  for (const s of (data.steps||[])) {
    const tag = s.status==='completed'?'tag-success':s.status==='failed'?'tag-error':'tag-pending';
    html += `[${s.agent_name}] ${s.status} ${s.duration_ms? s.duration_ms+'ms':''}\n`;
  }
  const tl = _lastTimelineData;
  if (tl) {
    html += `\n时间线: ${tl.width}x${tl.height} @ ${tl.fps}fps | ${tl.duration_sec}s | ${(tl.tracks||[]).length} 轨道\n`;
    for (const t of (tl.tracks||[])) html += `  [${t.kind}] ${t.name}: ${(t.clips||[]).length} clips\n`;
  }
  html += '</pre></div>';
  html += '<div class="btn-group mt-2"><button class="btn btn-primary btn-sm" onclick="switchEditTab(\'timeline\',document.querySelector(\'#section-edit-workspace .tab:nth-child(2)\'));renderTimeline(_lastTimelineData)">查看时间线</button></div>';
  el.innerHTML = html;
}
function renderTimeline(tl) {
  if (!tl) return;
  const container = document.getElementById('timelineInner');
  document.getElementById('tlInfo').textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${tl.duration_sec}s | ${(tl.tracks||[]).length} 轨道`;
  const zoom = _tlZoom, pxPerSec = 80 * zoom, totalWidth = Math.max((tl.duration_sec||60) * pxPerSec, 400);
  const labelWidth = 70;
  let html = `<div style="display:flex;flex-direction:column;min-width:${labelWidth+totalWidth}px;position:relative">`;
  html += `<div class="tl-ruler" style="padding-left:${labelWidth}px;height:28px">`;
  const tickInterval = Math.max(1, Math.floor(10 / zoom));
  for (let t = 0; t <= (tl.duration_sec||60); t += tickInterval) {
    const x = t * pxPerSec;
    html += `<div class="tick-line" style="left:${x}px"></div><div class="tick" style="left:${x}px">${t}s</div>`;
  }
  html += '</div>';
  const colors = { video:'video', audio:'audio', text:'text', caption:'caption', image:'image', shape:'shape', waveform:'waveform' };
  for (const track of (tl.tracks||[])) {
    html += `<div class="tl-track"><div class="tl-track-label">${track.name||track.kind}</div><div class="tl-track-inner" style="min-width:${totalWidth}px">`;
    for (const clip of (track.clips||[])) {
      const x = (clip.start_sec||0) * pxPerSec, w = Math.max((clip.duration_sec||1) * pxPerSec, 8);
      const cls = colors[clip.kind]||'video';
      const label = clip.text||clip.asset_id||clip.id||clip.kind;
      const title = `${label} | ${clip.start_sec}s-${(clip.start_sec+clip.duration_sec).toFixed(1)}s | ${clip.kind}`;
      html += `<div class="tl-clip ${cls}" style="left:${x}px;width:${w}px" title="${title}">`;
      if (w > 30) html += `<span class="tl-label">${label}</span>`;
      html += '</div>';
    }
    html += '</div></div>';
  }
  html += '</div>';
  container.innerHTML = html;
}
function zoomTimeline(delta) {
  _tlZoom = Math.max(0.3, Math.min(5, _tlZoom + delta));
  if (_lastTimelineData) renderTimeline(_lastTimelineData);
}
async function runStt() {
  const path = document.getElementById('sttAudioPath').value.trim();
  if (!path) { document.getElementById('sttResult').innerHTML = '<div class="tag tag-error">请输入音频路径</div>'; return; }
  const el = document.getElementById('sttResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">转录中...</span></div>';
  const res = await fetch(API_BASE() + '/api/stt/transcribe', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({audio_path: path}) });
  const data = await res.json();
  if (!data.success) { el.innerHTML = `<div class="tag tag-error">${data.error||'转录失败'}</div>`; return; }
  let html = `<div class="result-box"><div class="result-header"><span>${data.model} | ${data.language} | ${data.duration_sec}s</span><span class="tag tag-success">${(data.segments||[]).length} 段</span></div><pre>`;
  for (const seg of (data.segments||[])) html += `[${seg.start.toFixed(1)}s-${seg.end.toFixed(1)}s] ${seg.text}\n`;
  el.innerHTML = html + '</pre></div>';
}
async function runSttAlign() {
  const path = document.getElementById('sttAudioPath').value.trim();
  const text = document.getElementById('sttTranscript').value.trim();
  if (!path || !text) { document.getElementById('sttResult').innerHTML = '<div class="tag tag-error">请填写音频路径和文案</div>'; return; }
  const el = document.getElementById('sttResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">对齐中...</span></div>';
  const res = await fetch(API_BASE() + '/api/stt/align', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({audio_path: path, transcript_text: text}) });
  const data = await res.json();
  if (!data.success) { el.innerHTML = `<div class="tag tag-error">${data.error||'对齐失败'}</div>`; return; }
  let html = `<div class="result-box"><div class="result-header"><span>${(data.segments||[]).length} 段对齐</span><span class="tag tag-success">${data.duration_sec}s</span></div><pre>`;
  for (const seg of (data.segments||[])) html += `[${seg.start.toFixed(1)}s-${seg.end.toFixed(1)}s] ${seg.text}\n`;
  el.innerHTML = html + '</pre></div>';
}
/* EDL */
async function importEdl() {
  const content = document.getElementById('edlContent').value;
  const el = document.getElementById('edlResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">解析中...</span></div>';
  const { ok, data } = await api('POST', '/api/edl/import/edl', { content });
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : '<div class="tag tag-error">解析失败</div>';
}
async function importFcpxml() {
  const content = document.getElementById('edlContent').value;
  const el = document.getElementById('edlResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">解析中...</span></div>';
  const { ok, data } = await api('POST', '/api/edl/import/fcpxml', { content });
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : '<div class="tag tag-error">解析失败</div>';
}
/* Proxy */
async function generateProxy() {
  const input_path = document.getElementById('proxyPath').value.trim();
  const proxy_height = parseInt(document.getElementById('proxyHeight').value) || 720;
  const el = document.getElementById('proxyResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">生成中...</span></div>';
  const { ok, data } = await api('POST', '/api/proxy/generate', { input_path, proxy_height });
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : '<div class="tag tag-error">生成失败</div>';
}
async function switchProxy() {
  const el = document.getElementById('proxyResult');
  if (!_lastTimelineData) { el.innerHTML = '<div class="tag tag-error">请先运行管线</div>'; return; }
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">切换中...</span></div>';
  const { ok, data } = await api('POST', '/api/proxy/switch', { timeline: _lastTimelineData });
  el.innerHTML = ok ? `<div class="result-box"><pre>${JSON.stringify(data, null, 2)}</pre></div>` : '<div class="tag tag-error">切换失败</div>';
}
