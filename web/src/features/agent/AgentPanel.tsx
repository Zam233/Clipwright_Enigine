import { useState, useEffect, useRef, useCallback } from 'react';
import { useAgentStore, loadRequirementsDraft, clearRequirementsDraft } from '@/stores/agentStore';
import { useProjectStore } from '@/stores/projectStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { Markdown } from '@/components/shared/Markdown';
import { TimelineDiffView } from './TimelineDiffView';
import { resolveMessageAttachments } from './requirementsAttachments';
import { pipelineApi, requirementsApi } from '@/services/api';
import { useBackendHealth } from '@/pages/useBackendHealth';
import { Button } from '@/components/ui';
import { uid } from '@/lib/utils';
import type { PipelinePhase, LogEventType, LogEntry } from '@/types/pipeline';
import type { Timeline, Clip, ClipKind } from '@/types/timeline';
import type { RequirementsStatus } from '@/types/persona';
import {
  Bot, Send, Sparkles, Check, FileText, ListChecks, Loader2, Zap,
  MessageSquareText, ChevronDown, ChevronRight, X, Paperclip,
} from 'lucide-react';

const PHASE_LABELS: Record<PipelinePhase, string> = {
  idle: '待命', structure: '结构', material: '素材', edit: '剪辑',
  animation: '动画', audio: '音效', quality: '质检',
  self_heal: '自愈', completed: '完成', failed: '失败',
};

const PHASE_ORDER: PipelinePhase[] = ['structure', 'material', 'edit', 'animation', 'audio', 'quality'];

const LOG_ICONS: Record<LogEventType, string> = {
  agent_start: '▶', agent_end: '✓', llm: '🤖', tool: '🔧',
  skill: '🧠', plugin: '🔌', info: '○', warning: '⚠',
  error: '✗', timeline_snapshot: '📊',
  mg_start: '🎬', mg_end: '✨',
};

const LOG_COLORS: Record<LogEventType, string> = {
  agent_start: 'text-primary', agent_end: 'text-track-audio',
  llm: 'text-track-caption', tool: 'text-on-surface-variant/70',
  skill: 'text-tertiary', plugin: 'text-track-image',
  info: 'text-on-surface-variant/50', warning: 'text-track-text',
  error: 'text-error', timeline_snapshot: 'text-track-video',
  mg_start: 'text-track-text', mg_end: 'text-track-animation',
};

export function AgentPanel() {
  const [tab, setTab] = useState<'requirements' | 'logs'>('requirements');
  const agentTimeline = useAgentStore((s) => s.agentTimeline);
  const setAgentTimeline = useAgentStore((s) => s.setAgentTimeline);
  // G5: 后端心跳——离线时显示横幅，区分"未连接"与"请求失败"
  const backend = useBackendHealth();

  return (
    <div className="flex flex-col h-full bg-surface-container-low">
      {backend === 'offline' && (
        <div className="px-3 py-1 text-caption font-medium text-warning bg-warning/10 border-b border-warning/30 shrink-0">
          后端离线 — 当前为离线演示模式
        </div>
      )}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-outline-variant/30 shrink-0">
        <div className="w-6 h-6 rounded-cw-full bg-primary-container flex items-center justify-center">
          <Bot className="w-3.5 h-3.5 text-on-primary-container" />
        </div>
        <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">
          Agent 副驾驶
        </span>
        <span className="ml-auto w-2 h-2 rounded-cw-full bg-track-audio animate-pulse" title="在线" />
      </div>

      <div className="flex border-b border-outline-variant/30 shrink-0">
        <button onClick={() => setTab('requirements')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-label font-medium border-b-2 transition-colors cursor-pointer ${
            tab === 'requirements' ? 'border-primary text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface'
          }`}>
          <MessageSquareText className="w-3.5 h-3.5" /> 需求对话
        </button>
        <button onClick={() => setTab('logs')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-label font-medium border-b-2 transition-colors cursor-pointer ${
            tab === 'logs' ? 'border-primary text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface'
          }`}>
          <ListChecks className="w-3.5 h-3.5" /> 执行日志
        </button>
      </div>

      <div className="flex-1 overflow-hidden min-h-0">
        {tab === 'requirements' ? <RequirementsView /> : <LogPanel />}
      </div>

      <BottomBar />

      {agentTimeline && (
        <div className="fixed inset-0 z-[60] bg-surface flex flex-col">
          <TimelineDiffView agentTimeline={agentTimeline} onDone={() => setAgentTimeline(null)} />
        </div>
      )}
    </div>
  );
}

function RequirementsView() {
  const status = useAgentStore((s) => s.requirementsStatus);
  const messages = useAgentStore((s) => s.requirementsMessages);
  const addMessage = useAgentStore((s) => s.addRequirementsMessage);
  const setStatus = useAgentStore((s) => s.setRequirementsStatus);
  const setBrief = useAgentStore((s) => s.setCreativeBrief);
  const setPlan = useAgentStore((s) => s.setProductionPlan);
  const setSession = useAgentStore((s) => s.setRequirementsSession);
  const setPipelineId = useAgentStore((s) => s.setPipelineId);
  const updatePhase = useAgentStore((s) => s.updatePhase);
  const setReviewMode = useAgentStore((s) => s.setReviewMode);
  const [topic, setTopic] = useState('');
  const [input, setInput] = useState('');
  const [manualBusy, setBusy] = useState(false);
  // Auto-start (from HomePage launch) runs in EditorPage via useRequirementsAutoStart;
  // reflect its busy flag here so the UI shows progress even though it's store-driven.
  const autoBusy = useAgentStore((s) => s.requirementsBusy);
  const busy = manualBusy || autoBusy;
  const [draftLoaded, setDraftLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  // G7: 参考文件上传——无会话时提示先开始会话
  const handleUploadFile = async (file: File) => {
    const sid = useAgentStore.getState().requirementsSessionId;
    if (!sid) {
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '请先开始需求会话，再上传参考文件。' });
      return;
    }
    setBusy(true);
    try {
      const res = await requirementsApi.upload(sid, file);
      addMessage({ id: uid('m'), role: 'system', timestamp: new Date().toISOString(),
        content: `已上传 ${res?.file_name ?? file.name}` });
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (e instanceof Error ? e.message : '上传失败');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: `上传失败：${detail}` });
    } finally { setBusy(false); }
  };

  // 选中素材标签（C6）：订阅时间轴选中片段与当前时间线
  const selectedClipIds = useSelectionStore((s) => s.selectedClipIds);
  const timeline = useTimelineStore((s) => s.timeline);
  const selectedClips = selectedClipIds
    .map((id) => {
      for (const track of timeline.tracks) {
        const c = track.clips.find((clip) => clip.id === id);
        if (c) return { clip: c, kind: track.kind };
      }
      return null;
    })
    .filter((x): x is { clip: Clip; kind: ClipKind } => x !== null);

  useEffect(() => {
    if (draftLoaded) return;
    // Guard: if store already has messages (e.g. from auto-start or StrictMode re-mount),
    // skip draft loading to prevent duplication.
    if (useAgentStore.getState().requirementsMessages.length > 0) {
      setDraftLoaded(true);
      return;
    }
    const draft = loadRequirementsDraft();
    if (draft && draft.messages?.length > 0) {
      draft.messages.forEach((m) => addMessage(m));
      if (draft.brief) setBrief(draft.brief);
      if (draft.plan) setPlan(draft.plan);
      if (draft.status) setStatus(draft.status);
      if (draft.sessionId) setSession(draft.sessionId);
    }
    setDraftLoaded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- store actions are stable; draft loading must run once
  }, [draftLoaded]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages.length, busy]);

  const startSession = async () => {
    if (!topic.trim()) return;
    setBusy(true);
    setStatus('gathering');
    addMessage({ id: uid('m'), role: 'user', content: `选题：${topic}`, timestamp: new Date().toISOString() });
    try {
      const res = await requirementsApi.init({ topic });
      setSession(res.session_id);
      await sendChat(res.session_id, `我的选题是：${topic}。请帮我生成创意简报。`);
    } catch {
      const brief = demoBrief(topic);
      setBrief(brief);
      setStatus('brief_ready');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '已为你生成创意简报，请审阅后确认。', creative_brief: brief });
    } finally { setBusy(false); }
  };

  const sendChat = async (sessionId: string, message: string) => {
    addMessage({ id: uid('m'), role: 'user', content: message, timestamp: new Date().toISOString() });
    setBusy(true);
    try {
      const res = await requirementsApi.chat({ session_id: sessionId, message });
      const brief = res.creative_brief ?? null;
      const plan = res.production_plan ?? null;
      const st = res.status as string | undefined;
      // 有简报/规划书就刷新（不再仅限特定状态，避免修订版被丢弃）
      if (brief) setBrief(brief);
      if (plan) setPlan(plan);
      // 以后端权威状态同步前端状态机（映射到合法的 RequirementsStatus）
      const VALID: string[] = ['gathering', 'brief_ready', 'brief_confirmed', 'planning', 'plan_ready', 'plan_confirmed', 'pipeline_running', 'pipeline_done', 'completed'];
      if (st && VALID.includes(st)) setStatus(st as RequirementsStatus);
      else if (plan) setStatus('plan_ready');
      else if (brief) setStatus('brief_ready');
      const att = resolveMessageAttachments(st, brief, plan);
      addMessage({ id: uid('m'), role: 'assistant', content: res.reply ?? res.message ?? '已收到。',
        timestamp: new Date().toISOString(),
        creative_brief: att.creative_brief,
        production_plan: att.production_plan });
    } catch {
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '（离线演示）已记录你的需求。' });
    } finally { setBusy(false); }
  };

  // 选中素材 + 自然语言指令 → 时间线编辑（C6）：走 /edit 端点，返回 proposed_timeline 触发 diff 审阅
  const sendEdit = async (sessionId: string, message: string) => {
    const labels = selectedClips.map(({ clip, kind }) => {
      const label = clipText(clip, kind);
      return `${label}(${kind})`;
    });
    const content = `【选中素材】${labels.join('、')}\n${message}`;
    addMessage({ id: uid('m'), role: 'user', content, timestamp: new Date().toISOString() });
    setBusy(true);
    try {
      const res = await requirementsApi.edit({
        session_id: sessionId,
        message,
        timeline: useTimelineStore.getState().timeline,
        selected_clip_ids: selectedClipIds,
      });
      if (res.proposed_timeline) {
        // 触发既有 TimelineDiffView 审阅覆盖层；接受/合并时 TimelineDiffView 会注册真实媒体
        useAgentStore.getState().setAgentTimeline(res.proposed_timeline);
      }
      const att = resolveMessageAttachments(res.status, null, null);
      addMessage({ id: uid('m'), role: 'assistant', content: res.reply ?? '已根据你的指令调整时间线。',
        timestamp: new Date().toISOString(),
        creative_brief: att.creative_brief, production_plan: att.production_plan });
    } catch {
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '（离线演示）已记录你的时间线编辑指令。' });
    } finally { setBusy(false); }
  };

  const confirmBrief = async () => {
    // 防双击并发：store busy 同步置位，二次进入直接返回，杜绝双消息
    const ag = useAgentStore.getState();
    if (ag.requirementsBusy) return;
    ag.setRequirementsBusy(true);
    setStatus('planning');
    try {
      const sid = ag.requirementsSessionId;
      if (sid) {
        // Online: sendChat adds exactly ONE user bubble + reads reply/plan from backend.
        // sendChat 只管理组件内 manualBusy；store busy 必须由本函数在 finally 清理，否则按钮永久禁用
        await sendChat(sid, '确认，请生成完整的制作规划书。');
        return;
      }
      // Offline demo path: exactly ONE user + ONE assistant
      setBusy(true);
      addMessage({ id: uid('m'), role: 'user', content: '确认，请生成制作规划书。', timestamp: new Date().toISOString() });
      await new Promise((r) => setTimeout(r, 600));
      const plan = { markdown: demoPlanMarkdown(topic) };
      setPlan(plan);
      setStatus('plan_ready');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '制作规划书已生成，请审阅。', production_plan: plan });
      setBusy(false);
    } finally {
      useAgentStore.getState().setRequirementsBusy(false);
    }
  };

  const confirmPlan = async () => {
    // 防双击并发：store busy 同步置位
    const ag = useAgentStore.getState();
    if (ag.requirementsBusy) return;
    ag.setRequirementsBusy(true);
    const sid = ag.requirementsSessionId;
    try {
      if (!sid) {
        // 离线演示：无会话，无法启动管线（底部启动 UI 已移除）
        addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
          content: '离线模式无法启动管线，请连接后端后重试。' });
        return;
      }
      setStatus('pipeline_running');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '已确认规划书，正在启动制作管线…' });
      const st = useProjectStore.getState();
      const res = await requirementsApi.proceed(
        sid,
        st.personaId ?? 'default',
        st.pluginId ?? 'knowledge_longform',
      ) as { pipeline_id?: string };
      if (res.pipeline_id) {
        // 设置 pipelineId + 运行相位，BottomBar 的 effect 会自动挂接 SSE 追踪
        setPipelineId(res.pipeline_id);
        updatePhase('structure', 5);
        // 持久化到 sessionStorage：页面刷新后 BottomBar 挂载时可恢复并自动重连 SSE
        try { sessionStorage.setItem('cw_pipeline_id', res.pipeline_id); } catch { /* ignore */ }
      }
    } catch {
      setStatus('plan_ready');
      addMessage({ id: uid('m'), role: 'assistant', timestamp: new Date().toISOString(),
        content: '管线启动失败，请稍后重试。' });
    } finally {
      useAgentStore.getState().setRequirementsBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {messages.length === 0 && (
          <div className="bg-agent-bubble/40 border border-primary-container/40 rounded-cw-md p-3">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-primary" />
              <span className="text-body-sm font-medium text-on-surface">需求 Agent</span>
            </div>
            <p className="text-label-sm text-on-surface-variant leading-relaxed">
              你好！我是需求 Agent。输入选题，我会先帮你梳理<b className="text-on-surface">创意简报</b>，
              确认后再生成完整的<b className="text-on-surface">制作规划书</b>，然后启动生产管线。
            </p>
            <div className="mt-3">
              <input value={topic} onChange={(e) => setTopic(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && startSession()}
                placeholder="输入视频选题…" className="w-full bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
                  outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50" />
              <Button size="sm" onClick={startSession} disabled={!topic.trim() || busy} className="mt-2 w-full">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                开始需求分析
              </Button>
              {loadRequirementsDraft() && (
                <button onClick={() => clearRequirementsDraft()} className="text-caption text-on-surface-variant/50 hover:text-error cursor-pointer mt-1.5 block w-full text-center">
                  清除已保存的会话草稿
                </button>
              )}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-cw-md px-3 py-2 text-body-sm leading-relaxed ${m.role === 'user'
              ? 'bg-primary-container text-on-primary-container rounded-br-cw-xs whitespace-pre-wrap'
              : 'bg-surface-container text-on-surface rounded-bl-cw-xs border border-outline-variant/20'}`}>
              {m.role === 'user'
                ? m.content
                // 带简报/规划书卡片的消息：正文往往嵌入了同一份内容的 markdown，跳过以避免重复渲染
                : (!m.creative_brief && !m.production_plan ? <Markdown text={m.content} /> : null)}
              {m.creative_brief && (
                <div className="mt-2 pt-2 border-t border-outline-variant/20">
                  <BriefCard brief={m.creative_brief} onConfirm={confirmBrief} busy={busy} onReview={() => setReviewMode('brief')} />
                </div>
              )}
              {m.production_plan && (
                <div className="mt-2 pt-2 border-t border-outline-variant/20">
                  <PlanCard markdown={m.production_plan.markdown_content || m.production_plan.markdown || ''} onConfirm={confirmPlan} busy={busy} onReview={() => setReviewMode('plan')} />
                </div>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div className="flex items-center gap-2 text-label-sm text-on-surface-variant px-3">
            <Loader2 className="w-3 h-3 animate-spin text-primary" /> 正在思考…
          </div>
        )}
      </div>

      {/* B17: 管线运行/规划生成期间隐藏输入；完成/失败后恢复 */}
      {messages.length > 0 && !['pipeline_running', 'planning'].includes(status) && (
        <div className="p-3 border-t border-outline-variant/20 shrink-0">
          {selectedClips.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 mb-2">
              <span className="text-caption text-on-surface-variant/60">选中素材</span>
              {selectedClips.map(({ clip, kind }) => (
                <span key={clip.id}
                  className="flex items-center gap-1.5 px-2 py-0.5 rounded-cw-full bg-primary-container/60 text-label-sm text-on-primary-container">
                  <span className="text-caption text-primary uppercase">{kind}</span>
                  <span className="max-w-[130px] truncate">{clipText(clip, kind)}</span>
                  <button onClick={() => useSelectionStore.getState().selectClip(clip.id, true)}
                    className="hover:text-error cursor-pointer" title="移除标签">
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
              <button onClick={() => useSelectionStore.getState().deselectAll()}
                className="text-caption text-on-surface-variant/60 hover:text-error cursor-pointer">
                清除全部
              </button>
            </div>
          )}
          <div className="flex gap-2">
            {/* G7: 参考文件上传 */}
            <input
              ref={uploadInputRef}
              type="file"
              accept=".txt,.md,.pdf,.docx"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUploadFile(f);
                e.target.value = '';
              }}
            />
            <Button size="icon" variant="outline" disabled={busy}
              onClick={() => uploadInputRef.current?.click()} title="上传参考文件">
              <Paperclip className="w-3.5 h-3.5" />
            </Button>
            <input value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return;
                if (busy) return; // 与发送按钮一致，避免并发重复发送
                const sid = useAgentStore.getState().requirementsSessionId;
                if (input.trim() && sid) {
                  const m = input; setInput('');
                  if (selectedClipIds.length > 0) sendEdit(sid, m);
                  else sendChat(sid, m);
                }
              }}
              placeholder={selectedClipIds.length > 0 ? '描述对选中素材的修改…（如：换一个更明亮的素材）' : '继续与需求 Agent 对话…'}
              className="flex-1 bg-surface-container rounded-cw-sm px-3 py-2 text-body-sm text-on-surface
                outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50" />
            <Button size="icon" onClick={() => {
              const sid = useAgentStore.getState().requirementsSessionId;
              if (input.trim() && sid) {
                const m = input; setInput('');
                if (selectedClipIds.length > 0) sendEdit(sid, m);
                else sendChat(sid, m);
              }
            }}             disabled={!input.trim() || busy}>
              <Send className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function LogPanel() {
  const logEntries = useAgentStore((s) => s.logEntries);
  const toggleExpand = useAgentStore((s) => s.toggleLogExpand);
  const clearLogs = useAgentStore((s) => s.clearLogs);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [grouped, setGrouped] = useState(true);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [logEntries.length]);

  const groups = grouped ? buildGroups(logEntries) : null;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-outline-variant/20 shrink-0">
        <span className="text-caption text-on-surface-variant font-mono">
          {logEntries.length} 条
        </span>
        <button onClick={() => setGrouped(!grouped)}
          className="text-caption text-on-surface-variant hover:text-on-surface cursor-pointer ml-auto">
          {grouped ? '展开全部' : '分组'}
        </button>
        <button onClick={clearLogs}
          className="text-caption text-on-surface-variant hover:text-error cursor-pointer">
          清空
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-2 min-h-0 font-mono text-label-sm leading-relaxed">
        {logEntries.length === 0 && (
          <div className="text-center py-8 text-on-surface-variant/40 text-label-sm">等待操作…</div>
        )}
        {grouped && groups
          ? groups.map((g, gi) => <AgentGroup key={gi} group={g} onToggle={toggleExpand}
              defaultOpen={gi === groups.length - 1} />)
          : logEntries.map((e) => <LogLine key={e.id} entry={e} onToggle={toggleExpand} />)
        }
      </div>
    </div>
  );
}

function AgentGroup({ group, onToggle, defaultOpen = false }: {
  group: { agent: string; entries: LogEntry[] };
  onToggle: (id: string) => void;
  defaultOpen?: boolean;
}) {
  const { agent, entries } = group;
  // E7: 默认折叠（已有）；最后一组默认展开，便于查看最近活动
  const [folded, setFolded] = useState(agent !== 'system' && !defaultOpen);
  const types = entries.reduce((acc, e) => { acc[e.type] = (acc[e.type] || 0) + 1; return acc; }, {} as Record<string, number>);
  const typeSummary = Object.entries(types).slice(0, 4).map(([t, n]) => `${n}x ${t}`).join(' ');

  return (
    <div className="mb-1">
      <button onClick={() => setFolded(!folded)}
        className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded-cw-xs hover:bg-surface-container/50 transition-colors cursor-pointer text-label-sm">
        {folded ? <ChevronRight className="w-3 h-3 shrink-0" /> : <ChevronDown className="w-3 h-3 shrink-0" />}
        <span className="font-semibold text-on-surface">{agent}</span>
        <span className="text-on-surface-variant/50 ml-auto">{entries.length} 条</span>
        <span className="text-on-surface-variant/30 text-caption ml-1 truncate max-w-[120px]">{typeSummary}</span>
      </button>
      {!folded && (
        <div className="pl-4">
          {entries.map((e) => <LogLine key={e.id} entry={e} onToggle={onToggle} />)}
        </div>
      )}
    </div>
  );
}

function LogLine({ entry, onToggle }: { entry: LogEntry; onToggle: (id: string) => void }) {
  return (
    <div className="group">
      <button onClick={() => onToggle(entry.id)}
        className={`w-full text-left flex items-start gap-1 py-0.5 hover:bg-surface-container/40 rounded-cw-xs px-1 cursor-pointer ${LOG_COLORS[entry.type]}`}>
        <span className="shrink-0 w-4 text-center">{LOG_ICONS[entry.type]}</span>
        <span className="flex-1 truncate">{entry.summary}</span>
        {entry.detail && (
          <span className="text-on-surface-variant/30 text-caption shrink-0">{entry.expanded ? '▾' : '▸'}</span>
        )}
      </button>
      {entry.expanded && entry.detail && (
        <div className="pl-5 pr-1 pb-1">
          <pre className="text-caption text-on-surface-variant/60 bg-surface-container rounded-cw-xs p-2 overflow-x-auto max-h-[200px] overflow-y-auto whitespace-pre-wrap">
            {JSON.stringify(entry.detail, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function BottomBar() {
  const phase = useAgentStore((s) => s.phase);
  const progress = useAgentStore((s) => s.progress);
  const pipelineId = useAgentStore((s) => s.pipelineId);
  const error = useAgentStore((s) => s.error);
  const pipelineSummary = useAgentStore((s) => s.pipelineSummary);
  const mgTotal = useAgentStore((s) => s.mgTotal);
  const mgDone = useAgentStore((s) => s.mgDone);
  const logEntries = useAgentStore((s) => s.logEntries);
  const addLogEntry = useAgentStore((s) => s.addLogEntry);
  const updatePhase = useAgentStore((s) => s.updatePhase);
  const cancelling = useAgentStore((s) => s.cancelling);
  const setCancelling = useAgentStore((s) => s.setCancelling);

  const esRef = useRef<EventSource | null>(null);
  const lastTimelineRef = useRef<Timeline | null>(null);
  // SSE 断线重连定时器
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // SSE 连续失败计数（任何一次成功 onmessage/onopen 都会清零）
  const retryCountRef = useRef(0);
  const [sseDisconnected, setSseDisconnected] = useState(false);

  const running = pipelineId !== null && phase !== 'completed' && phase !== 'failed' && phase !== 'idle';

  // G2: 停止管线 —— 发送取消请求；SSE 端收到 cancelled 事件后走 finish(false) 收尾
  const handleStop = useCallback(async () => {
    if (!pipelineId) return;
    setCancelling(true);
    try {
      await pipelineApi.cancel(pipelineId);
      addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'info', summary: '已发送取消请求…' });
    } catch (e) {
      addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'error', summary: `取消失败: ${(e as Error)?.message || '未知错误'}` });
    } finally {
      setCancelling(false);
    }
  }, [pipelineId, addLogEntry, setCancelling]);

  const openSSE = useCallback((pid: string) => {
    esRef.current?.close();
    const es = new EventSource(pipelineApi.getTraceStreamUrl(pid));
    esRef.current = es;

    const startTimes: Record<string, number> = {};
    let finished = false;
    // 每次挂接 SSE 前清空上一次运行残留的时间线，避免 finish 复用旧时间线（张冠李戴）
    lastTimelineRef.current = null;

    // 完成处理：ok=true 拉取最终时间线进入审阅；ok=false 标记失败并提示
    const finish = async (ok: boolean, errMsg?: string) => {
      if (finished) return;
      finished = true;
      if (ok) {
        updatePhase('completed', 100);
        // B17: 管线完成 → 复位需求状态，输入框恢复可继续对话
        useAgentStore.getState().setRequirementsStatus('pipeline_done');
        // 优先用 SSE 快照；否则从 result 接口取最终时间线
        let tl = lastTimelineRef.current;
        if (!tl) {
          try {
            const result = await pipelineApi.getResult(pid) as { shared_data?: { final_timeline?: Timeline } };
            tl = result?.shared_data?.final_timeline ?? null;
          } catch { tl = null; }
        }
        if (tl) useAgentStore.getState().setAgentTimeline(tl);
      } else {
        updatePhase('failed');
        useAgentStore.getState().setRequirementsStatus('error');
        useAgentStore.getState().setError(errMsg || '管线执行失败');
        addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'error', summary: errMsg || '管线执行失败' });
      }
      es.close();
      esRef.current = null;
      // 管线到达终态：清除持久化的 pipelineId，避免刷新后误重连
      try { sessionStorage.removeItem('cw_pipeline_id'); } catch { /* ignore */ }
    };

    es.onopen = () => {
      // 连接成功：清零连续失败计数并恢复横幅
      retryCountRef.current = 0;
      setSseDisconnected(false);
    };

    // 后端 SSE 不带 event: 字段，所有事件都走默认 message，类型在 payload 的 type 中。
    es.onmessage = (e) => {
      // 任何一条成功事件都说明连接健康，清零重连计数
      retryCountRef.current = 0;
      setSseDisconnected(false);
      let d: Record<string, unknown>;
      try {
        d = JSON.parse((e as MessageEvent).data);
      } catch { return; }
      const t = (d.type as string) || '';
      const name = (d.agent_name || d.agent || 'system') as string;

      switch (t) {
        case 'agent_start': {
          startTimes[name] = Date.now();
          const ph = normalizePhase(name);
          if (ph) updatePhase(ph);
          addLogEntry({ timestamp: Date.now(), agent: name, type: 'agent_start', summary: `${name} 启动` });
          break;
        }
        case 'agent_end':
        case 'agent_complete': {
          const dur = startTimes[name] ? ((Date.now() - startTimes[name]) / 1000).toFixed(1) + 's' : '';
          addLogEntry({ timestamp: Date.now(), agent: name, type: 'agent_end', summary: `${name} 完成${dur ? ` (${dur})` : ''}` });
          break;
        }
        case 'error':
          // 管线级失败（终态）→ 标记失败并结束
          addLogEntry({ timestamp: Date.now(), agent: name, type: 'error', summary: (d.error || d.summary || `${name} 失败`) as string });
          void finish(false, (d.error || d.summary || '管线执行失败') as string);
          break;
        case 'agent_error':
          // 单个 Agent 错误（管线可能自愈恢复）→ 仅记录
          addLogEntry({ timestamp: Date.now(), agent: name, type: 'error', summary: (d.error || d.summary || `${name} 失败`) as string });
          break;
        case 'timeline_snapshot': {
          // 时间线存放在 detail 字段（非 timeline）
          const tl = (d.detail || d.timeline) as Timeline | undefined;
          if (tl) {
            lastTimelineRef.current = tl;
            addLogEntry({ timestamp: Date.now(), agent: name, type: 'timeline_snapshot',
              summary: `时间线: ${tl.tracks?.length || 0}轨, ${tl.duration_sec?.toFixed(0) || 0}s` });
          }
          break;
        }
        case 'done':
        case 'pipeline_complete':
          void finish(true);
          break;
        case 'llm':
        case 'tool':
        case 'skill':
        case 'plugin':
        case 'info':
        case 'warning':
          addLogEntry({
            timestamp: Date.now(),
            agent: (d.agent as string) || 'system',
            type: t as LogEventType,
            summary: (d.summary || d.message || '') as string,
            detail: (d.detail as Record<string, unknown>) || null,
          });
          break;
        case 'mg_start':
          useAgentStore.getState().mgStarted();
          addLogEntry({
            timestamp: Date.now(),
            agent: (d.agent as string) || 'animation',
            type: 'mg_start',
            summary: (d.summary || d.message || 'MG 生成开始') as string,
            detail: (d.detail as Record<string, unknown>) || null,
          });
          break;
          case 'mg_end':
          useAgentStore.getState().mgFinished();
          addLogEntry({
            timestamp: Date.now(),
            agent: (d.agent as string) || 'animation',
            type: 'mg_end',
            summary: (d.summary || d.message || 'MG 生成完成') as string,
            detail: (d.detail as Record<string, unknown>) || null,
          });
          break;
        case 'cancelled':
          // G2: 管线已取消 → 复用 failed 相位收尾（类型不扩散）
          addLogEntry({ timestamp: Date.now(), agent: name, type: 'error', summary: (d.summary || '管线已取消') as string });
          void finish(false, '管线已取消');
          break;
        default:
          break;
      }
    };

    es.onerror = () => {
      // 管线可能长达 30-60 分钟：后端流硬上限/网络抖动都会触发 onerror。
      // B2: 不能依赖 EventSource 原生自动重连（行为不可控、双连接风险）——
      // 先显式 close 释放连接，再走单一手动定时器重连；连续失败 5 次后
      // 判定断线并停止调度（实例已关闭，不再创建新实例）。
      if (finished) return;
      es.close();
      esRef.current = null;
      retryCountRef.current += 1;
      if (retryCountRef.current >= 5) {
        setSseDisconnected(true);
        addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'error',
          summary: 'SSE 连接已断开（多次重连失败），请手动刷新恢复追踪' });
        return; // 已 close + 不调度 → 无后台重连
      }
      if (!retryTimerRef.current) {
        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null;
          if (!finished && !esRef.current) openSSE(pid);
        }, 3000);
      }
      addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'warning', summary: 'SSE 连接中断，3s 后重连…' });
    };
  }, [addLogEntry, updatePhase]);

  // 页面刷新后 store 被重置：从 sessionStorage 恢复运行中的 pipelineId 并重新挂接 SSE。
  // confirmPlan 启动管线时写入 cw_pipeline_id，finish（终态）时清除。
  useEffect(() => {
    let stored: string | null = null;
    try { stored = sessionStorage.getItem('cw_pipeline_id'); } catch { /* ignore */ }
    if (stored && !useAgentStore.getState().pipelineId) {
      useAgentStore.getState().setPipelineId(stored);
      // phase 刷新后回落为 idle，恢复为运行相位以触发下方 SSE 挂接 effect
      if (useAgentStore.getState().phase === 'idle') {
        useAgentStore.getState().updatePhase('structure');
      }
    }
  }, []);

  useEffect(() => {
    if (pipelineId && running && !esRef.current) {
      openSSE(pipelineId);
    }
  }, [pipelineId, running, openSSE]);

  useEffect(() => () => {
    esRef.current?.close();
    if (retryTimerRef.current) { clearTimeout(retryTimerRef.current); retryTimerRef.current = null; }
  }, []);

  return (
    <div className="border-t border-outline-variant/20 shrink-0 bg-surface-container-low">
      {sseDisconnected && (
        <div className="px-3 py-1.5 text-caption font-medium text-error bg-error/10 border-b border-error/30">
          连接已断开
        </div>
      )}
      {running && (
        <div className="px-3 pt-2 space-y-0.5">
          <div className="flex items-center justify-end pt-1">
            <Button
              variant="outline"
              size="sm"
              disabled={cancelling}
              onClick={handleStop}
              className="text-error hover:bg-error/10 border-error/40"
            >
              <X className="w-3.5 h-3.5 mr-1" />
              {cancelling ? '取消中…' : '停止'}
            </Button>
          </div>
          {PHASE_ORDER.map((p) => {
            const idx = PHASE_ORDER.indexOf(p);
            const curIdx = PHASE_ORDER.indexOf(phase as never);
            const isSelfHeal = (phase as string) === 'self_heal';
            // self_heal 发生在所有阶段之后，视为各阶段已完成（正在联动重做）
            const done = (phase as string) === 'completed' || isSelfHeal || (curIdx > idx);
            const active = (phase as string) === p;
            return (
              <div key={p} className="flex items-center gap-1.5">
                <span className={`w-3.5 h-3.5 rounded-cw-full flex items-center justify-center shrink-0 text-caption ${
                  done ? 'bg-track-audio text-black' : active ? 'bg-primary' : 'bg-surface-container text-on-surface-variant/50'
                }`}>
                  {done ? <Check className="w-2 h-2" /> : active ? <Loader2 className="w-2 h-2 animate-spin" /> : ''}
                </span>
                <span className={`text-caption ${active ? 'text-primary font-semibold' : done ? 'text-on-surface' : 'text-on-surface-variant/50'}`}>
                  {PHASE_LABELS[p]}
                </span>
                {active && (
                  <div className="flex-1 h-0.5 bg-surface-container rounded-cw-full overflow-hidden">
                    <div className="h-full bg-primary animate-pulse" style={{ width: `${progress % 100 || 50}%` }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* G4: 当前 Agent 活动（取日志尾部非 system 条目；无则回退相位名） */}
      {running && (
        <div className="px-3 pt-1 text-caption text-on-surface-variant">
          <span className="text-on-surface-variant/60">当前：</span>
          <span className="text-primary font-medium">{currentActivity(logEntries) ?? PHASE_LABELS[phase as keyof typeof PHASE_LABELS] ?? phase}</span>
        </div>
      )}

      {/* MG 动画逐片段进度（动画阶段） */}
      {mgTotal > 0 && (
        <div className="flex items-center gap-2 px-3 pt-1.5">
          <span className="text-caption text-track-animation shrink-0">动画片段</span>
          <div className="flex-1 h-1 bg-surface-container rounded-cw-full overflow-hidden">
            <div className="h-full bg-track-animation transition-all duration-short3"
              style={{ width: `${Math.min(100, Math.round((mgDone / mgTotal) * 100))}%` }} />
          </div>
          <span className="text-caption font-mono text-on-surface-variant shrink-0">{mgDone}/{mgTotal}</span>
        </div>
      )}

      {error && (
        <div className="px-3 py-1.5 text-caption text-error">{error}</div>
      )}

      {pipelineSummary && (
        <div className="px-3 py-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-caption font-mono text-on-surface-variant">
          <span>{pipelineSummary.totalTokens} tokens</span>
          <span>{pipelineSummary.totalCost}</span>
          <span>{pipelineSummary.materialCount} 素材</span>
          {pipelineSummary.selfHealCount > 0 && <span>{pipelineSummary.selfHealCount}× 自愈</span>}
          {pipelineSummary.timelineStats && (
            <span>{pipelineSummary.timelineStats.tracks}轨 {pipelineSummary.timelineStats.clips}clip {pipelineSummary.timelineStats.durationSec}s</span>
          )}
        </div>
      )}
    </div>
  );
}

function BriefCard({ brief, onConfirm, busy, onReview }: { brief: NonNullable<ReturnType<typeof useAgentStore.getState>['creativeBrief']>; onConfirm: () => void; busy: boolean; onReview?: () => void }) {
  return (
    <div className="bg-surface-container border border-primary/30 rounded-cw-md overflow-hidden text-left">
      <div className="flex items-center gap-2 px-3 py-2 bg-primary/10 border-b border-primary/20">
        <FileText className="w-3.5 h-3.5 text-primary" />
        <span className="text-label font-medium text-primary">创意简报</span>
      </div>
      <div className="p-3 space-y-1 text-label-sm">
        <span className="block"><span className="text-on-surface-variant">标题：</span>{brief.title}</span>
        <span className="block"><span className="text-on-surface-variant">概述：</span>{brief.overview}</span>
        <span className="block"><span className="text-on-surface-variant">目标受众：</span>{brief.target_audience}</span>
        <span className="block"><span className="text-on-surface-variant">核心信息：</span>{brief.core_message}</span>
        {brief.style_direction && (
          <span className="block"><span className="text-on-surface-variant">风格方向：</span>{brief.style_direction}</span>
        )}
        {brief.structure_suggestion && (
          <span className="block"><span className="text-on-surface-variant">结构建议：</span>{brief.structure_suggestion}</span>
        )}
        <span className="block"><span className="text-on-surface-variant">时长预估：</span>{brief.duration_estimate}</span>
        {brief.key_elements?.length > 0 && (
          <span className="block"><span className="text-on-surface-variant">关键元素：</span>{brief.key_elements.join('、')}</span>
        )}
        {brief.special_requirements?.length > 0 && (
          <span className="block"><span className="text-on-surface-variant">特殊要求：</span>{brief.special_requirements.join('、')}</span>
        )}
        {brief.production_plan && (
          <span className="block"><span className="text-on-surface-variant">制作方案：</span>{brief.production_plan}</span>
        )}
        {brief.reference_style && (
          <span className="block"><span className="text-on-surface-variant">参考风格：</span>{brief.reference_style}</span>
        )}
        {brief.bgm_requirement && (
          <span className="block"><span className="text-on-surface-variant">BGM需求：</span>{brief.bgm_requirement}</span>
        )}
        {brief.era_background && (
          <span className="block"><span className="text-on-surface-variant">年代背景：</span>{brief.era_background}</span>
        )}
        {brief.material_requirements && (
          <div className="mt-1 pt-1 border-t border-outline-variant/10">
            {brief.material_requirements.type && (
              <span className="block"><span className="text-on-surface-variant">素材类型：</span>{brief.material_requirements.type}</span>
            )}
            {brief.material_requirements.source && (
              <span className="block"><span className="text-on-surface-variant">推荐来源：</span>{brief.material_requirements.source}</span>
            )}
            {brief.material_requirements.preference && (
              <span className="block"><span className="text-on-surface-variant">素材偏好：</span>{brief.material_requirements.preference}</span>
            )}
          </div>
        )}
        {brief.animation_style && (
          <div className="mt-1 pt-1 border-t border-outline-variant/10">
            {brief.animation_style.style && (
              <span className="block"><span className="text-on-surface-variant">动画风格：</span>{brief.animation_style.style}</span>
            )}
            {brief.animation_style.tone && (
              <span className="block"><span className="text-on-surface-variant">色调倾向：</span>{brief.animation_style.tone}</span>
            )}
          </div>
        )}
        {brief.asset_ratio && (
          <span className="block"><span className="text-on-surface-variant">素材/动画占比：</span>实拍 {brief.asset_ratio.footage} · MG {brief.asset_ratio.mg}</span>
        )}
      </div>
      <div className="flex gap-2 px-3 pb-3">
        {onReview && (
          <Button size="sm" variant="outline" onClick={onReview} className="flex-1">
            <FileText className="w-3.5 h-3.5" /> 审阅
          </Button>
        )}
        <Button size="sm" onClick={onConfirm} disabled={busy} className="flex-1">
          <Check className="w-3.5 h-3.5" /> 确认简报
        </Button>
      </div>
    </div>
  );
}

function PlanCard({ markdown, onConfirm, busy, onReview }: { markdown?: string; onConfirm: () => void; busy: boolean; onReview?: () => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-surface-container border border-primary/30 rounded-cw-md overflow-hidden text-left">
      <div className="flex items-center gap-2 px-3 py-2 bg-primary/10 border-b border-primary/20">
        <ListChecks className="w-3.5 h-3.5 text-primary" />
        <span className="text-label font-medium text-primary">制作规划书</span>
      </div>
      <div className="p-3">
        <div className={`${expanded ? 'max-h-[300px] overflow-y-auto' : 'max-h-24 overflow-hidden'} transition-all`}>
          <Markdown text={markdown || ''} />
        </div>
        <button onClick={() => setExpanded(!expanded)} className="text-label text-primary hover:underline mt-1.5 cursor-pointer">
          {expanded ? '收起' : '展开全文'}
        </button>
      </div>
      <div className="flex gap-2 px-3 pb-3">
        {onReview && (
          <Button size="sm" variant="outline" onClick={onReview} className="flex-1">
            <FileText className="w-3.5 h-3.5" /> 审阅
          </Button>
        )}
        <Button size="sm" onClick={onConfirm} disabled={busy} className="flex-1">
          <Zap className="w-3.5 h-3.5" /> 确认并启动管线
        </Button>
      </div>
    </div>
  );
}

/** G4: 当前 Agent 活动文本——取日志尾部非 system 条目的 "agent · summary"。 */
function currentActivity(entries: ReturnType<typeof useAgentStore.getState>['logEntries']): string | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.agent === 'system') continue;
    return `${e.agent} · ${e.summary}`;
  }
  return null;
}

function buildGroups(entries: ReturnType<typeof useAgentStore.getState>['logEntries']) {  const groups: { agent: string; entries: typeof entries }[] = [];
  for (const e of entries) {
    const last = groups[groups.length - 1];
    if (last && last.agent === e.agent) {
      last.entries.push(e);
    } else {
      groups.push({ agent: e.agent, entries: [e] });
    }
  }
  return groups;
}

function normalizePhase(name: string): PipelinePhase | null {
  const n = name.toLowerCase();
  if (n.includes('structure')) return 'structure';
  if (n.includes('material')) return 'material';
  if (n.includes('edit')) return 'edit';
  if (n.includes('animation')) return 'animation';
  if (n.includes('audio')) return 'audio';
  if (n.includes('quality')) return 'quality';
  if (n.includes('self_heal') || n.includes('heal')) return 'self_heal';
  // 未知/编排类 Agent 不映射到具体相位，避免相位指示回退（如 quality→structure）
  return null;
}

export function demoBrief(topic: string) {
  return {
    title: `《${topic}》`, overview: `围绕「${topic}」展开的知识型视频，以问题驱动叙事。`,
    target_audience: '对该主题感兴趣的大众观众',
    core_message: `用 3 个关键论点把「${topic}」讲清楚。`,
    style_direction: '理性克制 + 信息密度高',
    structure_suggestion: '钩子 → 背景 → 论点×3 → 总结',
    duration_estimate: '3-5 分钟',
    key_elements: ['数据可视化', '关键词标注', 'B-roll 穿插'],
    special_requirements: [],
  };
}

/** 选中素材标签的显示文案：文字/字幕取 text，否则 metadata.title 或 asset_id。 */
function clipText(clip: Clip, kind: string): string {
  if ((kind === 'text' || kind === 'caption') && clip.text) return clip.text;
  if (clip.metadata && typeof clip.metadata.title === 'string') return clip.metadata.title as string;
  if (clip.asset_id) return clip.asset_id;
  return kind;
}

function demoPlanMarkdown(topic: string) {
  return `# 制作规划书：${topic || '未命名选题'}\n\n## 一、整体结构\n- 00:00-00:20  钩子\n- 00:20-01:00  背景\n- 01:00-03:30  三个核心论点\n- 03:30-04:00  总结\n\n## 二、场景列表\n1. 开场钩子\n2. 数据展示\n3. 论点一\n4. 论点二\n5. 论点三\n6. 结尾引导`;
}
