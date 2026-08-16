import { useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { personaApi } from '@/services/api';
import { Button, Badge } from '@/components/ui';
import { uid } from '@/lib/utils';
import { toast } from '@/stores/toastStore';
import type { ParameterLayer, Persona } from '@/types/persona';
import {
  ArrowLeft, Send, Sparkles, Bot, User, Check, Loader2, FileUp, Dna,
  FileText, MessageCircleQuestion, Wand2, RotateCcw, ExternalLink,
} from 'lucide-react';

interface ChatMsg { id: string; role: 'user' | 'assistant'; content: string; }
type Dimension = 'identity' | 'language' | 'rhythm' | 'visual' | 'audio' | 'constraints';
const DIMENSIONS: { key: Dimension; label: string; color: string }[] = [
  { key: 'identity', label: '身份', color: '#4F8CFF' },
  { key: 'language', label: '语言', color: '#A855F7' },
  { key: 'rhythm', label: '节奏', color: '#FBBF24' },
  { key: 'visual', label: '视觉', color: '#34D399' },
  { key: 'audio', label: '音频', color: '#F59E0B' },
  { key: 'constraints', label: '约束', color: '#FF6B6B' },
];

type ForgeMode = 'chat' | 'prompt' | 'script' | 'dialogue';

const FORGE_MODES: { key: ForgeMode; label: string; icon: typeof Sparkles }[] = [
  { key: 'chat', label: '对话创建', icon: Bot },
  { key: 'prompt', label: '从提示词生成', icon: Wand2 },
  { key: 'script', label: '从脚本文稿生成', icon: FileText },
  { key: 'dialogue', label: '引导问答', icon: MessageCircleQuestion },
];

/** GET /api/persona/forge/chat/state/{session} 的响应结构 */
interface ChatForgeStateResp {
  session_id?: string;
  message_count?: number;
  persona_draft?: unknown;
  knowledge_base_count?: number;
}

/**
 * PersonaForgePage — conversational persona creation + extended forge modes.
 * - chat: 对话式创建（含会话状态恢复）
 * - prompt: 从自然语言提示词生成（POST /from-prompt）
 * - script: 从脚本/口播文本生成（POST /from-script）
 * - dialogue: 引导问答生成（POST /dialogue/generate-questions + /dialogue/build）
 */
export function PersonaForgePage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<ForgeMode>('chat');
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<Dimension, number>>({
    identity: 0, language: 0, rhythm: 0, visual: 0, audio: 0, constraints: 0,
  });
  const [draft, setDraft] = useState<Partial<ParameterLayer> | null>(null);
  const [personaName, setPersonaName] = useState('');
  const [kbBusy, setKbBusy] = useState(false);
  const [kbFile, setKbFile] = useState<{ name: string; total: number; current: number } | null>(null);
  const [restoreId, setRestoreId] = useState('');
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreMsg, setRestoreMsg] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const kbClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 挂载时若 URL 带 ?session= 则自动恢复后端会话状态（GET /state/{session}）
  useEffect(() => {
    const sid = new URLSearchParams(window.location.search).get('session');
    if (!sid) return;
    let alive = true;
    (async () => {
      setRestoreBusy(true);
      try {
        const state = (await personaApi.chatForgeState(sid)) as ChatForgeStateResp;
        if (!alive) return;
        const realId = state.session_id || sid;
        setSessionId(realId);
        if (state.persona_draft) {
          setDraft(state.persona_draft as Partial<ParameterLayer>);
          setProgress(estimateProgressFromDraft(state.persona_draft));
        }
        setMessages((m) => [
          ...m,
          {
            id: uid('m'),
            role: 'assistant',
            content: `已恢复会话 ${realId.slice(0, 12)}：${state.message_count ?? 0} 条对话、${state.knowledge_base_count ?? 0} 个知识块。继续描述即可完善。`,
          },
        ]);
        setRestoreMsg(`会话 ${realId} 已恢复`);
      } catch {
        if (alive) setRestoreMsg('恢复失败：会话不存在或后端不可达');
      } finally {
        if (alive) setRestoreBusy(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => () => {
    if (kbClearTimerRef.current) clearTimeout(kbClearTimerRef.current);
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages.length, busy]);

  const addMsg = (role: ChatMsg['role'], content: string) =>
    setMessages((m) => [...m, { id: uid('m'), role, content }]);

  const handleRestore = async () => {
    const sid = restoreId.trim();
    if (!sid || restoreBusy) return;
    setRestoreBusy(true);
    setRestoreMsg('');
    try {
      const state = (await personaApi.chatForgeState(sid)) as ChatForgeStateResp;
      const realId = state.session_id || sid;
      setSessionId(realId);
      if (state.persona_draft) {
        setDraft(state.persona_draft as Partial<ParameterLayer>);
        setProgress(estimateProgressFromDraft(state.persona_draft));
      }
      addMsg('assistant', `已恢复会话 ${realId.slice(0, 12)}：${state.message_count ?? 0} 条对话、${state.knowledge_base_count ?? 0} 个知识块。继续描述即可完善。`);
      setRestoreMsg(`会话 ${realId} 已恢复`);
    } catch {
      setRestoreMsg('恢复失败：会话不存在或后端不可达');
    } finally {
      setRestoreBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    addMsg('assistant', '你好！我是 PersonaForge。先描述一下你想打造的创作人格吧——比如「一个说话犀利、节奏快、爱用数据说话的科技评论人格」。');
    try {
      const res = await personaApi.chatForgeStart();
      setSessionId(res.session_id);
      if (res.persona_draft) setDraft(res.persona_draft);
      const prog = res.progress;
      if (prog) setProgress((p) => ({ ...p, ...normalizeProgress(prog) }));
    } catch {
      setSessionId(uid('forge')); // offline session
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput('');
    addMsg('user', text);
    setBusy(true);

    try {
      const res = sessionId
        ? await personaApi.chatForgeMessage(sessionId, text)
        : null;
      if (res?.persona_draft) setDraft(res.persona_draft);
      const prog = res?.progress;
      if (prog) {
        const scaled = normalizeProgress(prog);
        setProgress((p) => ({ ...p, ...scaled }));
      }
      addMsg('assistant', res?.reply ?? '收到，让我想想…');
    } catch {
      // Offline: simulate progressive persona building
      await new Promise((r) => setTimeout(r, 500));
      const filled = simulateProgress(progress, text);
      setProgress(filled.progress);
      setDraft(filled.draft);
      addMsg('assistant', filled.reply);
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!personaName.trim()) return;
    setBusy(true);
    try {
      if (sessionId) await personaApi.chatForgeCommit(sessionId, personaName);
      toast('人格已保存', 'success');
      navigate({ to: '/persona' });
    } catch {
      // Stay on the page so the user's work isn't silently lost
      toast('保存失败 — 后端未连接或请求被拒绝', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleKnowledge = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!sessionId) {
      toast('请先开始对话再上传参考文档', 'error');
      e.target.value = '';
      return;
    }
    let text: string;
    try {
      text = await file.text();
    } catch {
      toast('文件读取失败', 'error');
      e.target.value = '';
      return;
    }
    const sections = splitByH1(text);
    setKbFile({ name: file.name, total: sections.length, current: 0 });
    setKbBusy(true);
    try {
      for (let i = 0; i < sections.length; i++) {
        setKbFile({ name: file.name, total: sections.length, current: i + 1 });
        const res = await personaApi.chatForgeKnowledge(
          sessionId,
          sections[i].content,
          file.name,
        );
        if (res.persona_draft) setDraft(res.persona_draft);
        const prog = res.progress;
        if (prog) {
          const scaled = normalizeProgress(prog);
          setProgress((p) => ({ ...p, ...scaled }));
        }
      }
      addMsg('assistant', `参考文档分析完成，共 ${sections.length} 段。`);
    } catch {
      addMsg('assistant', '参考文档上传失败，请重试。');
    } finally {
      setKbBusy(false);
      if (kbClearTimerRef.current) clearTimeout(kbClearTimerRef.current);
      kbClearTimerRef.current = setTimeout(() => setKbFile(null), 2000);
      e.target.value = '';
    }
  };

  const overall = Math.round(DIMENSIONS.reduce((s, d) => s + progress[d.key], 0) / DIMENSIONS.length);

  return (
    <div className="h-full flex flex-col bg-surface overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-3 px-6 py-3.5 border-b border-outline-variant/25 shrink-0">
        <button onClick={() => navigate({ to: '/persona' })}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
          <ArrowLeft className="w-4.5 h-4.5" />
        </button>
        <div className="w-8 h-8 rounded-cw-sm bg-primary-container flex items-center justify-center">
          <Sparkles className="w-4 h-4 text-on-primary-container" />
        </div>
        <div>
          <h1 className="text-title-sm font-semibold text-on-surface leading-tight">PersonaForge · AI 人格构建</h1>
          <p className="text-caption text-on-surface-variant leading-tight">对话 / 提示词 / 脚本 / 引导问答 四种方式塑造创作人格</p>
        </div>
        {mode === 'chat' && <Badge variant="info" className="ml-auto font-mono">{overall}% 完成</Badge>}
      </div>

      {/* forge mode tabs */}
      <div className="flex gap-1 px-6 pt-2 border-b border-outline-variant/25 shrink-0">
        {FORGE_MODES.map((m) => (
          <button key={m.key} onClick={() => setMode(m.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-label-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer ${
              mode === m.key ? 'border-primary text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}>
            <m.icon className="w-3.5 h-3.5" /> {m.label}
          </button>
        ))}
      </div>

      {/* chat session restore bar */}
      {mode === 'chat' && (
        <div className="flex items-center gap-2 px-6 py-1.5 border-b border-outline-variant/15 bg-surface-container-low shrink-0">
          <input
            value={restoreId}
            onChange={(e) => setRestoreId(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleRestore()}
            placeholder="会话 ID（恢复后端历史会话）"
            className="w-60 bg-surface rounded-cw-xs px-2.5 py-1 text-caption font-mono text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
          />
          <Button size="sm" variant="outline" onClick={handleRestore} disabled={restoreBusy || !restoreId.trim()}>
            <RotateCcw className="w-3.5 h-3.5" /> {restoreBusy ? '恢复中…' : '恢复会话'}
          </Button>
          {restoreMsg && <span className="text-caption text-on-surface-variant">{restoreMsg}</span>}
        </div>
      )}

      {mode === 'chat' ? (
        <div className="flex flex-1 overflow-hidden">
          {/* ── chat column ── */}
          <div className="flex-1 flex flex-col min-w-0 border-r border-outline-variant/25">
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 space-y-4 min-h-0">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <div className="w-16 h-16 rounded-cw-full bg-primary-container/40 border border-primary/30 flex items-center justify-center mb-4">
                    <Dna className="w-7 h-7 text-primary" />
                  </div>
                  <p className="text-title-sm font-semibold text-on-surface mb-1.5">打造你的数字分身</p>
                  <p className="text-body-sm text-on-surface-variant max-w-[380px] leading-relaxed mb-5">
                    通过对话描述你的风格，PersonaForge 会从六个维度逐步构建人格参数。也可以上传参考文档让它分析你的作品。
                  </p>
                  <Button onClick={start}><Sparkles className="w-4 h-4" /> 开始对话</Button>
                </div>
              )}

              {messages.map((m) => (
                <div key={m.id} className={`flex gap-2.5 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {m.role === 'assistant' && (
                    <span className="w-7 h-7 rounded-cw-full bg-primary-container flex items-center justify-center shrink-0 mt-0.5">
                      <Bot className="w-3.5 h-3.5 text-on-primary-container" />
                    </span>
                  )}
                  <div className={`max-w-[75%] rounded-cw-md px-3.5 py-2.5 text-body-sm leading-relaxed ${
                    m.role === 'user'
                      ? 'bg-primary-container text-on-primary-container rounded-br-cw-xs'
                      : 'bg-surface-container text-on-surface border border-outline-variant/20 rounded-bl-cw-xs'
                  }`}>
                    {m.content}
                  </div>
                  {m.role === 'user' && (
                    <span className="w-7 h-7 rounded-cw-full bg-secondary-container flex items-center justify-center shrink-0 mt-0.5">
                      <User className="w-3.5 h-3.5 text-secondary" />
                    </span>
                  )}
                </div>
              ))}

              {busy && (
                <div className="flex items-center gap-2 text-label-sm text-on-surface-variant">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" /> 正在构建人格…
                </div>
              )}
            </div>

            {/* input */}
            {messages.length > 0 && (
              <div className="p-4 border-t border-outline-variant/25 shrink-0">
                <div className="flex gap-2">
                  <label className="p-2.5 rounded-cw-sm bg-surface-container text-on-surface-variant hover:text-primary border border-outline-variant/30 transition-colors cursor-pointer" title="上传参考文档">
                    <FileUp className="w-4 h-4" />
                    <input type="file" className="hidden" accept=".md,.txt" onChange={handleKnowledge} disabled={kbBusy} />
                  </label>
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && send()}
                    placeholder="描述这个人格的风格、语气、节奏…"
                    className="flex-1 bg-surface-container rounded-cw-sm px-3.5 py-2.5 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
                  />
                  <Button size="icon" onClick={send} disabled={!input.trim() || busy}>
                    <Send className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* ── live draft panel ── */}
          <div className="w-[340px] shrink-0 flex flex-col overflow-y-auto p-5 space-y-5 bg-surface-container-low">
            <div>
              <h2 className="text-label font-medium text-on-surface-variant uppercase tracking-wide mb-3">维度完成度</h2>
              <div className="space-y-2.5">
                {DIMENSIONS.map((d) => (
                  <div key={d.key}>
                    <div className="flex justify-between text-label-sm mb-1">
                      <span className="text-on-surface-variant">{d.label}</span>
                      <span className="font-mono text-on-surface-variant">{progress[d.key]}%</span>
                    </div>
                    <div className="h-1.5 bg-surface rounded-cw-full overflow-hidden">
                      <div className="h-full rounded-cw-full transition-all duration-long2"
                        style={{ width: `${progress[d.key]}%`, background: d.color }} />
                    </div>
                  </div>
                ))}
                {kbFile && (
                  <div>
                    <div className="flex justify-between text-label-sm mb-1">
                      <span className="text-on-surface-variant truncate max-w-[180px]">📖 {kbFile.name}</span>
                      <span className="font-mono text-on-surface-variant">{kbFile.current}/{kbFile.total}</span>
                    </div>
                    <div className="h-1.5 bg-surface rounded-cw-full overflow-hidden">
                      <div className="h-full rounded-cw-full transition-all duration-medium2 bg-track-audio animate-pulse"
                        style={{ width: `${(kbFile.current / kbFile.total) * 100}%` }} />
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* live draft preview */}
            <div>
              <h2 className="text-label font-medium text-on-surface-variant uppercase tracking-wide mb-2.5">人格草稿</h2>
              <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-3.5 space-y-2">
                {draft ? (
                  <>
                    {draft.identity?.persona_name && (
                      <p className="text-body-sm font-semibold text-on-surface">{draft.identity.persona_name}</p>
                    )}
                    {draft.identity?.tone && (
                      <p className="text-label-sm text-on-surface-variant">语气：<span className="text-primary font-mono">{draft.identity.tone}</span></p>
                    )}
                    {draft.rhythm?.cut_density_tier && (
                      <p className="text-label-sm text-on-surface-variant">剪切密度：<span className="text-primary font-mono">{draft.rhythm.cut_density_tier}</span></p>
                    )}
                    {draft.visual?.animation_style && (
                      <p className="text-label-sm text-on-surface-variant">动画风格：<span className="text-primary">{draft.visual.animation_style}</span></p>
                    )}
                    {draft.language?.academic_density != null && (
                      <p className="text-label-sm text-on-surface-variant">学术密度：<span className="text-primary font-mono">{draft.language.academic_density}</span></p>
                    )}
                  </>
                ) : (
                  <p className="text-label-sm text-on-surface-variant/60">随着对话进行，人格草稿会在这里实时成形。</p>
                )}
              </div>
            </div>

            {/* commit */}
            <div className="mt-auto pt-3 border-t border-outline-variant/25 space-y-2.5">
              <input
                value={personaName}
                onChange={(e) => setPersonaName(e.target.value)}
                placeholder="人格名称，如「老陈·数码毒舌」"
                className="w-full bg-surface-container rounded-cw-sm px-3 py-2 text-body-sm text-on-surface
                  outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
              />
              <Button className="w-full" onClick={commit} disabled={overall < 40 || !personaName.trim() || busy}>
                <Check className="w-4 h-4" /> 保存人格
              </Button>
              {overall < 40 && (
                <p className="text-caption text-on-surface-variant/60 text-center">完成度达 40% 后可保存</p>
              )}
            </div>
          </div>
        </div>
      ) : mode === 'prompt' ? (
        <PromptForgeBody />
      ) : mode === 'script' ? (
        <ScriptForgeBody />
      ) : (
        <DialogueForgeBody />
      )}
    </div>
  );
}

/**
 * PromptForgeBody — 从自然语言提示词生成 Persona（POST /from-prompt）。
 */
function PromptForgeBody() {
  const navigate = useNavigate();
  const [description, setDescription] = useState('');
  const [personaId, setPersonaId] = useState('');
  const [personaName, setPersonaName] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Persona | null>(null);
  const [error, setError] = useState('');

  const generate = async () => {
    if (!description.trim() || busy) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const manifest = await personaApi.forgeFromPrompt(
        description.trim(),
        personaId.trim() || uid('persona_'),
        personaName.trim() || undefined,
      );
      setResult(manifest as Persona);
    } catch {
      setError('生成失败：后端未连接或请求被拒绝');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ForgeBodyShell>
      <h2 className="text-body-sm font-medium text-on-surface mb-1.5">描述你的创作人格</h2>
      <p className="text-label-sm text-on-surface-variant mb-2">用自然语言描述风格、语气、节奏等，系统自动映射为结构化 Persona。</p>
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={8}
        placeholder="例如：一个说话犀利、节奏快、爱用数据说话的科技评论人格……"
        className="w-full bg-surface-container rounded-cw-md px-4 py-3 text-body-sm text-on-surface
          outline-none border border-outline-variant/30 focus:border-primary resize-y leading-relaxed placeholder:text-on-surface-variant/40"
      />
      <div className="grid grid-cols-2 gap-3">
        <input
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          placeholder="Persona ID（留空自动生成）"
          className="w-full bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
        />
        <input
          value={personaName}
          onChange={(e) => setPersonaName(e.target.value)}
          placeholder="人格名称（可选）"
          className="w-full bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
        />
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={generate} disabled={busy || !description.trim()}>
          <Wand2 className="w-4 h-4" /> {busy ? '生成中…' : '从提示词生成'}
        </Button>
        {error && <span className="text-caption text-error">{error}</span>}
      </div>
      {result && <ManifestResultCard persona={result} navigate={navigate} />}
    </ForgeBodyShell>
  );
}

/**
 * ScriptForgeBody — 从脚本/口播文本生成 Persona（POST /from-script）。
 */
function ScriptForgeBody() {
  const navigate = useNavigate();
  const [script, setScript] = useState('');
  const [scriptFormat, setScriptFormat] = useState<'txt' | 'srt' | 'md'>('txt');
  const [personaId, setPersonaId] = useState('');
  const [personaName, setPersonaName] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Persona | null>(null);
  const [error, setError] = useState('');

  const generate = async () => {
    if (!script.trim() || busy) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const manifest = await personaApi.forgeFromScript(
        script.trim(),
        personaId.trim() || uid('persona_'),
        personaName.trim() || undefined,
        scriptFormat,
      );
      setResult(manifest as Persona);
    } catch {
      setError('生成失败：后端未连接或请求被拒绝');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ForgeBodyShell>
      <div className="flex items-center justify-between mb-1.5">
        <h2 className="text-body-sm font-medium text-on-surface">粘贴脚本文稿</h2>
        <select
          value={scriptFormat}
          onChange={(e) => setScriptFormat(e.target.value as 'txt' | 'srt' | 'md')}
          className="bg-surface-container rounded-cw-xs px-2 py-1 text-caption text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
        >
          <option value="txt">纯文本 (.txt)</option>
          <option value="srt">字幕 (.srt)</option>
          <option value="md">Markdown (.md)</option>
        </select>
      </div>
      <p className="text-label-sm text-on-surface-variant mb-2">上传或粘贴口播稿/字幕，系统通过语言分析提取创作风格。</p>
      <textarea
        value={script}
        onChange={(e) => setScript(e.target.value)}
        rows={10}
        placeholder="粘贴你的脚本、口播稿或字幕内容……"
        className="w-full bg-surface-container rounded-cw-md px-4 py-3 text-body-sm font-mono text-on-surface
          outline-none border border-outline-variant/30 focus:border-primary resize-y leading-relaxed placeholder:text-on-surface-variant/40"
      />
      <div className="grid grid-cols-2 gap-3">
        <input
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          placeholder="Persona ID（留空自动生成）"
          className="w-full bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
        />
        <input
          value={personaName}
          onChange={(e) => setPersonaName(e.target.value)}
          placeholder="人格名称（可选）"
          className="w-full bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
        />
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={generate} disabled={busy || !script.trim()}>
          <FileText className="w-4 h-4" /> {busy ? '生成中…' : '从脚本文稿生成'}
        </Button>
        {error && <span className="text-caption text-error">{error}</span>}
      </div>
      {result && <ManifestResultCard persona={result} navigate={navigate} />}
    </ForgeBodyShell>
  );
}

/**
 * DialogueForgeBody — 引导问答生成 Persona
 * （POST /dialogue/generate-questions → 问答 → POST /dialogue/build）。
 */
function DialogueForgeBody() {
  const navigate = useNavigate();
  const [personaId, setPersonaId] = useState('');
  const [questions, setQuestions] = useState<Array<{ question?: string; category?: string; field?: string }>>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Persona | null>(null);
  const [error, setError] = useState('');

  const generateQuestions = async () => {
    const pid = personaId.trim();
    if (!pid || busy) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const qs = await personaApi.forgeDialogueQuestions(pid);
      setQuestions(qs.length ? qs : [{ question: '请描述你的视频创作风格', category: 'identity', field: 'tone' }]);
      setAnswers({});
    } catch {
      setError('生成引导问题失败：后端不可达或人格不存在');
    } finally {
      setBusy(false);
    }
  };

  const build = async () => {
    const pid = personaId.trim();
    if (!pid || busy) return;
    setBusy(true);
    setError('');
    try {
      const manifest = await personaApi.forgeDialogueBuild(pid, answers);
      setResult(manifest as Persona);
    } catch {
      setError('构建失败：后端未连接或请求被拒绝');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ForgeBodyShell>
      <h2 className="text-body-sm font-medium text-on-surface mb-1.5">引导问答</h2>
      <p className="text-label-sm text-on-surface-variant mb-2">基于已有 Persona 生成引导问题，逐题回答后编译为完整配置。</p>
      <div className="flex gap-2">
        <input
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          placeholder="Persona ID（已有的人格）"
          className="flex-1 bg-surface-container rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
        />
        <Button variant="outline" onClick={generateQuestions} disabled={busy || !personaId.trim()}>
          <MessageCircleQuestion className="w-4 h-4" /> 生成引导问题
        </Button>
      </div>
      {questions.length > 0 && (
        <div className="space-y-3">
          {questions.map((q, i) => (
            <div key={i} className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-3">
              <p className="text-body-sm text-on-surface mb-1.5">{q.question}</p>
              <textarea
                value={answers[q.field ?? q.question ?? String(i)] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.field ?? q.question ?? String(i)]: e.target.value }))}
                rows={2}
                placeholder="回答…"
                className="w-full bg-surface rounded-cw-xs px-2.5 py-1.5 text-body-sm text-on-surface
                  outline-none border border-outline-variant/30 focus:border-primary resize-none placeholder:text-on-surface-variant/50"
              />
            </div>
          ))}
          <div className="flex items-center gap-3">
            <Button onClick={build} disabled={busy}>
              <Check className="w-4 h-4" /> {busy ? '构建中…' : '构建 Persona'}
            </Button>
            {error && <span className="text-caption text-error">{error}</span>}
          </div>
        </div>
      )}
      {questions.length === 0 && error && <p className="text-caption text-error">{error}</p>}
      {result && <ManifestResultCard persona={result} navigate={navigate} />}
    </ForgeBodyShell>
  );
}

/** 扩展模式通用外层布局。 */
function ForgeBodyShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto p-6 min-h-0">
      <div className="max-w-[760px] mx-auto space-y-4">{children}</div>
    </div>
  );
}

/** 生成结果卡片：展示 PersonaManifest 摘要并跳转到详情页。 */
function ManifestResultCard({ persona, navigate }: { persona: Persona; navigate: ReturnType<typeof useNavigate> }) {
  const p = persona.parameter;
  return (
    <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 space-y-2">
      <h3 className="text-body-sm font-medium text-on-surface">已生成 Persona</h3>
      <p className="text-body-sm text-on-surface font-semibold">{persona.persona_name}</p>
      <p className="text-caption text-on-surface-variant font-mono">{persona.persona_id} · v{persona.version}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-label-sm">
        <span className="text-on-surface-variant">语气: <span className="text-primary font-mono">{p?.identity?.tone || '-'}</span></span>
        <span className="text-on-surface-variant">剪切密度: <span className="text-primary font-mono">{p?.rhythm?.cut_density_tier || '-'}</span></span>
        <span className="text-on-surface-variant">动画风格: <span className="text-primary">{p?.visual?.animation_style || '-'}</span></span>
        <span className="text-on-surface-variant">定位: <span className="text-primary">{p?.identity?.positioning || '-'}</span></span>
      </div>
      <div className="pt-1">
        <Button size="sm" variant="outline"
          onClick={() => navigate({ to: '/persona/$personaId', params: { personaId: persona.persona_id } })}>
          前往编辑 <ExternalLink className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

/** 由后端会话草稿估算各维度完成度（get_state 不返回 progress 字段）。 */
function estimateProgressFromDraft(draft: unknown): Record<Dimension, number> {
  const out: Record<Dimension, number> = {
    identity: 0, language: 0, rhythm: 0, visual: 0, audio: 0, constraints: 0,
  };
  if (!draft || typeof draft !== 'object') return out;
  const d = draft as Record<string, unknown>;
  const dims: [Dimension, unknown][] = [
    ['identity', d.identity],
    ['language', d.language],
    ['rhythm', d.rhythm],
    ['visual', d.visual],
    ['audio', d.audio],
    ['constraints', d.constraints],
  ];
  for (const [key, val] of dims) {
    if (dimensionHasSignal(val)) out[key] = 100;
  }
  return out;
}

function dimensionHasSignal(val: unknown): boolean {
  if (!val || typeof val !== 'object') return false;
  return Object.values(val as Record<string, unknown>).some((v) => {
    if (typeof v === 'string') return v.trim().length > 0;
    if (typeof v === 'number') return v !== 0;
    if (typeof v === 'boolean') return v;
    if (Array.isArray(v)) return v.length > 0;
    if (v && typeof v === 'object') return Object.keys(v as object).length > 0;
    return v != null;
  });
}

/** Offline simulation: each user message advances 1-2 dimensions. */
function simulateProgress(
  prev: Record<Dimension, number>,
  _userText: string,
): { progress: Record<Dimension, number>; draft: Partial<ParameterLayer>; reply: string } {
  const order: Dimension[] = ['identity', 'language', 'rhythm', 'visual', 'audio', 'constraints'];
  const next = { ...prev };
  // advance the least-filled dimension(s)
  const sorted = [...order].sort((a, b) => next[a] - next[b]);
  const bump = sorted[0];
  next[bump] = Math.min(100, next[bump] + 34 + Math.round(Math.random() * 12));
  if (Math.random() > 0.5 && next[sorted[1]] < 100) {
    next[sorted[1]] = Math.min(100, next[sorted[1]] + 25);
  }

  const draft: Partial<ParameterLayer> = {
    identity: { persona_id: 'forging', persona_name: '新人格', version: '0.1.0', tone: 'critical', knowledge_domains: ['科技'] },
    language: next.language > 30 ? { max_sentence_length: 20, academic_density: 0.6, slang_ratio: 0.25 } : undefined,
    rhythm: next.rhythm > 30 ? { cut_density_tier: 'high', base_shot_duration_sec: 5 } : undefined,
    visual: next.visual > 30 ? { animation_style: '关键词标注', color_palette: { primary: '#1a1a2e', accent: '#e94560' } } : undefined,
    audio: next.audio > 30 ? { loudness_target_lufs: -15 } : undefined,
    constraints: next.constraints > 30 ? { max_duration_sec: 720 } : undefined,
  };

  const replies: Record<Dimension, string> = {
    identity: '明白了，这个人格的定位和语气我记下了。它主要聊哪些领域？',
    language: '好的，语言风格已捕捉——措辞密度和句式节奏我会按这个来。它的剪辑节奏偏快还是偏慢？',
    rhythm: '收到，节奏感很清晰。视觉上它偏好什么动画风格和配色？',
    visual: '视觉基调已定。音频方面呢——BGM 是铺垫型还是节奏骨架型？响度目标大概多少？',
    audio: '音频参数记好了。最后，有什么硬性约束吗？比如最长时长、是否必须标注来源。',
    constraints: '约束已记录。人格画像基本完整了，给它起个名字就可以保存啦。',
  };
  return { progress: next, draft, reply: replies[bump] };
}

const KB_CHUNK_LIMIT = 6000;

function normalizeProgress(prog: Record<string, number>): Record<string, number> {
  const vals = Object.values(prog).filter((v) => v > 0);
  if (vals.length === 0) return prog;
  // 逐键归一化：混合 0-1 与 0-100 的比例时也按各自尺度处理，避免 0.x 渲染为 0%
  const scaled: Record<string, number> = {};
  for (const [k, v] of Object.entries(prog)) {
    scaled[k] = v <= 1 ? Math.round(v * 100) : v;
  }
  return scaled;
}

function splitByH1(text: string): { heading: string; content: string }[] {
  if (text.length <= KB_CHUNK_LIMIT) return [{ heading: '', content: text }];
  const sections = text.split(/^# /m);
  const chunks: { heading: string; content: string }[] = [];
  for (const section of sections) {
    if (!section.trim()) continue;
    const lines = section.split('\n');
    const heading = lines[0].trim();
    const body = lines.slice(1).join('\n').trim();
    const chunkContent = heading ? `# ${heading}\n\n${body}` : body;
    chunks.push({
      heading: heading || `section_${chunks.length + 1}`,
      content: chunkContent.length > KB_CHUNK_LIMIT ? chunkContent.slice(0, KB_CHUNK_LIMIT) : chunkContent,
    });
  }
  return chunks.length ? chunks : [{ heading: '', content: text.slice(0, KB_CHUNK_LIMIT) }];
}
