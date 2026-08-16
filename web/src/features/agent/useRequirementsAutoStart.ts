import { useEffect, useRef } from 'react';
import { useProjectStore } from '@/stores/projectStore';
import { useAgentStore } from '@/stores/agentStore';
import { requirementsApi } from '@/services/api';
import { uid } from '@/lib/utils';
import { demoBrief } from './AgentPanel';
import { resolveMessageAttachments } from './requirementsAttachments';

/**
 * useRequirementsAutoStart — 从 HomePage「开始创作」进入编辑器后自动启动需求 Agent。
 *
 * 挂在 EditorPage 顶层（始终挂载），不依赖 Agent 面板是否展开。
 * 必须等到项目加载完成（ready=true，EditorPage 已恢复 requirements 数据）再消费，
 * 否则会抢在 EditorPage 恢复数据之前清空 topic，导致文案/时长等信息丢失。
 * 当 projectStore.requirementsTopic 有值且尚无对话消息时，自动初始化需求会话、
 * 发送选题并请求生成创意简报 / 规划书。全程仅使用全局 store action。
 */
export function useRequirementsAutoStart(ready: boolean) {
  const requirementsTopic = useProjectStore((s) => s.requirementsTopic);
  const autoStartedRef = useRef(false);

  useEffect(() => {
    if (!ready) return; // 等待项目加载并恢复 requirements 数据后再启动
    if (autoStartedRef.current) return;
    if (!requirementsTopic) return;
    if (useAgentStore.getState().requirementsMessages.length > 0) return;
    autoStartedRef.current = true;

    const st = useProjectStore.getState();
    const t = requirementsTopic;
    st.setRequirementsTopic(''); // consume so it won't re-trigger

    const ag = useAgentStore.getState();
    ag.setRequirementsBusy(true);
    ag.setRequirementsStatus('gathering');
    // B15: 仅保留一条 user 消息（合并选题/文稿/时长），避免双气泡冗余
    let firstMsg = `我的选题是：${t}。`;
    if (st.requirementsScript) firstMsg += `文稿：${st.requirementsScript}。`;
    firstMsg += `预估时长：${st.requirementsAudioDuration}秒。请帮我生成创意简报。`;
    ag.addRequirementsMessage({ id: uid('m'), role: 'user', content: firstMsg, timestamp: new Date().toISOString() });

    (async () => {
      try {
        const res = await requirementsApi.init({
          topic: t,
          persona_id: st.personaId || undefined,
          category_plugin_id: st.pluginId || undefined,
          script_text: st.requirementsScript || undefined,
          audio_duration_sec: st.requirementsAudioDuration || undefined,
          extra: {
            material_source_ids: st.materialSourceIds || [],
            audio_path: st.audioPath || undefined,
            video_mode: st.videoMode || undefined,
            split_mode: st.splitMode || undefined,
            auto_dub: st.autoDub,
            voice_id: st.voiceId || undefined,
            // B21: 配音段传入需求会话，StructureAgent 场景时间与之对齐
            dub_segments: st.dubSegments ?? undefined,
          },
        });
        useAgentStore.getState().setRequirementsSession(res.session_id);
        try {
          const chatRes = await requirementsApi.chat({ session_id: res.session_id, message: firstMsg });
          const brief = chatRes.creative_brief ?? null;
          const plan = chatRes.production_plan ?? null;
          const ag2 = useAgentStore.getState();
          if (brief) { ag2.setCreativeBrief(brief); ag2.setRequirementsStatus('brief_ready'); }
          if (plan) { ag2.setProductionPlan(plan); ag2.setRequirementsStatus('plan_ready'); }
          const att = resolveMessageAttachments(chatRes.status, brief, plan);
          ag2.addRequirementsMessage({
            id: uid('m'), role: 'assistant', content: chatRes.reply ?? chatRes.message ?? '已收到。',
            timestamp: new Date().toISOString(), creative_brief: att.creative_brief, production_plan: att.production_plan,
          });
        } catch {
          const brief = demoBrief(t);
          const ag2 = useAgentStore.getState();
          ag2.setCreativeBrief(brief);
          ag2.setRequirementsStatus('brief_ready');
          ag2.addRequirementsMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(), content: '已为你生成创意简报，请审阅后确认。', creative_brief: brief });
        }
      } catch {
        const brief = demoBrief(t);
        const ag2 = useAgentStore.getState();
        ag2.setCreativeBrief(brief);
        ag2.setRequirementsStatus('brief_ready');
        ag2.addRequirementsMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(), content: '已为你生成创意简报，请审阅后确认。', creative_brief: brief });
      } finally {
        useAgentStore.getState().setRequirementsBusy(false);
      }
    })();
  }, [ready, requirementsTopic]);
}
