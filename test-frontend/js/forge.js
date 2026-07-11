/* PersonaForge 模块 */
function switchForgeTab(name, el) {
  document.querySelectorAll('#section-forge .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('#section-forge .card-body[id^="forge-"]').forEach(d => d.style.display = 'none');
  document.getElementById('forge-' + name).style.display = 'block';
}
async function forgeFromPrompt() {
  const { ok, data } = await api('POST', '/api/persona/forge/from-prompt', {
    description: document.getElementById('forgePromptDesc').value,
    persona_id: document.getElementById('forgePromptId').value,
    persona_name: document.getElementById('forgePromptName').value,
  });
  showResult('forgePromptResult', data, ok);
}
async function forgeFromScript() {
  const { ok, data } = await api('POST', '/api/persona/forge/from-script', {
    script: document.getElementById('forgeScriptText').value,
    persona_id: document.getElementById('forgeScriptId').value,
    persona_name: document.getElementById('forgeScriptName').value,
    script_format: 'txt',
  });
  showResult('forgeScriptResult', data, ok);
}
async function forgeRefine() {
  const { ok, data } = await api('POST', '/api/persona/forge/refine', {
    persona_id: document.getElementById('forgeRefineId').value,
    feedback: document.getElementById('forgeRefineFeedback').value,
  });
  showResult('forgeRefineResult', data, ok);
}
async function dialogueGenerateQuestions() {
  const { ok, data } = await api('POST', '/api/persona/forge/dialogue/generate-questions', {
    persona_id: document.getElementById('forgeDialogueId').value,
    existing_answers: {},
  });
  showResult('forgeDialogueResult', data, ok);
}
async function dialogueBuild() {
  let answers = {};
  try { answers = JSON.parse(document.getElementById('forgeDialogueAnswers').value || '{}'); } catch {}
  const { ok, data } = await api('POST', '/api/persona/forge/dialogue/build', {
    persona_id: document.getElementById('forgeDialogueId').value,
    persona_name: '对话测试',
    answers,
  });
  showResult('forgeDialogueResult', data, ok);
}
