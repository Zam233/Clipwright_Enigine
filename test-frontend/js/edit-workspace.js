/* 剪辑工作台 — AI 剪辑一体化工作流 */
let _lastTimelineData = null, _tlZoom = 1.0;

// ── 日志 ──
function ewLog(msg, type) {
  const panel = document.getElementById('ewLogPanel');
  const welcome = panel.querySelector('.text-muted');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.style.cssText = 'padding:2px 0;line-height:1.5';
  const colors = {error:'var(--red)', success:'var(--green)', step:'var(--accent)', muted:'var(--text2)', title:'var(--accent)'};
  div.style.color = colors[type] || 'var(--text)';
  div.style.fontSize = type === 'title' ? '12px' : '11px';
  div.style.fontWeight = type === 'title' ? '600' : '400';
  div.textContent = msg;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
}
function clearEwLog() {
  document.getElementById('ewLogPanel').innerHTML =
    '<div class="text-muted" style="text-align:center;padding:30px 0;font-size:11px;color:var(--text2)">等待操作...</div>';
}
function setEwProgress(pct, text) {
  const bar = document.getElementById('ewProgressBar');
  const label = document.getElementById('ewProgressText');
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = text;
  document.getElementById('ewProgress').style.display = 'block';
}

async function loadEditSources() {
  const el = document.getElementById('ewSourceList');
  const { ok, data } = await api('GET', '/api/material/sources');
  if (!ok || !data || !data.length) {
    el.innerHTML = '<span class="text-muted" style="font-size:10px">无素材源</span>'; return;
  }
  el.innerHTML = data.map(s =>
    `<label style="display:inline-flex;align-items:center;gap:2px;padding:2px 6px;background:var(--surface2);border:1px solid var(--border);border-radius:3px;cursor:pointer;font-size:10px">
      <input type="checkbox" checked data-source-id="${s.id}" style="accent-color:var(--accent);width:10px;height:10px;margin:0"> ${s.name}
    </label>`
  ).join('');
}
function getSelectedSources() {
  return Array.from(document.querySelectorAll('#ewSourceList input:checked')).map(c => c.getAttribute('data-source-id'));
}
function importEditScript(e) {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader(); r.onload = ev => document.getElementById('ewScript').value = ev.target.result; r.readAsText(f);
}
async function uploadAudio(e) {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try {
    const r = await fetch(API_BASE() + '/api/asset/upload', { method:'POST', body:fd });
    const d = await r.json();
    if (d.file_path) { document.getElementById('ewAudioPath').value = d.file_path; ewLog('配音上传成功', 'success'); }
  } catch(ex) { ewLog('上传失败', 'error'); }
}

// ── 主流程：异步 + 实时时间线 ──
async function runAiEdit() {
  const btn = document.getElementById('ewRunBtn');
  btn.disabled = true; btn.textContent = '运行中...';
  clearEwLog();
  const startedAt = Date.now();
  const topic = document.getElementById('ewTopic').value.trim();
  const script = document.getElementById('ewScript').value.trim();
  const personaId = document.getElementById('ewPersonaId').value.trim();
  const pluginId = document.getElementById('ewPluginId').value;
  const audioPath = document.getElementById('ewAudioPath').value.trim();
  const selectedSources = getSelectedSources();

  ewLog('=== AI 剪辑工作流 ===', 'title');
  ewLog(`选题: ${topic}`, '');
  ewLog(`人格: ${personaId} | ${pluginId}`, 'muted');
  if (audioPath) ewLog(`配音: ${audioPath.split('/').pop()}`, 'muted');

  try {
    // Step 1: STT
    setEwProgress(10, '对齐配音…');
    ewLog('[1/4] 对齐配音…', 'step');
    let sttSegments = [], audioDuration = 0;
    if (audioPath && script) {
      const r = await fetch(API_BASE() + '/api/stt/align', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({audio_path:audioPath, transcript_text:script}) });
      const d = await r.json();
      if (d.success) { sttSegments = d.segments || []; audioDuration = sttSegments.length ? sttSegments[sttSegments.length-1].end : 0; ewLog(`${sttSegments.length} 段对齐`, 'success'); }
    }

    // Step 2: 异步启动管线
    setEwProgress(20, '启动管线…');
    ewLog('[2/4] 启动 AI 管线…', 'step');
    const startRes = await fetch(API_BASE() + '/api/pipeline/run-async', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      persona_id: personaId, category_plugin_id: pluginId, topic,
      extra_params: { material_source_ids: selectedSources, audio_duration_sec: audioDuration }, dry_run: false
    })});
    const { pipeline_id } = await startRes.json();
    ewLog(`管线 ID: ${pipeline_id}`, 'muted');

    // Step 3: SSE 流实时追踪
    setEwProgress(30, '管线运行中…');
    ewLog('[3/4] 实时追踪中…', 'step');
    const evtSource = new EventSource(API_BASE() + '/api/pipeline/trace/stream/' + pipeline_id);
    let pipelineDone = false;

    await new Promise((resolve, reject) => {
      evtSource.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data);
          const iconMap = {llm:'🤖', tool:'🔧', skill:'🧠', plugin:'🔌', agent_start:'▶', agent_end:'✓', error:'✗', info:'○', timeline_snapshot:'📊'};
          const icon = iconMap[event.type] || '·';

          // 时间线快照 → 实时渲染
          if (event.type === 'timeline_snapshot' && event.detail) {
            _lastTimelineData = event.detail;
            renderTimelineWithCaptions(event.detail, sttSegments);
            ewLog(`📊 时间线更新: ${event.agent}`, 'muted');
          }
          // Agent 完成 → 更新进度
          else if (event.type === 'agent_end') {
            ewLog(`  ✓ ${event.agent}`, 'success');
          }
          // 错误
          else if (event.type === 'error') {
            ewLog(`  ✗ ${event.summary}`, 'error');
          }
          // 工具/LLM 调用
          else if (event.type === 'tool' || event.type === 'llm') {
            ewLog(`  ${icon} ${event.summary}`, 'muted');
          }
          // 管线完成（done 事件或 quality agent 完成）
          if (event.type === 'done' || (event.type === 'agent_end' && event.agent === 'quality')) {
            pipelineDone = true; evtSource.close(); resolve();
          }
          // 系统错误
          else if (event.type === 'error' && event.agent === 'system') {
            evtSource.close(); reject(new Error(event.summary));
          }
        } catch(ex) {}
      };
      evtSource.onerror = () => {
        pipelineDone = true; evtSource.close(); setTimeout(resolve, 300);
      };
      setTimeout(() => { if (!pipelineDone) { evtSource.close(); resolve(); } }, 120000);
    });

    if (!_lastTimelineData) {
      ewLog('管线未生成时间线', 'error');
      // 拉取所有 trace 事件来显示错误详情
      try {
        const r2 = await fetch(API_BASE() + '/api/pipeline/trace/' + pipeline_id + '?' + Date.now());
        const all = await r2.json();
        if (Array.isArray(all)) for (const ev of all) {
          if (ev.type === 'error' && ev.summary) ewLog(`  ${ev.summary}`, 'error');
        }
      } catch(ex) {}

      btn.disabled = false; btn.textContent = '生成视频';
      return;
    }
    ewLog('管线完成', 'success');

    // Step 4: 渲染
    setEwProgress(70, '渲染 MP4…');
    ewLog('[4/4] 渲染视频…', 'step');
    try {
      const r = await fetch(API_BASE() + '/api/render/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({timeline:_lastTimelineData, output_path:'renders/ai_edit_output.mp4'}) });
      const rd = await r.json();
      if (rd?.success) {
        ewLog(`渲染完成: ${(rd.duration_sec||0).toFixed(1)}s`, 'success');
        const video = document.getElementById('ewPreviewVideo');
        const src = document.getElementById('ewPreviewSrc');
        const panel = document.getElementById('ewPreviewPanel');
        if (video && src) {
          src.src = API_BASE() + '/renders/ai_edit_output.mp4';
          video.load(); panel.style.display = 'block';
        }
      } else {
        ewLog(`渲染: ${rd?.error || '无输出'}`, 'muted');
      }
    } catch(ex) { ewLog(`渲染失败: ${ex.message}`, 'error'); }

    setEwProgress(100, `完成 (${((Date.now()-startedAt)/1000).toFixed(1)}s)`);
  } catch(e) {
    ewLog(`错误: ${e.message}`, 'error');
  }
  btn.disabled = false; btn.textContent = '生成视频';
}

// ── Timeline Renderer ──
function renderTimelineWithCaptions(tl, sttSegments) {
  if (!tl) return;
  const container = document.getElementById('timelineInner');
  const info = document.getElementById('tlInfo');
  const dur = tl.duration_sec || 60;
  info.textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${dur.toFixed(1)}s`;
  const zoom = _tlZoom, pxPerSec = 100 * zoom, totalWidth = Math.max(dur * pxPerSec, 400);
  const trackH = 48, rulerH = 26, labelW = 70;
  let html = `<div style="display:flex;flex-direction:column;min-width:${labelW+totalWidth+10}px;user-select:none">`;
  html += `<div style="display:flex;height:${rulerH}px;position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border)">`;
  html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);font-size:9px;color:var(--text2);display:flex;align-items:center;justify-content:center">时间</div>`;
  html += `<div style="flex:1;position:relative;overflow:hidden">`;
  const tickInterval = Math.max(1, Math.floor(5 / zoom));
  for (let t = 0; t <= dur; t += tickInterval/2) {
    const x = t * pxPerSec; const isMain = t % tickInterval === 0;
    html += `<div style="position:absolute;left:${x}px;bottom:0;width:1px;height:${isMain?10:5}px;background:var(--border)"></div>`;
    if (isMain) html += `<div style="position:absolute;left:${x}px;bottom:12px;transform:translateX(-50%);font-size:9px;color:var(--text2)">${t.toFixed(1)}s</div>`;
  }
  html += `</div></div>`;
  const colors = {video:'#4f8cff', audio:'#34d399', text:'#fbbf24', caption:'#f59e0b', image:'#a855f7'};
  const trackNames = {video:'视频', audio:'音频', text:'文字', caption:'字幕', image:'图片'};
  for (const track of (tl.tracks||[])) {
    const kind = track.kind || 'video', color = colors[kind] || '#888';
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">${trackNames[kind]||kind}</div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;
    for (const clip of (track.clips||[])) {
      const x = (clip.start_sec||0) * pxPerSec, w = Math.max((clip.duration_sec||1) * pxPerSec, 4);
      const meta = clip.metadata || {};
      const label = clip.text || meta.label || meta.source_title || (clip.asset_id?clip.asset_id.split('/').pop().split('\\').pop().slice(0,20):'') || clip.id || '';
      const title = `${label} | ${clip.start_sec.toFixed(1)}s-${(clip.start_sec+clip.duration_sec).toFixed(1)}s`;
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;background:${color}44;border:1px solid ${color}88;display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;font-size:10px" title="${title}">`;
      if (w > 30) html += `<span style="color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label.slice(0,15)}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }
  if (sttSegments && sttSegments.length > 0) {
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">配音</div>`;
    html += `<div style="flex:1;position:relative">`;
    for (const seg of sttSegments) {
      const x = (seg.start||0)*pxPerSec, w = Math.max(((seg.end||seg.start+1)-seg.start)*pxPerSec, 6);
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;background:#fbbf2444;border:1px solid #fbbf2488;display:flex;align-items:center;justify-content:center;overflow:hidden;font-size:10px;cursor:pointer" title="${seg.text}">`;
      if (w > 40) html += `<span style="color:#fff;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${seg.text}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }
  html += '</div>';
  container.innerHTML = html;
  document.getElementById('tlRange').textContent = `${dur.toFixed(1)}s`;
}

function zoomTimeline(delta) {
  _tlZoom = Math.max(0.3, Math.min(5, _tlZoom + delta));
  if (_lastTimelineData) renderTimelineWithCaptions(_lastTimelineData, []);
}
