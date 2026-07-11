/* Pipeline 模块 */
async function runPipeline() {
  const el = document.getElementById('pipelineResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">执行中...</span></div>';
  const { ok, data } = await api('POST', '/api/pipeline/run', {
    persona_id: document.getElementById('pipePersonaId').value,
    category_plugin_id: document.getElementById('pipePluginId').value,
    topic: document.getElementById('pipeTopic').value,
    dry_run: false,
  });
  if (ok && data && data.steps) {
    const summary = {
      status: data.status,
      pipeline_id: data.pipeline_id,
      steps: data.steps.map(s => ({ agent: s.agent_name, status: s.status, duration_ms: s.duration_ms })),
      timeline: data.shared_data?.final_timeline ? {
        width: data.shared_data.final_timeline.width,
        height: data.shared_data.final_timeline.height,
        fps: data.shared_data.final_timeline.fps,
        duration_sec: data.shared_data.final_timeline.duration_sec,
        tracks: data.shared_data.final_timeline.tracks?.length,
      } : null,
    };
    showResult('pipelineResult', summary, ok);
  } else {
    showResult('pipelineResult', data, ok);
  }
}
