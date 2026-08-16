import { memo, useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useVoiceStore } from '@/stores/voiceStore';
import { useProjectStore } from '@/stores/projectStore';
import { voiceApi } from '@/services/api';
import { Button, Badge } from '@/components/ui';
import { AudioPlayer } from '@/components/shared/AudioPlayer';
import type { VoiceRecord } from '@/types/voice';
import {
  Mic, Plus, Trash2, Loader2, Upload, X, Play, AudioLines, Check,
} from 'lucide-react';

const PROVIDERS = [
  { id: 'qwen_tts', label: 'Qwen TTS', color: '#4F8CFF' },
  { id: 'cosyvoice', label: 'CosyVoice', color: '#A855F7' },
  { id: 'minimax', label: 'MiniMax', color: '#F97316' },
] as const;

const ACCEPTED_TYPES = ['.wav', '.mp3', '.m4a', '.flac', '.ogg'];
const MAX_SIZE = 10 * 1024 * 1024;

export function VoicePage({ embedded = false, onSelect }: { embedded?: boolean; onSelect?: (v: VoiceRecord) => void } = {}) {
  const navigate = useNavigate();
  const voices = useVoiceStore((s) => s.voices);
  const loading = useVoiceStore((s) => s.loading);
  const error = useVoiceStore((s) => s.error);
  const fetchVoices = useVoiceStore((s) => s.fetchVoices);
  const deleteVoice = useVoiceStore((s) => s.deleteVoice);
  const clearError = useVoiceStore((s) => s.clearError);

  const [cloneOpen, setCloneOpen] = useState(false);

  useEffect(() => { fetchVoices(); }, [fetchVoices]);

  // Persist voice selection for pipeline (skipped in embedded/modal mode).
  // 持久化的是用户实际选择的 voiceId（projectStore），而不是列表第一项。
  useEffect(() => {
    if (embedded) return;
    const persistVoicePreferences = (state: ReturnType<typeof useProjectStore.getState>) => {
      const voiceId = state.voiceId ?? null;
      localStorage.setItem('clipwright_voice_prefs', JSON.stringify({ voiceId, autoDub: true }));
    };

    const unsub = useProjectStore.subscribe(persistVoicePreferences);
    persistVoicePreferences(useProjectStore.getState());
    return () => unsub();
  }, [embedded]);

  return (
    <div className="h-full overflow-y-auto bg-surface">
      {embedded ? (
        <div className="flex items-center justify-between px-5 py-3 border-b border-outline-variant/25 shrink-0">
          <div>
            <h2 className="text-title-sm font-bold text-on-surface">音色库</h2>
            <p className="text-caption text-on-surface-variant">选择或克隆配音音色</p>
          </div>
          <Button size="sm" onClick={() => setCloneOpen(true)}>
            <Plus className="w-3.5 h-3.5" /> 克隆新音色
          </Button>
        </div>
      ) : (
        <div className="relative border-b border-outline-variant/25 overflow-hidden">
          <div className="absolute inset-0 opacity-[0.06]" style={{
            backgroundImage: 'radial-gradient(circle at 20% 30%, #4F8CFF 0%, transparent 50%), radial-gradient(circle at 85% 70%, #A855F7 0%, transparent 50%)',
          }} />
          <div className="relative max-w-[1100px] mx-auto px-8 py-8">
            <button onClick={() => navigate({ to: '/' })}
              className="text-label-sm text-on-surface-variant hover:text-primary transition-colors mb-4 cursor-pointer">
              ← 返回工作台
            </button>
            <div className="flex items-end justify-between flex-wrap gap-4">
              <div>
                <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-1.5">Voice Clone</p>
                <h1 className="font-display text-[34px] font-bold text-on-surface leading-tight">音色库</h1>
                <p className="text-body text-on-surface-variant mt-1.5 max-w-[520px]">
                  上传人声样本克隆专属音色，用于视频配音与语音合成。
                </p>
              </div>
              <Button onClick={() => setCloneOpen(true)}>
                <Plus className="w-4 h-4" /> 克隆新音色
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className={embedded ? 'px-5 py-5' : 'max-w-[1100px] mx-auto px-8 py-8'}>
        {error && (
          <div className="mb-4 flex items-center gap-2 bg-error/10 border border-error/30 rounded-cw-sm px-4 py-2.5 text-label-sm text-error">
            <span className="flex-1">{error}</span>
            <button onClick={clearError} className="cursor-pointer hover:opacity-70"><X className="w-3.5 h-3.5" /></button>
          </div>
        )}

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-48 bg-surface-container rounded-cw-md animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {voices.map((v) => (
              <VoiceCard key={v.id} voice={v} onDelete={() => deleteVoice(v.id)} onSelect={onSelect} />
            ))}
            <button
              onClick={() => setCloneOpen(true)}
              className="group min-h-[200px] rounded-cw-md border-2 border-dashed border-outline-variant/40 hover:border-primary/60
                flex flex-col items-center justify-center gap-3 transition-all duration-short3 cursor-pointer
                hover:bg-primary/5 text-on-surface-variant hover:text-primary"
            >
              <span className="w-12 h-12 rounded-cw-full border border-current flex items-center justify-center group-hover:scale-110 transition-transform">
                <Mic className="w-5 h-5" />
              </span>
              <span className="text-body-sm font-medium">克隆新音色</span>
              <span className="text-caption opacity-70">上传 5-60 秒纯人声</span>
            </button>
          </div>
        )}
      </div>

      {cloneOpen && <CloneDialog onClose={() => setCloneOpen(false)} />}
    </div>
  );
}

const VoiceCard = memo(function VoiceCard({ voice, onDelete, onSelect }: { voice: VoiceRecord; onDelete: () => void; onSelect?: (v: VoiceRecord) => void }) {
  const provider = PROVIDERS.find((p) => p.id === voice.provider);
  const [previewText, setPreviewText] = useState('你好，这是一段测试语音。');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [synthesizing, setSynthesizing] = useState(false);
  const [synthError, setSynthError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const synthesize = async () => {
    if (!previewText.trim()) return;
    setSynthesizing(true);
    setSynthError(null);
    setPreviewUrl(null);
    try {
      const res = await voiceApi.synthesize({ voice_id: voice.id, text: previewText });
      setPreviewUrl(voiceApi.getAudioUrl(res.audio_url));
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (e instanceof Error ? e.message : '合成失败');
      setSynthError(detail);
    } finally {
      setSynthesizing(false);
    }
  };

  return (
    <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 space-y-3
      hover:border-outline-variant/60 transition-colors">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2.5">
          <span className="w-9 h-9 rounded-cw-full flex items-center justify-center shrink-0"
            style={{ background: `${provider?.color ?? '#4F8CFF'}18` }}>
            <AudioLines className="w-4 h-4" style={{ color: provider?.color ?? '#4F8CFF' }} />
          </span>
          <div>
            <p className="text-body-sm font-medium text-on-surface leading-tight">{voice.voice_name}</p>
            <p className="text-caption text-on-surface-variant font-mono mt-0.5">
              {new Date(voice.created_at).toLocaleDateString('zh-CN')}
            </p>
          </div>
        </div>
        {confirmDelete ? (
          <div className="flex items-center gap-1">
            <button onClick={onDelete}
              className="px-2 py-1 rounded-cw-xs bg-error text-on-error text-caption font-medium cursor-pointer hover:bg-error/90">
              确认
            </button>
            <button onClick={() => setConfirmDelete(false)}
              className="px-2 py-1 rounded-cw-xs text-caption text-on-surface-variant cursor-pointer hover:text-on-surface">
              取消
            </button>
          </div>
        ) : (
          <button onClick={() => setConfirmDelete(true)}
            className="p-1.5 rounded-cw-xs text-on-surface-variant/50 hover:text-error hover:bg-error/10 transition-colors cursor-pointer">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Badge variant="info" style={{ borderColor: provider?.color, color: provider?.color }}>
          {provider?.label ?? voice.provider}
        </Badge>
        <span className="text-caption text-on-surface-variant font-mono truncate">{voice.target_model}</span>
      </div>

      <div className="border-t border-outline-variant/20 pt-3 space-y-2">
        <div className="flex gap-1.5">
          <input
            value={previewText}
            onChange={(e) => setPreviewText(e.target.value)}
            placeholder="输入试听文本…"
            className="flex-1 bg-surface rounded-cw-xs px-2.5 py-1.5 text-label-sm text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
          />
          <Button size="icon-sm" onClick={synthesize} disabled={synthesizing || !previewText.trim()}>
            {synthesizing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
          </Button>
        </div>
        {synthError && <p className="text-caption text-error">{synthError}</p>}
        {previewUrl && <AudioPlayer src={previewUrl} />}
      </div>

      {onSelect && (
        <Button size="sm" onClick={() => onSelect(voice)} className="w-full">
          <Check className="w-3.5 h-3.5" /> 选择此音色
        </Button>
      )}
    </div>
  );
});

function CloneDialog({ onClose }: { onClose: () => void }) {
  const cloneStep = useVoiceStore((s) => s.cloneStep);
  const error = useVoiceStore((s) => s.error);
  const cloneVoice = useVoiceStore((s) => s.cloneVoice);
  const clearError = useVoiceStore((s) => s.clearError);

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState('qwen_tts');
  const [dragOver, setDragOver] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Clear stale error/step left over from a previous dialog session
  useEffect(() => {
    useVoiceStore.setState({ cloneStep: 'idle', error: null });
  }, []);

  const validateFile = useCallback((f: File): boolean => {
    const ext = '.' + (f.name.split('.').pop()?.toLowerCase() ?? '');
    if (!ACCEPTED_TYPES.includes(ext)) {
      setFileError(`不支持的格式，请上传 ${ACCEPTED_TYPES.join(' / ')} 文件`);
      return false;
    }
    if (f.size > MAX_SIZE) {
      setFileError('文件过大，请上传 10MB 以内的音频');
      return false;
    }
    setFileError(null);
    return true;
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f && validateFile(f)) setFile(f);
  }, [validateFile]);

  const handleSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f && validateFile(f)) setFile(f);
  };

  const submit = async () => {
    if (!file || !name.trim()) return;
    const ok = await cloneVoice(file, name.trim(), provider);
    if (ok) onClose();
  };

  const busy = cloneStep === 'uploading' || cloneStep === 'cloning';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={() => { if (!busy) onClose(); }}>
      <div
        className="w-full max-w-md bg-surface-container border border-outline-variant/40 rounded-cw-lg p-6 space-y-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-title-sm font-semibold text-on-surface">克隆新音色</h2>
          <button onClick={onClose} disabled={busy}
            className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`border-2 border-dashed rounded-cw-md p-6 flex flex-col items-center gap-2 cursor-pointer transition-colors
            ${dragOver ? 'border-primary bg-primary/5' : file ? 'border-track-audio/60 bg-track-audio/5' : 'border-outline-variant/40 hover:border-primary/50'}`}
        >
          <input ref={inputRef} type="file" accept={ACCEPTED_TYPES.join(',')} onChange={handleSelect} className="hidden" />
          {file ? (
            <>
              <AudioLines className="w-6 h-6 text-track-audio" />
              <span className="text-body-sm text-on-surface font-medium">{file.name}</span>
              <span className="text-caption text-on-surface-variant">{(file.size / 1024).toFixed(0)} KB</span>
            </>
          ) : (
            <>
              <Upload className="w-6 h-6 text-on-surface-variant" />
              <span className="text-body-sm text-on-surface-variant">拖拽或点击选择音频文件</span>
              <span className="text-caption text-on-surface-variant/60">5-60 秒纯人声，无背景音乐</span>
            </>
          )}
        </div>
        {fileError && <p className="text-caption text-error">{fileError}</p>}

        <div>
          <label className="block text-label text-on-surface-variant mb-1.5">音色名称</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：我的音色"
            className="w-full bg-surface rounded-cw-xs px-3 py-2 text-body-sm text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/50"
          />
        </div>

        <div>
          <label className="block text-label text-on-surface-variant mb-1.5">提供者</label>
          <div className="flex gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                onClick={() => setProvider(p.id)}
                className={`flex-1 px-3 py-2 rounded-cw-xs text-label-sm border transition-colors cursor-pointer ${
                  provider === p.id
                    ? 'border-primary bg-primary/10 text-primary font-medium'
                    : 'border-outline-variant/40 text-on-surface-variant hover:text-on-surface'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          {provider !== 'qwen_tts' && (
            <p className="text-caption text-on-surface-variant mt-1.5">
              {provider === 'minimax' ? '需要公网 URL + 试听文案，后端自动处理上传' : '需要公网 URL，后端自动处理上传'}
            </p>
          )}
        </div>

        {error && cloneStep === 'error' && (
          <div className="flex items-center gap-2 bg-error/10 border border-error/30 rounded-cw-xs px-3 py-2 text-label-sm text-error">
            <span className="flex-1">{error}</span>
            <button onClick={clearError} className="cursor-pointer hover:opacity-70"><X className="w-3 h-3" /></button>
          </div>
        )}

        <div className="flex gap-2 pt-1">
          <Button variant="outline" onClick={onClose} className="flex-1" disabled={busy}>取消</Button>
          <Button onClick={submit} disabled={!file || !name.trim() || busy} className="flex-1">
            {busy ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {cloneStep === 'uploading' ? '上传中…' : '克隆中…'}
              </>
            ) : (
              <>
                <Mic className="w-4 h-4" /> 开始克隆
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
