/* 剪辑工作台 — AI 剪辑一体化工作流 */
let _lastTimelineData = null, _tlZoom = 1.0, _ewStep = 0;

function importEditScript(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => { document.getElementById('ewScript').value = e.target.result; };
  reader.readAsText(file);
}

async function uploadAudio(event) {
  const file = event.target.files[0];
  if (!file) return;
  const el = document.getElementById('ewAudioPath');
  // Upload via asset API
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch(API_BASE() + '/api/asset/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.file_path) {
      el.value = data.file_path;
      addEwLog(`配音上传成功: ${data.filename} (${(data.file_size/1024).toFixed(0)}KB)`);
    }
  } catch(e) {
    el.value = file.name;
  }
}

let _ewLogs = [];
function addEwLog(msg) {
  _ewLogs.push(msg);
  const el = document.getElementById('ewResult');
  el.innerHTML = _ewLogs.map(m => `<div class="text-muted" style="font-size:11px;padding:2px 0">${m}</div>`).join('');
}
function setEwProgress(pct, text) {
  const bar = document.getElementById('ewProgressBar');
  const label = document.getElementById('ewProgressText');
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = text;
  document.getElementById('ewProgress').style.display = 'block';
}

// ── 主流程：生成视频 ──
async function runAiEdit() {
  const btn = document.getElementById('ewRunBtn');
  btn.disabled = true; btn.textContent = '生成中...';
  _ewLogs = []; document.getElementById('ewResult').innerHTML = '';
  _ewStep = 0;

  try {
    const topic = document.getElementById('ewTopic').value.trim();
    const script = document.getElementById('ewScript').value.trim();
    const personaId = document.getElementById('ewPersonaId').value.trim();
    const pluginId = document.getElementById('ewPluginId').value;
    const audioPath = document.getElementById('ewAudioPath').value.trim();

    addEwLog('开始 AI 剪辑流程...');

    // Step 1: STT align (配音+文案对齐)
    _ewStep = 1;
    setEwProgress(10, '正在对齐配音与文案...');
    let sttSegments = [];
    if (audioPath && script) {
      const res = await fetch(API_BASE() + '/api/stt/align', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({audio_path: audioPath, transcript_text: script})
      });
      const data = await res.json();
      if (data.success) {
        sttSegments = data.segments || [];
        addEwLog(`配音对齐完成: ${sttSegments.length} 个时间戳段落`);
      }
    }

    // Step 2: Run pipeline
    _ewStep = 2;
    setEwProgress(30, 'AI 管线运行中 (Structure→Material→Edit→Animation→Audio→Quality)...');
    let pipelineResult;
    try {
      const res = await fetch(API_BASE() + '/api/pipeline/run', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          persona_id: personaId, category_plugin_id: pluginId, topic, dry_run: false
        })
      });
      pipelineResult = await res.json();
    } catch(e) {
      throw new Error('管线调用失败: ' + e.message);
    }

    const tl = pipelineResult?.shared_data?.final_timeline;
    if (!tl) {
      addEwLog('管线完成，但未生成时间线');
      btn.disabled = false; btn.textContent = '生成视频';
      return;
    }
    _lastTimelineData = tl;

    // Show execution trace (LLM calls, Tool calls, Plugin usage)
    const trace = pipelineResult?.shared_data?.execution_trace || [];
    addEwLog(`管线完成: ${(pipelineResult.steps||[]).filter(s=>s.status==='completed').length}/6 Agent 通过`);
    addEwLog(`执行事件: ${trace.length} 条`);

    // Render trace events in a collapsible panel
    const resultEl = document.getElementById('ewResult');
    let traceHtml = '<div style="margin-top:4px;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">';
    traceHtml += '<div style="font-size:11px;font-weight:600;padding:6px 10px;background:var(--surface);cursor:pointer" onclick="toggleTrace()">执行追踪 ▾</div>';
    traceHtml += '<div id="tracePanel" style="max-height:300px;overflow-y:auto;font-size:10px;line-height:1.5">';

    const typeIcons = {llm:'🤖', tool:'🔧', skill:'🧠', plugin:'🔌', agent_start:'▶️', agent_end:'✅', error:'❌', info:'ℹ️'};
    for (const ev of trace) {
      const icon = typeIcons[ev.type] || '•';
      const time = ev.time ? new Date(ev.time*1000).toLocaleTimeString() : '';
      traceHtml += `<div style="padding:4px 10px;border-bottom:1px solid var(--border);display:flex;gap:6px">
        <span>${icon}</span>
        <span style="color:var(--text2);width:50px;flex-shrink:0">${ev.agent||''}</span>
        <span style="flex:1">${ev.summary||''}</span>
        <span style="color:var(--text2);font-size:9px">${time}</span>
      </div>`;
    }
    traceHtml += '</div></div>';
    resultEl.innerHTML = _ewLogs.map(m => `<div class="text-muted" style="font-size:11px;padding:1px 0">${m}</div>`).join('') + traceHtml;

    // Step status
    for (const s of (pipelineResult.steps||[])) {
      addEwLog(`  ${s.agent_name}: ${s.status}${s.duration_ms?' ('+s.duration_ms+'ms)':''}`);
    }

    // Step 3: Render
    _ewStep = 3;
    setEwProgress(70, '正在渲染 MP4...');
    let renderResult;
    try {
      const res = await fetch(API_BASE() + '/api/render/start', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({timeline: tl, output_path: 'renders/ai_edit_output.mp4'})
      });
      renderResult = await res.json();
    } catch(e) {
      addEwLog('渲染调用失败 (FFmpeg 可能未安装): ' + e.message);
      renderResult = null;
    }

    setEwProgress(100, '完成');
    if (renderResult?.success) {
      addEwLog(`渲染完成: ${renderResult.output_path} (${renderResult.duration_sec.toFixed(1)}s)`);
    } else if (renderResult) {
      addEwLog(`渲染状态: ${renderResult.error || '完成 (无文件输出)'}`);
    }

    // Render timeline
    renderTimelineWithCaptions(tl, sttSegments);
    addEwLog('时间线已更新');

  } catch(e) {
    addEwLog('错误: ' + e.message);
  }

  btn.disabled = false; btn.textContent = '生成视频';
}

// ── Timeline Renderer (CapCut-style) ──
function renderTimelineWithCaptions(tl, sttSegments) {
  if (!tl) return renderTimeline(tl);

  const container = document.getElementById('timelineInner');
  const info = document.getElementById('tlInfo');
  const dur = tl.duration_sec || 60;
  info.textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${dur.toFixed(1)}s | ${(tl.tracks||[]).length} 轨道`;

  const zoom = _tlZoom, pxPerSec = 100 * zoom;
  const totalWidth = Math.max(dur * pxPerSec, 400);
  const trackH = 56, rulerH = 30, labelW = 80;

  let html = `<div style="display:flex;flex-direction:column;min-width:${labelW+totalWidth+20}px;user-select:none">`;

  // Ruler
  html += `<div style="display:flex;height:${rulerH}px;position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border)">`;
  html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);font-size:10px;color:var(--text2);display:flex;align-items:center;justify-content:center">时间</div>`;
  html += `<div style="flex:1;position:relative;overflow:hidden">`;
  const tickInterval = Math.max(1, Math.floor(5 / zoom));
  for (let t = 0; t <= dur; t += tickInterval/2) {
    const x = t * pxPerSec;
    const isMain = t % tickInterval === 0;
    html += `<div style="position:absolute;left:${x}px;bottom:0;width:1px;height:${isMain?12:6}px;background:var(--border)"></div>`;
    if (isMain) html += `<div style="position:absolute;left:${x}px;bottom:14px;transform:translateX(-50%);font-size:10px;color:var(--text2)">${t.toFixed(1)}s</div>`;
  }
  html += `</div></div>`;

  // Tracks
  const colors = {video:'#4f8cff', audio:'#34d399', text:'#fbbf24', caption:'#f59e0b', image:'#a855f7', shape:'#ec4899', waveform:'#22d3ee'};
  const trackNames = {video:'视频轨', audio:'音频轨', text:'文字轨', caption:'字幕轨', image:'图片轨'};

  for (const track of (tl.tracks||[])) {
    const kind = track.kind || 'video';
    const color = colors[kind] || '#888';
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">`;
    html += `<span style="font-weight:600">${trackNames[kind]||kind}</span>`;
    html += `<span style="font-size:9px">${(track.clips||[]).length} 片段</span></div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;

    // Grid lines
    for (let t = 0; t <= dur; t += tickInterval/2) {
      const x = t * pxPerSec;
      html += `<div style="position:absolute;left:${x}px;top:0;width:1px;height:100%;background:rgba(255,255,255,0.03)"></div>`;
    }

    // Clips
    for (const clip of (track.clips||[])) {
      const x = (clip.start_sec||0) * pxPerSec;
      const w = Math.max((clip.duration_sec||1) * pxPerSec, 6);
      const top = 4;
      const h = trackH - 8;
      const label = clip.text || clip.asset_id || clip.id || '';
      const shortLabel = label.length > 20 ? label.slice(0,20)+'…' : label;
      const title = `${label} | ${clip.start_sec.toFixed(1)}s-${(clip.start_sec+clip.duration_sec).toFixed(1)}s`;

      html += `<div style="position:absolute;left:${x}px;top:${top}px;width:${w}px;height:${h}px;border-radius:4px;background:${color}44;border:1px solid ${color}88;display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;font-size:10px" title="${title}">`;
      if (w > 40) {
        html += `<span style="color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${shortLabel}</span>`;
      }
      html += `</div>`;
    }
    html += `</div></div>`;
  }

  // STT caption track (if available)
  if (sttSegments && sttSegments.length > 0) {
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface);font-weight:600">配音</div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;
    for (const seg of sttSegments) {
      const x = (seg.start||0) * pxPerSec;
      const w = Math.max(((seg.end||seg.start+1) - (seg.start||0)) * pxPerSec, 10);
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:4px;background:#fbbf2444;border:1px solid #fbbf2488;display:flex;align-items:center;justify-content:center;overflow:hidden;font-size:10px;cursor:pointer" title="${seg.text}">`;
      if (w > 50) html += `<span style="color:#fff;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${seg.text}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }

  html += '</div>';
  container.innerHTML = html;
  document.getElementById('tlRange').textContent = `${dur.toFixed(1)}s`;
}

// Legacy timeline render (also updated)
function renderTimeline(tl) {
  if (!tl) return;
  const container = document.getElementById('timelineInner');
  const info = document.getElementById('tlInfo');
  const dur = tl.duration_sec || 60;
  info.textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${dur.toFixed(1)}s`;
  renderTimelineWithCaptions(tl, []);
}

function toggleTrace() {
  const panel = document.getElementById('tracePanel');
  if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}
function zoomTimeline(delta) {
  _tlZoom = Math.max(0.3, Math.min(5, _tlZoom + delta));
  if (_lastTimelineData) renderTimelineWithCaptions(_lastTimelineData, []);
}
