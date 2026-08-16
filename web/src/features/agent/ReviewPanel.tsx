import { useState, useRef, useCallback } from 'react';
import { useAgentStore } from '@/stores/agentStore';
import { requirementsApi, pipelineApi } from '@/services/api';
import { useProjectStore } from '@/stores/projectStore';
import { Button } from '@/components/ui';
import { Markdown } from '@/components/shared/Markdown';
import { resolveMessageAttachments } from './requirementsAttachments';
import { uid } from '@/lib/utils';
import { MessageSquare, ThumbsDown, ThumbsUp, X, Send, Check, Loader2, ChevronLeft } from 'lucide-react';

interface ReviewPanelProps {
  brief?: NonNullable<ReturnType<typeof useAgentStore.getState>['creativeBrief']> | null;
  planMarkdown?: string;
  onBack: () => void;
}

type AnnType = 'comment' | 'dislike' | 'like';

export function ReviewPanel({ brief, planMarkdown, onBack }: ReviewPanelProps) {
  const addAnnotation = useAgentStore((s) => s.addAnnotation);
  const removeAnnotation = useAgentStore((s) => s.removeAnnotation);
  const annotations = useAgentStore((s) => s.annotations);
  const setStatus = useAgentStore((s) => s.setRequirementsStatus);
  const setBrief = useAgentStore((s) => s.setCreativeBrief);
  const setPlan = useAgentStore((s) => s.setProductionPlan);
  const sessionId = useAgentStore((s) => s.requirementsSessionId);
  const addMessage = useAgentStore((s) => s.addRequirementsMessage);

  const [selText, setSelText] = useState('');
  const [toolbarPos, setToolbarPos] = useState<{ x: number; y: number } | null>(null);
  const [commentInput, setCommentInput] = useState('');
  const [showComment, setShowComment] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  const onSelection = useCallback(() => {
    // 评注输入弹层打开时，忽略冒泡上来的 keyup/mouseup，避免清空 toolbarPos 导致输入框被卸载
    if (showComment) return;
    const sel = window.getSelection();
    const txt = sel?.toString().trim();
    if (!txt || txt.length < 2) { setToolbarPos(null); setSelText(''); return; }
    const range = sel?.getRangeAt(0);
    if (!range) return;
    const rect = range.getBoundingClientRect();
    setSelText(txt);
    setToolbarPos({ x: rect.left + rect.width / 2, y: rect.top - 8 });
    setShowComment(false);
  }, [showComment]);

  const addAnn = (type: AnnType) => {
    if (!selText) return;
    if (type === 'comment') {
      setShowComment(true);
      return;
    }
    addAnnotation({ type, text: selText });
    setToolbarPos(null);
    setSelText('');
  };

  const submitComment = () => {
    addAnnotation({ type: 'comment', text: selText, note: commentInput || undefined });
    setToolbarPos(null);
    setSelText('');
    setShowComment(false);
    setCommentInput('');
  };

  const sendFeedback = async () => {
    if (annotations.length === 0 || !sessionId) return;
    setSubmitting(true);
    const lines = annotations.map((a) => {
      const tag = a.type === 'dislike' ? '[删除/不喜欢]' : a.type === 'like' ? '[保留/喜欢]' : `[${a.type || '反馈'}]`;
      const note = a.note ? ` — ${a.note}` : '';
      return `${tag}「${a.text}」${note}`;
    });
    const msg = `以下是我对方案/规划书的反馈：\n${lines.join('\n')}\n\n请根据以上反馈重新生成。`;
    addMessage({ id: uid('m'), role: 'user', content: msg, timestamp: new Date().toISOString() });
    try {
      const res = await requirementsApi.chat({ session_id: sessionId, message: msg });
      const newBrief = res.creative_brief ?? null;
      const newPlan = res.production_plan ?? null;
      const st = res.status as string | undefined;
      // Only reset brief during gathering/brief_ready; preserve it after confirmation
      if (newBrief && (st === 'gathering' || st === 'brief_ready')) { setBrief(newBrief); setStatus('brief_ready'); }
      if (newPlan) { setPlan(newPlan); setStatus('plan_ready'); }
      addMessage({ id: uid('m'), role: 'assistant', content: res.reply ?? '已根据反馈更新方案。',
        timestamp: new Date().toISOString(),
        creative_brief: (st === 'gathering' || st === 'brief_ready') ? newBrief : null,
        production_plan: newPlan });
      useAgentStore.getState().clearAnnotations();
      onBack();
    } catch (e) {
      // B14: 区分离线（网络层失败 → 演示文案）与真实请求失败（4xx/5xx → 提示重试）
      const code = (e as { code?: string })?.code;
      const hasResponse = Boolean((e as { response?: unknown })?.response);
      const isNetwork = code === 'ERR_NETWORK' || code === 'ECONNABORTED' || (!hasResponse && code !== undefined);
      if (isNetwork) {
        addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
          content: '（离线模式）已记录反馈。' });
        onBack();
      } else {
        addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
          content: '反馈发送失败，请稍后重试。' });
      }
    } finally { setSubmitting(false); }
  };

  const confirm = async () => {
    // 防双击并发：同一时刻只允许一次确认，避免产生两条回复/两次管线启动
    if (confirming) return;
    setConfirming(true);
    useAgentStore.getState().setRequirementsBusy(true);
    // 立即关闭审阅界面并清理标注——LLM 生成转入后台执行，UI 不被阻塞
    onBack();
    useAgentStore.getState().clearAnnotations();
    const st = useProjectStore.getState();
    try {
      if (planMarkdown) {
        setStatus('pipeline_running');
        addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
          content: '已确认规划书，启动管线中…请切换到「执行日志」标签查看进度。' });
        const audioDur = st.audioDurationSec || 0;
        const plan = useAgentStore.getState().productionPlan;
        const sceneCount = plan?.scenes?.length ?? 0;
        // 动画阶段逐片段生成耗时长：按音频时长×4 与场景数×240s 取大者，避免管线误超时
        const pipelineTimeoutSec = Math.max(1800, audioDur * 4, sceneCount * 240);
        const res = await pipelineApi.runAsync({
          persona_id: st.personaId ?? 'default',
          category_plugin_id: st.pluginId ?? 'knowledge_longform',
          topic: st.projectName,
          use_v2: true,
          extra_params: {
            script_text: st.scriptText || undefined,
            audio_duration_sec: st.audioDurationSec || undefined,
            split_mode: st.splitMode || undefined,
            video_mode: st.videoMode || undefined,
            audio_path: st.audioPath || undefined,
            auto_dub: st.autoDub,
            voice_id: st.voiceId || undefined,
            dub_segments: st.dubSegments ?? undefined,
            creative_brief: useAgentStore.getState().creativeBrief ?? undefined,
            production_plan: useAgentStore.getState().productionPlan ?? undefined,
            pipeline_timeout_sec: pipelineTimeoutSec,
          },
        });
        useAgentStore.getState().setPipelineId(res.pipeline_id);
        useAgentStore.getState().updatePhase('structure', 5);
      } else {
        setStatus('brief_confirmed');
        addMessage({ id: uid('m'), role: 'user', content: '确认，请生成完整的制作规划书。', timestamp: new Date().toISOString() });
        if (sessionId) {
          try {
            const res = await requirementsApi.chat({ session_id: sessionId, message: '确认，请生成完整的制作规划书。' });
            const plan = res.production_plan ?? null;
            if (plan) { setPlan(plan); setStatus('plan_ready'); }
            // assistant 回复按统一附件规则挂载（规划书消息不再挂旧的创意简报，避免消息内容错配）
            const att = resolveMessageAttachments(res.status, null, plan);
            addMessage({ id: uid('m'), role: 'assistant', content: res.reply ?? '制作规划书已生成。',
              timestamp: new Date().toISOString(), creative_brief: att.creative_brief, production_plan: att.production_plan });
          } catch {
            addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
              content: '（离线模式）已记录确认。连接后端后可重新生成制作规划书。' });
          }
        }
      }
    } catch {
      // 启动失败：回滚状态并提示，避免卡在 pipeline_running 无法重试（仅 plan 分支走此路径）
      setStatus('plan_ready');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '管线启动失败，请稍后重试。' });
    } finally {
      useAgentStore.getState().setRequirementsBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div className="flex flex-col h-full" onMouseUp={onSelection} onKeyUp={onSelection}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-outline-variant/20 shrink-0">
        <button onClick={onBack} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface cursor-pointer">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="text-label-sm font-medium text-on-surface">
          {planMarkdown ? '审阅规划书' : '审阅简报'}
        </span>
        <div className="flex items-center gap-1.5 ml-auto">
          <Button size="sm" variant="outline" onClick={sendFeedback} disabled={submitting || annotations.length === 0}>
            {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            发送反馈
          </Button>
          <Button size="sm" onClick={confirm} disabled={confirming}>
            {confirming ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            确认实施
          </Button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div ref={contentRef} className="flex-1 overflow-y-auto p-4 min-w-0 select-text text-body-sm leading-relaxed whitespace-pre-wrap bg-surface-container-low">
          {brief && !planMarkdown && (
            <div className="space-y-3">
              <h3 className="text-title-sm font-bold text-on-surface">{brief.title}</h3>
              <div><span className="text-on-surface-variant">概述：</span>{brief.overview}</div>
              <div><span className="text-on-surface-variant">目标受众：</span>{brief.target_audience}</div>
              <div><span className="text-on-surface-variant">核心信息：</span>{brief.core_message}</div>
              <div><span className="text-on-surface-variant">风格方向：</span>{brief.style_direction}</div>
              <div><span className="text-on-surface-variant">结构建议：</span>{brief.structure_suggestion}</div>
              <div><span className="text-on-surface-variant">时长预估：</span>{brief.duration_estimate}</div>
              {brief.key_elements?.length > 0 && (
                <div><span className="text-on-surface-variant">关键元素：</span>{brief.key_elements.join('、')}</div>
              )}
              {brief.special_requirements?.length > 0 && (
                <div><span className="text-on-surface-variant">特殊要求：</span>{brief.special_requirements.join('、')}</div>
              )}
              {brief.production_plan && (
                <div><span className="text-on-surface-variant">制作方案：</span>{brief.production_plan}</div>
              )}
              {brief.reference_style && (
                <div><span className="text-on-surface-variant">参考风格：</span>{brief.reference_style}</div>
              )}
              {brief.bgm_requirement && (
                <div><span className="text-on-surface-variant">BGM需求：</span>{brief.bgm_requirement}</div>
              )}
              {brief.era_background && (
                <div><span className="text-on-surface-variant">年代背景：</span>{brief.era_background}</div>
              )}
              {brief.material_requirements && (
                <div className="pt-1 border-t border-outline-variant/10 space-y-1">
                  {brief.material_requirements.type && (
                    <div><span className="text-on-surface-variant">素材类型：</span>{brief.material_requirements.type}</div>
                  )}
                  {brief.material_requirements.source && (
                    <div><span className="text-on-surface-variant">推荐来源：</span>{brief.material_requirements.source}</div>
                  )}
                  {brief.material_requirements.preference && (
                    <div><span className="text-on-surface-variant">素材偏好：</span>{brief.material_requirements.preference}</div>
                  )}
                </div>
              )}
              {brief.animation_style && (
                <div className="pt-1 border-t border-outline-variant/10 space-y-1">
                  {brief.animation_style.style && (
                    <div><span className="text-on-surface-variant">动画风格：</span>{brief.animation_style.style}</div>
                  )}
                  {brief.animation_style.tone && (
                    <div><span className="text-on-surface-variant">色调倾向：</span>{brief.animation_style.tone}</div>
                  )}
                </div>
              )}
              {brief.asset_ratio && (
                <div><span className="text-on-surface-variant">素材/动画占比：</span>实拍 {brief.asset_ratio.footage} · MG {brief.asset_ratio.mg}</div>
              )}
            </div>
          )}
          {planMarkdown && <Markdown text={planMarkdown} />}
        </div>

        <div className="w-[240px] shrink-0 border-l border-outline-variant/20 flex flex-col overflow-y-auto bg-surface-container p-3">
          <p className="text-label font-medium text-on-surface-variant mb-2">
            标注 · {annotations.length}
          </p>
          {annotations.length === 0 && (
            <p className="text-caption text-on-surface-variant/50">框选文字后点击工具按钮添加标注。</p>
          )}
          {annotations.map((a) => (
            <div key={a.id} className="bg-surface rounded-cw-xs border border-outline-variant/20 p-2 mb-1.5 text-label-sm group">
              <div className="flex items-start gap-1.5">
                <span className="shrink-0">
                  {a.type === 'comment' ? <MessageSquare className="w-3 h-3 text-primary" /> :
                   a.type === 'dislike' ? <ThumbsDown className="w-3 h-3 text-error" /> :
                   <ThumbsUp className="w-3 h-3 text-track-audio" />}
                </span>
                <span className="flex-1 break-all">
                  <span className="text-on-surface/60 line-clamp-2">「{a.text.slice(0, 60)}」</span>
                  {a.note && <span className="block text-primary mt-0.5">{a.note}</span>}
                </span>
                <button onClick={() => removeAnnotation(a.id)}
                  className="opacity-0 group-hover:opacity-100 text-on-surface-variant hover:text-error cursor-pointer shrink-0">
                  <X className="w-3 h-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {toolbarPos && !showComment && (
        <div className="fixed z-50 bg-surface-container-high border border-outline-variant/40 rounded-cw-md shadow-xl px-1.5 py-1 flex items-center gap-0.5"
          style={{ left: toolbarPos.x - 60, top: toolbarPos.y - 40 }}>
          <button onClick={() => addAnn('comment')}
            className="flex items-center gap-1 px-2 py-1 rounded-cw-xs text-label-sm text-on-surface-variant hover:text-primary hover:bg-primary/10 cursor-pointer">
            <MessageSquare className="w-3.5 h-3.5" /> 评论
          </button>
          <button onClick={() => addAnn('dislike')}
            className="flex items-center gap-1 px-2 py-1 rounded-cw-xs text-label-sm text-on-surface-variant hover:text-error hover:bg-error/10 cursor-pointer">
            <ThumbsDown className="w-3.5 h-3.5" /> 不喜欢
          </button>
          <button onClick={() => addAnn('like')}
            className="flex items-center gap-1 px-2 py-1 rounded-cw-xs text-label-sm text-on-surface-variant hover:text-track-audio hover:bg-track-audio/10 cursor-pointer">
            <ThumbsUp className="w-3.5 h-3.5" /> 点赞
          </button>
        </div>
      )}

      {toolbarPos && showComment && (
        <div className="fixed z-50 bg-surface-container-high border border-outline-variant/40 rounded-cw-md shadow-xl p-3 w-64"
          style={{ left: toolbarPos.x - 130, top: toolbarPos.y - 60 }}>
          <textarea value={commentInput} onChange={(e) => setCommentInput(e.target.value)}
            placeholder="输入评注…" rows={2} autoFocus
            className="w-full bg-surface rounded-cw-xs border border-outline-variant/30 p-2 text-label-sm text-on-surface outline-none resize-none" />
          <div className="flex gap-1.5 mt-1.5">
            <Button size="sm" onClick={submitComment} className="flex-1">添加</Button>
            <Button size="sm" variant="outline" onClick={() => setShowComment(false)} className="flex-1">取消</Button>
          </div>
        </div>
      )}
    </div>
  );
}
