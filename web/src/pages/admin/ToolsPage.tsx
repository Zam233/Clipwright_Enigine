import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { toolApi, skillApi } from '@/services/api';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Wrench, Sparkles, Terminal, Loader2, ChevronRight, ListOrdered } from 'lucide-react';

interface ToolItem { name: string; description?: string; }

/**
 * ToolsPage — browse & invoke the engine's atomic Tools and composite Skills.
 */
export function ToolsPage() {
  const [tab, setTab] = useState<'tool' | 'skill'>('tool');
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [skills, setSkills] = useState<ToolItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [params, setParams] = useState('{}');
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [batchCalls, setBatchCalls] = useState('[{"name":"video_trim","params":{}}]');

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [t, s] = await Promise.all([toolApi.list(), skillApi.list()]);
        if (!alive) return;
        setTools(normalize(t));
        setSkills(normalize(s));
      } catch {
        if (!alive) return;
        setTools(DEMO_TOOLS);
        setSkills(DEMO_SKILLS);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const list = tab === 'tool' ? tools : skills;

  const execute = async () => {
    if (batchMode) {
      // 批量模式：JSON 数组 [ {name, params} ] → toolApi.batch（顺序执行，互不影响）
      setRunning(true);
      setOutput(null);
      try {
        const parsed = JSON.parse(batchCalls || '[]');
        if (!Array.isArray(parsed)) throw new Error('批处理必须是 JSON 数组');
        const calls = parsed.map((c, i) => {
          const name = typeof c === 'string' ? c : String((c as Record<string, unknown> | null)?.name ?? '');
          if (!name) throw new Error(`第 ${i + 1} 项缺少 "name"`);
          const params = (c as Record<string, unknown> | null)?.params;
          return { name, params: (params && typeof params === 'object' ? params as Record<string, unknown> : {}) };
        });
        const res = await toolApi.batch(calls);
        setOutput(JSON.stringify(res, null, 2));
      } catch (e: unknown) {
        setOutput(`// 批处理失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setRunning(false);
      }
      return;
    }
    if (!selected) return;
    setRunning(true);
    setOutput(null);
    try {
      let parsed: Record<string, unknown> = {};
      try { parsed = JSON.parse(params || '{}'); } catch { /* keep empty */ }
      const res = tab === 'tool'
        ? await toolApi.execute(selected, parsed)
        : await skillApi.execute(selected, parsed);
      setOutput(JSON.stringify(res, null, 2));
    } catch (e: unknown) {
      setOutput(`// 执行失败（后端可能离线）\n${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Capabilities / Tools & Skills" title="工具与技能"
        desc="工具是原子能力（FFmpeg/OpenCV 封装），技能是多工具组合。选择一项并传入 JSON 参数即可执行。" />

      {/* tab switch */}
      <div className="flex gap-1 mb-5 bg-surface-container rounded-cw-sm p-1 w-fit border border-outline-variant/30">
        {(['tool', 'skill'] as const).map((t) => (
          <button key={t} onClick={() => { setTab(t); setSelected(null); setOutput(null); }}
            className={cn('flex items-center gap-1.5 px-4 py-1.5 rounded-cw-xs text-body-sm font-medium transition-colors cursor-pointer',
              tab === t ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface')}>
            {t === 'tool' ? <Wrench className="w-3.5 h-3.5" /> : <Sparkles className="w-3.5 h-3.5" />}
            {t === 'tool' ? `工具 (${tools.length})` : `技能 (${skills.length})`}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* list */}
        <div className="col-span-12 lg:col-span-5">
          {loading ? (
            <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-12 bg-surface-container rounded-cw-sm animate-pulse" />)}</div>
          ) : (
            <div className="space-y-1.5 max-h-[480px] overflow-y-auto pr-1">
              {list.map((item) => (
                <button key={item.name} onClick={() => { setSelected(item.name); setOutput(null); }}
                  className={cn('w-full flex items-center gap-3 px-3.5 py-2.5 rounded-cw-sm border text-left transition-all duration-short3 cursor-pointer group',
                    selected === item.name
                      ? 'border-primary/50 bg-primary/10'
                      : 'border-outline-variant/25 bg-surface-container hover:border-outline/50')}>
                  <span className={cn('w-7 h-7 rounded-cw-xs flex items-center justify-center shrink-0',
                    selected === item.name ? 'bg-primary text-on-primary' : 'bg-surface-container-high text-on-surface-variant')}>
                    {tab === 'tool' ? <Wrench className="w-3.5 h-3.5" /> : <Sparkles className="w-3.5 h-3.5" />}
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className={cn('block font-mono text-body-sm truncate', selected === item.name ? 'text-primary' : 'text-on-surface')}>{item.name}</span>
                    {item.description && <span className="block text-caption text-on-surface-variant truncate">{item.description}</span>}
                  </span>
                  <ChevronRight className={cn('w-4 h-4 shrink-0 transition-transform', selected === item.name ? 'text-primary translate-x-0.5' : 'text-on-surface-variant/40')} />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* executor */}
        <div className="col-span-12 lg:col-span-7">
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
              <Terminal className="w-4 h-4 text-primary" />
              <span className="font-mono text-label-sm text-on-surface">{selected ? `${tab}.execute("${selected}")` : '执行器'}</span>
            </div>
            <div className="p-4 space-y-3">
              {/* mode toggle: single vs batch */}
              <div className="flex gap-1 w-fit bg-surface rounded-cw-sm p-0.5 border border-outline-variant/30">
                <button onClick={() => setBatchMode(false)}
                  className={cn('flex items-center gap-1 px-3 py-1 rounded-cw-xs text-label-sm font-medium transition-colors cursor-pointer',
                    !batchMode ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface')}>
                  <Terminal className="w-3 h-3" />单个
                </button>
                <button onClick={() => setBatchMode(true)}
                  className={cn('flex items-center gap-1 px-3 py-1 rounded-cw-xs text-label-sm font-medium transition-colors cursor-pointer',
                    batchMode ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface')}>
                  <ListOrdered className="w-3 h-3" />批量
                </button>
              </div>

              {batchMode ? (
                <div>
                  <label className="block text-label text-on-surface-variant mb-1.5">批量调用 (JSON 数组)</label>
                  <textarea value={batchCalls} onChange={(e) => setBatchCalls(e.target.value)} rows={5}
                    className="w-full bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary resize-y"
                    placeholder='[{"name":"video_trim","params":{}},{"name":"scene_detect","params":{}}]' />
                  <p className="text-caption text-on-surface-variant/60 mt-1">顺序批量执行、互不影响。每项需含 "name"，"params" 可选。</p>
                </div>
              ) : (
                <div>
                  <label className="block text-label text-on-surface-variant mb-1.5">参数 (JSON)</label>
                  <textarea value={params} onChange={(e) => setParams(e.target.value)} rows={4}
                    className="w-full bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary resize-y" />
                </div>
              )}
              <Button onClick={execute} disabled={(batchMode ? false : !selected) || running} className="w-full">
                {running ? <Loader2 className="w-4 h-4 animate-spin" /> : batchMode ? <ListOrdered className="w-4 h-4" /> : <Terminal className="w-4 h-4" />}
                {running ? '执行中…' : batchMode ? '批量执行' : '执行'}
              </Button>
              {output && (
                <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2.5 font-mono text-caption
                  text-track-audio leading-relaxed max-h-56 overflow-auto whitespace-pre-wrap">{output}</pre>
              )}
            </div>
          </div>
        </div>
      </div>
    </ConsoleShell>
  );
}

function normalize(data: unknown): ToolItem[] {
  if (Array.isArray(data)) {
    return data.map((d) => {
      if (typeof d === 'string') return { name: d };
      const o = d as Record<string, unknown>;
      return { name: String(o.name ?? o.id ?? 'unknown'), description: o.description ? String(o.description) : undefined };
    });
  }
  if (data && typeof data === 'object') {
    const o = data as Record<string, unknown>;
    const arr = o.tools ?? o.skills ?? o.items;
    if (Array.isArray(arr)) return normalize(arr);
  }
  return [];
}

const DEMO_TOOLS: ToolItem[] = [
  { name: 'video_trim', description: '视频裁剪' }, { name: 'video_concat', description: '视频拼接' },
  { name: 'video_speed', description: '变速播放' }, { name: 'audio_mix', description: '多轨混音' },
  { name: 'scene_detect', description: '场景检测' }, { name: 'subtitle_burn', description: '字幕烧录' },
  { name: 'whisper_transcribe', description: '语音转文字' }, { name: 'text_to_speech', description: '文字转语音' },
];
const DEMO_SKILLS: ToolItem[] = [
  { name: 'auto_caption', description: '自动字幕（转写→拆分→同步）' },
  { name: 'broll_matcher', description: 'B-roll 匹配' },
  { name: 'voiceover_sync', description: '配音同步+闪避' },
  { name: 'background_music', description: 'BGM 匹配' },
  { name: 'silence_cut', description: '静音切除' },
];
