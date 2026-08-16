import { useState } from 'react';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { subtitleApi, sttApi } from '@/services/api';
import type { SubtitleClip, SttSegment } from '@/services/api/subtitle';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Captions, FileUp, FileDown, AlignLeft, Mic, Loader2, Check } from 'lucide-react';

const LANGUAGES = [
  { value: '', label: '自动检测' },
  { value: 'zh', label: '中文 (zh)' },
  { value: 'en', label: 'English (en)' },
  { value: 'ja', label: '日本語 (ja)' },
  { value: 'ko', label: '한국어 (ko)' },
  { value: 'es', label: 'Español (es)' },
  { value: 'fr', label: 'Français (fr)' },
  { value: 'de', label: 'Deutsch (de)' },
];

const MODEL_SIZES = ['tiny', 'base', 'small', 'medium', 'large'];

const inputCls =
  'w-full bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40';

/**
 * SubtitleToolsPage — 字幕与语音转写控制台 (Subtitle & STT control room).
 * 转写/对齐 (subtitle) · SRT 导入导出 · 独立 STT 端点。均以音频文件路径为输入。
 */
export function SubtitleToolsPage() {
  const [tab, setTab] = useState<'subtitle' | 'stt'>('subtitle');

  // subtitle.transcribe
  const [trAudio, setTrAudio] = useState('');
  const [trLang, setTrLang] = useState('');
  const [trModel, setTrModel] = useState('base');
  const [trFormat, setTrFormat] = useState<'timeline' | 'srt'>('srt');
  const [trBusy, setTrBusy] = useState(false);
  const [trOut, setTrOut] = useState<string | null>(null);

  // subtitle.align
  const [alAudio, setAlAudio] = useState('');
  const [alScript, setAlScript] = useState('');
  const [alLang, setAlLang] = useState('');
  const [alFormat, setAlFormat] = useState<'timeline' | 'srt'>('srt');
  const [alBusy, setAlBusy] = useState(false);
  const [alOut, setAlOut] = useState<string | null>(null);

  // subtitle.import (SRT → clips)
  const [impSrt, setImpSrt] = useState('');
  const [impBusy, setImpBusy] = useState(false);
  const [imported, setImported] = useState<{ segments: number; clips: SubtitleClip[] } | null>(null);

  // subtitle.export (clips → SRT)
  const [expClips, setExpClips] = useState('');
  const [expBusy, setExpBusy] = useState(false);
  const [expOut, setExpOut] = useState<string | null>(null);

  // stt.transcribe
  const [sttAudio, setSttAudio] = useState('');
  const [sttLang, setSttLang] = useState('');
  const [sttModel, setSttModel] = useState('base');
  const [sttWords, setSttWords] = useState(true);
  const [sttBusy, setSttBusy] = useState(false);
  const [sttOut, setSttOut] = useState<SttSegment[] | null>(null);

  // stt.align
  const [staAudio, setStaAudio] = useState('');
  const [staText, setStaText] = useState('');
  const [staLang, setStaLang] = useState('');
  const [staBusy, setStaBusy] = useState(false);
  const [staOut, setStaOut] = useState<SttSegment[] | null>(null);

  const runTranscribe = async () => {
    if (!trAudio.trim()) return;
    setTrBusy(true);
    setTrOut(null);
    try {
      const res = await subtitleApi.transcribe({ audio_path: trAudio.trim(), language: trLang, model_size: trModel, format: trFormat });
      setTrOut(formatSubtitleResult(res.success, res));
    } catch (e) {
      setTrOut(`// 转写失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTrBusy(false);
    }
  };

  const runAlign = async () => {
    if (!alAudio.trim() || !alScript.trim()) return;
    setAlBusy(true);
    setAlOut(null);
    try {
      const res = await subtitleApi.align({ audio_path: alAudio.trim(), script_text: alScript.trim(), language: alLang, format: alFormat });
      setAlOut(formatSubtitleResult(res.success, res));
    } catch (e) {
      setAlOut(`// 对齐失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAlBusy(false);
    }
  };

  const runImport = async () => {
    if (!impSrt.trim()) return;
    setImpBusy(true);
    setImported(null);
    try {
      const res = await subtitleApi.importSrt(impSrt.trim());
      setImported({ segments: res.segments, clips: res.clips ?? [] });
    } catch (e) {
      setImported({ segments: 0, clips: [] });
      setExpOut(`// 导入失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setImpBusy(false);
    }
  };

  const runExport = async () => {
    let clips: SubtitleClip[];
    try {
      clips = JSON.parse(expClips || '[]') as SubtitleClip[];
    } catch {
      setExpOut('// clips JSON 解析失败');
      return;
    }
    setExpBusy(true);
    setExpOut(null);
    try {
      const res = await subtitleApi.exportSrt(clips);
      setExpOut(res.srt || `// 导出成功，共 ${res.segments} 段`);
    } catch (e) {
      setExpOut(`// 导出失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExpBusy(false);
    }
  };

  const runSttTranscribe = async () => {
    if (!sttAudio.trim()) return;
    setSttBusy(true);
    setSttOut(null);
    try {
      const res = await sttApi.transcribe({ audio_path: sttAudio.trim(), language: sttLang, model_size: sttModel, word_timestamps: sttWords });
      setSttOut(res.success ? res.segments : []);
    } catch {
      setSttOut([]);
    } finally {
      setSttBusy(false);
    }
  };

  const runSttAlign = async () => {
    if (!staAudio.trim() || !staText.trim()) return;
    setStaBusy(true);
    setStaOut(null);
    try {
      const res = await sttApi.align({ audio_path: staAudio.trim(), transcript_text: staText.trim(), language: staLang });
      setStaOut(res.success ? res.segments : []);
    } catch {
      setStaOut([]);
    } finally {
      setStaBusy(false);
    }
  };

  const fillExportFromImport = () => {
    if (!imported) return;
    setExpClips(JSON.stringify(imported.clips, null, 2));
    setExpOut(null);
  };

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Media / Subtitle & STT" title="字幕与转写工具"
        desc="自动转写音频、将文案与音频对齐，并在 Timeline 片段与 SRT 字幕格式间互相转换。所有操作以音频文件路径为输入。" />

      {/* tab switch */}
      <div className="flex gap-1 mb-5 bg-surface-container rounded-cw-sm p-1 w-fit border border-outline-variant/30">
        {([
          { key: 'subtitle', label: '字幕 API (subtitle)', icon: Captions },
          { key: 'stt', label: 'STT 端点 (stt)', icon: Mic },
        ] as const).map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setTab(key)}
            className={cn('flex items-center gap-1.5 px-4 py-1.5 rounded-cw-xs text-body-sm font-medium transition-colors cursor-pointer',
              tab === key ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface')}>
            <Icon className="w-3.5 h-3.5" /> {label}
          </button>
        ))}
      </div>

      {tab === 'subtitle' ? (
        <div className="grid grid-cols-12 gap-5">
          {/* transcribe */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<Mic className="w-4 h-4" />} title="转写 · 音频 → 字幕" code="subtitle.transcribe">
              <Field label="音频路径">
                <input value={trAudio} onChange={(e) => setTrAudio(e.target.value)} placeholder="/data/audio/demo.mp3"
                  className={inputCls} />
              </Field>
              <div className="grid grid-cols-3 gap-3">
                <SelectField label="语言" value={trLang} onChange={setTrLang}
                  options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))} />
                <SelectField label="模型" value={trModel} onChange={setTrModel}
                  options={MODEL_SIZES.map((m) => ({ value: m, label: m }))} />
                <SelectField label="输出格式" value={trFormat} onChange={(v) => setTrFormat(v as 'timeline' | 'srt')}
                  options={[{ value: 'srt', label: 'SRT' }, { value: 'timeline', label: 'Timeline' }]} />
              </div>
              <Button onClick={runTranscribe} disabled={!trAudio.trim() || trBusy} className="w-full">
                {trBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mic className="w-4 h-4" />}
                {trBusy ? '转写中…' : '开始转写'}
              </Button>
              {trOut && <Pre>{trOut}</Pre>}
            </OpCard>
          </div>

          {/* align */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<AlignLeft className="w-4 h-4" />} title="对齐 · 音频 + 文案" code="subtitle.align">
              <Field label="音频路径">
                <input value={alAudio} onChange={(e) => setAlAudio(e.target.value)} placeholder="/data/audio/demo.mp3"
                  className={inputCls} />
              </Field>
              <Field label="文案 (script_text)">
                <textarea value={alScript} onChange={(e) => setAlScript(e.target.value)} rows={3} placeholder="已有文案逐行填入，将与音频对齐出时间戳…"
                  className={cn(inputCls, 'resize-y')} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <SelectField label="语言" value={alLang} onChange={setAlLang}
                  options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))} />
                <SelectField label="输出格式" value={alFormat} onChange={(v) => setAlFormat(v as 'timeline' | 'srt')}
                  options={[{ value: 'srt', label: 'SRT' }, { value: 'timeline', label: 'Timeline' }]} />
              </div>
              <Button onClick={runAlign} disabled={!alAudio.trim() || !alScript.trim() || alBusy} className="w-full">
                {alBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlignLeft className="w-4 h-4" />}
                {alBusy ? '对齐中…' : '开始对齐'}
              </Button>
              {alOut && <Pre>{alOut}</Pre>}
            </OpCard>
          </div>

          {/* SRT import → clips */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<FileUp className="w-4 h-4" />} title="SRT 导入 → Timeline clips" code="subtitle.importSrt">
              <Field label="SRT 内容">
                <textarea value={impSrt} onChange={(e) => setImpSrt(e.target.value)} rows={4} placeholder={'1\n00:00:00,000 --> 00:00:02,500\n你好，世界\n'} className={cn(inputCls, 'resize-y font-mono text-caption')} />
              </Field>
              <Button onClick={runImport} disabled={!impSrt.trim() || impBusy} variant="outline" className="w-full">
                {impBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />}
                {impBusy ? '解析中…' : '导入 SRT'}
              </Button>
              {imported && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-label-sm text-track-audio">
                      <Check className="w-3.5 h-3.5" /> 解析 {imported.segments} 段字幕
                    </span>
                    <Button size="sm" variant="outline" onClick={fillExportFromImport}>填入导出</Button>
                  </div>
                  <ClipsTable clips={imported.clips} />
                </div>
              )}
            </OpCard>
          </div>

          {/* clips → SRT export */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<FileDown className="w-4 h-4" />} title="clips → SRT 导出" code="subtitle.exportSrt">
              <Field label="clips (JSON)">
                <textarea value={expClips} onChange={(e) => setExpClips(e.target.value)} rows={4}
                  placeholder={JSON.stringify([{ text: '你好，世界', start_sec: 0, end_sec: 2.5 }], null, 2)}
                  className={cn(inputCls, 'resize-y font-mono text-caption')} />
              </Field>
              <Button onClick={runExport} disabled={!expClips.trim() || expBusy} variant="outline" className="w-full">
                {expBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileDown className="w-4 h-4" />}
                {expBusy ? '导出中…' : '导出 SRT'}
              </Button>
              {expOut && <Pre>{expOut}</Pre>}
            </OpCard>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-12 gap-5">
          {/* stt transcribe */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<Mic className="w-4 h-4" />} title="STT 转写 · 带词级时间戳" code="stt.transcribe">
              <Field label="音频/视频路径">
                <input value={sttAudio} onChange={(e) => setSttAudio(e.target.value)} placeholder="/data/audio/demo.mp3"
                  className={inputCls} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <SelectField label="语言" value={sttLang} onChange={setSttLang}
                  options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))} />
                <SelectField label="模型" value={sttModel} onChange={setSttModel}
                  options={MODEL_SIZES.map((m) => ({ value: m, label: m }))} />
              </div>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={sttWords} onChange={(e) => setSttWords(e.target.checked)}
                  className="accent-primary w-3.5 h-3.5 cursor-pointer" />
                <span className="text-label text-on-surface-variant">输出词级时间戳</span>
              </label>
              <Button onClick={runSttTranscribe} disabled={!sttAudio.trim() || sttBusy} className="w-full">
                {sttBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mic className="w-4 h-4" />}
                {sttBusy ? '转写中…' : '开始 STT 转写'}
              </Button>
              {sttOut && <SegmentsTable segments={sttOut} />}
            </OpCard>
          </div>

          {/* stt align */}
          <div className="col-span-12 lg:col-span-6">
            <OpCard icon={<AlignLeft className="w-4 h-4" />} title="STT 对齐 · 文本 + 音频" code="stt.align">
              <Field label="音频/视频路径">
                <input value={staAudio} onChange={(e) => setStaAudio(e.target.value)} placeholder="/data/audio/demo.mp3"
                  className={inputCls} />
              </Field>
              <Field label="转写文本 (transcript_text)">
                <textarea value={staText} onChange={(e) => setStaText(e.target.value)} rows={4}
                  placeholder="与音频对应的逐句/逐段文本…" className={cn(inputCls, 'resize-y')} />
              </Field>
              <SelectField label="语言" value={staLang} onChange={setStaLang}
                options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))} />
              <Button onClick={runSttAlign} disabled={!staAudio.trim() || !staText.trim() || staBusy} className="w-full">
                {staBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlignLeft className="w-4 h-4" />}
                {staBusy ? '对齐中…' : '开始 STT 对齐'}
              </Button>
              {staOut && <SegmentsTable segments={staOut} />}
            </OpCard>
          </div>
        </div>
      )}
    </ConsoleShell>
  );
}

/* ── shared presentational helpers ── */

function OpCard({ icon, title, code, children }: { icon: React.ReactNode; title: string; code: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden mb-5">
      <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
        <span className="text-primary">{icon}</span>
        <span className="text-label-sm font-medium text-on-surface">{title}</span>
        <span className="ml-auto font-mono text-caption text-on-surface-variant/60">{code}</span>
      </header>
      <div className="p-4 space-y-3">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-label text-on-surface-variant mb-1.5">{label}</label>
      {children}
    </div>
  );
}

function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <div>
      <label className="block text-label text-on-surface-variant mb-1.5">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary cursor-pointer">
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2.5 font-mono text-caption
      text-track-audio leading-relaxed max-h-56 overflow-auto whitespace-pre-wrap">{children}</pre>
  );
}

function ClipsTable({ clips }: { clips: SubtitleClip[] }) {
  return (
    <div className="max-h-48 overflow-auto rounded-cw-sm border border-outline-variant/30">
      <table className="w-full text-left">
        <thead className="sticky top-0 bg-surface-container-high">
          <tr className="text-caption text-on-surface-variant">
            <th className="px-2.5 py-1.5 font-mono">#</th>
            <th className="px-2.5 py-1.5 font-mono">时间轴</th>
            <th className="px-2.5 py-1.5">文本</th>
          </tr>
        </thead>
        <tbody>
          {clips.map((c, i) => (
            <tr key={i} className="border-t border-outline-variant/15 text-body-sm">
              <td className="px-2.5 py-1.5 font-mono text-caption text-on-surface-variant">{i + 1}</td>
              <td className="px-2.5 py-1.5 font-mono text-caption text-on-surface-variant whitespace-nowrap">
                {fmtTime(c.start_sec)} → {fmtTime(c.end_sec)}
              </td>
              <td className="px-2.5 py-1.5 text-on-surface truncate max-w-[220px]">{c.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SegmentsTable({ segments }: { segments: SttSegment[] }) {
  if (segments.length === 0) {
    return (
      <div className="bg-error/10 border border-error/30 rounded-cw-sm px-3.5 py-2">
        <span className="font-mono text-caption text-error">// 无结果（后端可能离线或返回空）</span>
      </div>
    );
  }
  return (
    <div className="max-h-56 overflow-auto rounded-cw-sm border border-outline-variant/30">
      <table className="w-full text-left">
        <thead className="sticky top-0 bg-surface-container-high">
          <tr className="text-caption text-on-surface-variant">
            <th className="px-2.5 py-1.5 font-mono">#</th>
            <th className="px-2.5 py-1.5 font-mono">时间轴</th>
            <th className="px-2.5 py-1.5">文本</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((s, i) => (
            <tr key={i} className="border-t border-outline-variant/15 text-body-sm">
              <td className="px-2.5 py-1.5 font-mono text-caption text-on-surface-variant">{i + 1}</td>
              <td className="px-2.5 py-1.5 font-mono text-caption text-on-surface-variant whitespace-nowrap">
                {fmtTime(s.start)} → {fmtTime(s.end)}
              </td>
              <td className="px-2.5 py-1.5 text-on-surface">{s.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatSubtitleResult(success: boolean, res: unknown): string {
  if (!success) return `// 失败\n${JSON.stringify(res, null, 2)}`;
  const o = (res ?? {}) as { srt?: string };
  const srt = o.srt;
  if (srt) return srt;
  return JSON.stringify(res, null, 2);
}

function fmtTime(sec: number | undefined): string {
  if (sec === undefined || Number.isNaN(sec)) return '--:--';
  const s = Math.max(0, Math.round(sec * 1000) / 1000);
  const m = Math.floor(s / 60);
  const ss = (s % 60).toFixed(3).padStart(6, '0');
  return `${String(m).padStart(2, '0')}:${ss}`;
}
