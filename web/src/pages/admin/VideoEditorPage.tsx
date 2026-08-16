import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { videoEditorApi, proxyApi, waveformApi } from '@/services/api';
import type { EditorProject, EditorProjectSummary } from '@/services/api/videoEditor';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  FolderOpen, Plus, Trash2, Save, Undo2, Redo2, Download, RefreshCw,
  Scissors, Move, Waves, FileCode, Loader2, Film, Boxes, GitCompareArrows,
} from 'lucide-react';

interface BackendStatus {
  status: string;
  projects: number;
  storage_dir: string;
}

const EMPTY_TIMELINE = (): Record<string, unknown> => ({
  version: 1,
  fps: 30,
  duration_sec: 10,
  tracks: [
    {
      id: 'track-1',
      kind: 'video',
      name: '视频轨道',
      clips: [
        {
          id: 'clip-1',
          kind: 'video',
          label: '片段 1',
          start_sec: 0,
          duration_sec: 5,
          source: '',
        },
      ],
    },
  ],
});

/**
 * VideoEditorPage — operations console for the backend video-editor service:
 * project CRUD / load-save / undo-redo / clip ops / export, plus integrated
 * proxy (generate/switch) and waveform generation controls.
 */
export function VideoEditorPage() {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [projects, setProjects] = useState<EditorProjectSummary[]>([]);
  const [current, setCurrent] = useState<EditorProject | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const [createName, setCreateName] = useState('新项目');
  const [trackIndex, setTrackIndex] = useState('0');
  const [clipIndex, setClipIndex] = useState('0');
  const [positionSec, setPositionSec] = useState('0');
  const [splitAtSec, setSplitAtSec] = useState('1');
  const [clipData, setClipData] = useState(
    '{\n  "id": "clip-2",\n  "kind": "video",\n  "label": "片段 2",\n  "start_sec": 5,\n  "duration_sec": 4,\n  "source": ""\n}',
  );

  const [proxyPath, setProxyPath] = useState('');
  const [proxyHeight, setProxyHeight] = useState('720');
  const [waveformPath, setWaveformPath] = useState('');
  const [exportFormat, setExportFormat] = useState<'json' | 'edl' | 'fcpxml'>('json');
  const [exportFps, setExportFps] = useState('30');

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await fn();
      setResult(`${label} → ${JSON.stringify(res, null, 2)}`);
    } catch (e: unknown) {
      setError(`${label} 失败（后端可能离线）：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [st, list] = await Promise.all([videoEditorApi.status(), videoEditorApi.listProjects()]);
      setStatus(st);
      setProjects(list);
    } catch (e: unknown) {
      setError(`连接 video-editor 服务失败（后端可能离线）：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const loadProject = async (id: string) => {
    await run('加载项目', async () => {
      const p = await videoEditorApi.getProject(id);
      setCurrent(p);
      return { project_id: p.project_id, name: p.name, version: p.version };
    });
  };

  const createProject = async () => {
    await run('创建项目', async () => {
      const p = await videoEditorApi.createProject({
        name: createName.trim() || '新项目',
        timeline: EMPTY_TIMELINE(),
        metadata: {},
      });
      setCurrent(p);
      setProjects(await videoEditorApi.listProjects());
      return { project_id: p.project_id, name: p.name };
    });
  };

  const saveProject = async () => {
    if (!current) return;
    await run('保存项目', async () => {
      const p = await videoEditorApi.saveProject(current.project_id, {
        name: current.name,
        timeline: current.timeline,
        metadata: current.metadata,
      });
      setCurrent(p);
      setProjects(await videoEditorApi.listProjects());
      return { project_id: p.project_id, version: p.version };
    });
  };

  const deleteProject = async (id: string, name: string) => {
    if (!window.confirm(`确定删除项目「${name}」？此操作不可撤销。`)) return;
    await run('删除项目', async () => {
      const res = await videoEditorApi.deleteProject(id);
      if (current?.project_id === id) setCurrent(null);
      setProjects(await videoEditorApi.listProjects());
      return res;
    });
  };

  const undo = async () => {
    if (!current) return;
    await run('撤销', async () => {
      const res = await videoEditorApi.undo(current.project_id);
      if (res.timeline) setCurrent({ ...current, timeline: res.timeline });
      return res;
    });
  };

  const redo = async () => {
    if (!current) return;
    await run('重做', async () => {
      const res = await videoEditorApi.redo(current.project_id);
      if (res.timeline) setCurrent({ ...current, timeline: res.timeline });
      return res;
    });
  };

  const parseClipData = (): Record<string, unknown> => {
    try {
      const v = JSON.parse(clipData || '{}');
      return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  };

  const addClip = async () => {
    if (!current) return;
    await run('添加片段', async () => {
      const res = await videoEditorApi.addClip(current.project_id, {
        track_index: toInt(trackIndex),
        position_sec: toFloat(positionSec),
        clip_data: parseClipData(),
      });
      await reloadCurrent();
      return res;
    });
  };

  const removeClip = async () => {
    if (!current) return;
    await run('删除片段', async () => {
      const res = await videoEditorApi.removeClip(current.project_id, {
        track_index: toInt(trackIndex),
        clip_index: toInt(clipIndex),
      });
      await reloadCurrent();
      return res;
    });
  };

  const moveClip = async () => {
    if (!current) return;
    await run('移动片段', async () => {
      const res = await videoEditorApi.moveClip(current.project_id, {
        track_index: toInt(trackIndex),
        clip_index: toInt(clipIndex),
        position_sec: toFloat(positionSec),
      });
      await reloadCurrent();
      return res;
    });
  };

  const splitClip = async () => {
    if (!current) return;
    await run('分割片段', async () => {
      const res = await videoEditorApi.splitClip(current.project_id, {
        track_index: toInt(trackIndex),
        clip_index: toInt(clipIndex),
        split_at_sec: toFloat(splitAtSec),
      });
      await reloadCurrent();
      return res;
    });
  };

  const reloadCurrent = async () => {
    if (!current) return;
    try {
      setCurrent(await videoEditorApi.getProject(current.project_id));
    } catch {
      /* keep last known state if refresh fails */
    }
  };

  const exportProject = async () => {
    if (!current) return;
    await run(`导出（${exportFormat}）`, () =>
      videoEditorApi.export(current.project_id, { format: exportFormat, fps: toFloat(exportFps) || 30 }),
    );
  };

  const generateProxy = async () => {
    await run('生成代理', () => proxyApi.generate(proxyPath, toInt(proxyHeight) || 720));
  };

  const switchProxy = async (mode: 'proxy' | 'full') => {
    if (!current) return;
    await run(mode === 'proxy' ? '切换为代理' : '切回原片', async () => {
      const tl = mode === 'proxy'
        ? await proxyApi.switchToProxy(current.timeline)
        : await proxyApi.switchToFull(current.timeline);
      setCurrent({ ...current, timeline: tl });
      return tl;
    });
  };

  const generateWaveform = async () => {
    await run('生成波形', () => waveformApi.generate(waveformPath, 200));
  };

  const stats = timelineStats(current?.timeline);
  const canEdit = Boolean(current) && !busy;

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Operations / Video Editor" title="视频编辑器控制台"
        desc="后端 video-editor 服务：项目/会话 CRUD、undo/redo、clips 增删移分、时间线导出，以及集成的 proxy（生成/切换）与波形生成控件。" />

      {/* status strip */}
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <StatusPill ok={status?.status === 'ok'} label={status?.status === 'ok' ? 'EDITOR:OK' : 'EDITOR:OFFLINE'} />
        <span className="font-mono text-caption text-on-surface-variant">PROJECTS {status?.projects ?? '—'}</span>
        <span className="font-mono text-caption text-on-surface-variant truncate hidden md:inline">{status?.storage_dir ?? 'storage: —'}</span>
        <div className="ml-auto">
          <Button size="sm" variant="outline" onClick={refresh} disabled={busy}>
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            刷新
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* left — session / project list */}
        <div className="col-span-12 lg:col-span-4 xl:col-span-3">
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
              <FolderOpen className="w-4 h-4 text-primary" />
              <span className="font-mono text-label-sm text-on-surface">SESSIONS / {projects.length}</span>
            </header>

            {/* create */}
            <div className="p-3 border-b border-outline-variant/20 space-y-2">
              <input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="项目名称"
                className="w-full bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface
                  outline-none border border-outline-variant/30 focus:border-primary"
              />
              <Button size="sm" onClick={createProject} disabled={busy} className="w-full">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                创建新项目
              </Button>
            </div>

            {/* list */}
            <div className="max-h-[440px] overflow-y-auto p-2 space-y-1">
              {loading ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-11 bg-surface rounded-cw-xs animate-pulse" />
                ))
              ) : projects.length === 0 ? (
                <p className="text-caption text-on-surface-variant px-3 py-4">暂无项目，点击上方创建。</p>
              ) : (
                projects.map((p) => {
                  const active = current?.project_id === p.project_id;
                  return (
                    <div key={p.project_id}
                      className={cn('flex items-center gap-2 px-2.5 py-2 rounded-cw-xs border transition-all duration-short3',
                        active ? 'border-primary/40 bg-primary/10' : 'border-transparent hover:bg-surface-container')}>
                      <button onClick={() => loadProject(p.project_id)} disabled={busy}
                        className="flex-1 min-w-0 text-left cursor-pointer group">
                        <span className={cn('block text-body-sm font-medium truncate', active ? 'text-primary' : 'text-on-surface group-hover:text-primary')}>
                          {p.name}
                        </span>
                        <span className="block font-mono text-caption text-on-surface-variant truncate">
                          v{p.version} · {p.updated_at}
                        </span>
                      </button>
                      <button onClick={() => deleteProject(p.project_id, p.name)} disabled={busy}
                        title="删除项目"
                        className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors cursor-pointer shrink-0">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* right — editor ops */}
        <div className="col-span-12 lg:col-span-8 xl:col-span-9 space-y-5 min-w-0">
          {error && (
            <div className="bg-error/10 border border-error/40 rounded-cw-md px-4 py-3 font-mono text-caption text-error whitespace-pre-wrap">
              {error}
            </div>
          )}

          {/* project toolbar */}
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
              <Film className="w-4 h-4 text-primary" />
              <span className="font-mono text-label-sm text-on-surface truncate">
                {current ? `PROJECT · ${current.project_id} · v${current.version}` : 'PROJECT · NONE'}
              </span>
            </header>
            <div className="p-4">
              {!current ? (
                <p className="text-body-sm text-on-surface-variant">从左侧选择一个项目，或创建新项目开始操作。</p>
              ) : (
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={saveProject} disabled={busy}>
                      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      保存
                    </Button>
                    <Button size="sm" variant="outline" onClick={undo} disabled={busy}>
                      <Undo2 className="w-3.5 h-3.5" /> 撤销
                    </Button>
                    <Button size="sm" variant="outline" onClick={redo} disabled={busy}>
                      <Redo2 className="w-3.5 h-3.5" /> 重做
                    </Button>
                    <div className="flex items-center gap-1.5 ml-auto">
                      <select value={exportFormat} onChange={(e) => setExportFormat(e.target.value as typeof exportFormat)}
                        className="bg-surface rounded-cw-xs px-2 py-1.5 text-label-sm font-mono text-on-surface
                          outline-none border border-outline-variant/30 focus:border-primary">
                        <option value="json">json</option>
                        <option value="edl">edl</option>
                        <option value="fcpxml">fcpxml</option>
                      </select>
                      <input value={exportFps} onChange={(e) => setExportFps(e.target.value)}
                        title="fps" className="w-14 bg-surface rounded-cw-xs px-2 py-1.5 text-label-sm font-mono text-on-surface
                          outline-none border border-outline-variant/30 focus:border-primary" />
                      <Button size="sm" variant="outline" onClick={exportProject} disabled={busy}>
                        <Download className="w-3.5 h-3.5" /> 导出
                      </Button>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-3 font-mono text-caption text-on-surface-variant">
                    <span>TRACKS <b className="text-on-surface">{stats.tracks}</b></span>
                    <span>CLIPS <b className="text-on-surface">{stats.clips}</b></span>
                    <span>DURATION <b className="text-on-surface">{stats.durationSec}s</b></span>
                  </div>

                  <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2.5 font-mono text-caption
                    text-track-audio leading-relaxed max-h-44 overflow-auto whitespace-pre-wrap">{JSON.stringify(current.timeline, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>

          {/* clip operations */}
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
              <Scissors className="w-4 h-4 text-primary" />
              <span className="font-mono text-label-sm text-on-surface">CLIP OPS · add / remove / move / split</span>
            </header>
            <div className="p-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                <NumberField label="track_index" value={trackIndex} onChange={setTrackIndex} />
                <NumberField label="clip_index" value={clipIndex} onChange={setClipIndex} />
                <NumberField label="position_sec" value={positionSec} onChange={setPositionSec} />
                <NumberField label="split_at_sec" value={splitAtSec} onChange={setSplitAtSec} />
              </div>
              <div className="mb-3">
                <label className="block text-label text-on-surface-variant mb-1.5">clip_data (JSON, 用于 add)</label>
                <textarea value={clipData} onChange={(e) => setClipData(e.target.value)} rows={4}
                  className="w-full bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
                    outline-none border border-outline-variant/30 focus:border-primary resize-y" />
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <Button size="sm" onClick={addClip} disabled={!canEdit}>
                  <Plus className="w-3.5 h-3.5" /> 添加
                </Button>
                <Button size="sm" variant="outline" onClick={removeClip} disabled={!canEdit}>
                  <Trash2 className="w-3.5 h-3.5" /> 删除
                </Button>
                <Button size="sm" variant="outline" onClick={moveClip} disabled={!canEdit}>
                  <Move className="w-3.5 h-3.5" /> 移动
                </Button>
                <Button size="sm" variant="outline" onClick={splitClip} disabled={!canEdit}>
                  <Scissors className="w-3.5 h-3.5" /> 分割
                </Button>
              </div>
              {!current && <p className="mt-3 text-caption text-on-surface-variant">需先选择项目。</p>}
            </div>
          </div>

          {/* proxy + waveform */}
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
              <GitCompareArrows className="w-4 h-4 text-primary" />
              <span className="font-mono text-label-sm text-on-surface">PROXY / WAVEFORM</span>
            </header>
            <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* proxy */}
              <div className="space-y-3">
                <label className="block text-label text-on-surface-variant mb-1.5">Proxy 生成（低清代理）</label>
                <div className="flex gap-2">
                  <input value={proxyPath} onChange={(e) => setProxyPath(e.target.value)}
                    placeholder="input_path 高清单条路径"
                    className="flex-1 min-w-0 bg-surface rounded-cw-sm px-3 py-2 text-body-sm font-mono text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary" />
                  <input value={proxyHeight} onChange={(e) => setProxyHeight(e.target.value)}
                    title="proxy_height" className="w-16 bg-surface rounded-cw-sm px-2 py-2 text-body-sm font-mono text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary" />
                </div>
                <Button size="sm" onClick={generateProxy} disabled={busy || !proxyPath.trim()}>
                  <Boxes className="w-3.5 h-3.5" /> 生成代理
                </Button>

                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="outline" onClick={() => switchProxy('proxy')} disabled={!canEdit}>
                    <Film className="w-3.5 h-3.5" /> 切换为代理
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => switchProxy('full')} disabled={!canEdit}>
                    <RefreshCw className="w-3.5 h-3.5" /> 切回原片
                  </Button>
                </div>
                <p className="text-caption text-on-surface-variant">switch 控件会改写当前已加载项目的时间线素材路径。</p>
              </div>

              {/* waveform */}
              <div className="space-y-3">
                <label className="block text-label text-on-surface-variant mb-1.5">Waveform 生成</label>
                <input value={waveformPath} onChange={(e) => setWaveformPath(e.target.value)}
                  placeholder="audio_path 音频路径"
                  className="w-full bg-surface rounded-cw-sm px-3 py-2 text-body-sm font-mono text-on-surface
                    outline-none border border-outline-variant/30 focus:border-primary" />
                <Button size="sm" onClick={generateWaveform} disabled={busy || !waveformPath.trim()}>
                  <Waves className="w-3.5 h-3.5" /> 生成波形
                </Button>
                <p className="text-caption text-on-surface-variant">返回 200 个采样峰值的波形数据。</p>
              </div>
            </div>
          </div>

          {/* result log */}
          {result && (
            <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
              <header className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
                <FileCode className="w-4 h-4 text-track-audio" />
                <span className="font-mono text-label-sm text-on-surface">RESPONSE</span>
              </header>
              <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 mx-4 my-4 px-3 py-2.5 font-mono text-caption
                text-track-audio leading-relaxed max-h-72 overflow-auto whitespace-pre-wrap">{result}</pre>
            </div>
          )}
        </div>
      </div>
    </ConsoleShell>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-label text-on-surface-variant mb-1.5">{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-surface rounded-cw-sm px-3 py-2 text-body-sm font-mono text-on-surface
          outline-none border border-outline-variant/30 focus:border-primary" />
    </div>
  );
}

function toInt(v: string): number {
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : 0;
}

function toFloat(v: string): number {
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function timelineStats(timeline: Record<string, unknown> | undefined): { tracks: number; clips: number; durationSec: number } {
  const tracks = Array.isArray(timeline?.tracks) ? (timeline.tracks as unknown[]) : [];
  let clips = 0;
  for (const t of tracks) {
    const c = (t as Record<string, unknown>).clips;
    if (Array.isArray(c)) clips += c.length;
  }
  const dur = timeline?.duration_sec;
  const durationSec = typeof dur === 'number' ? Math.round(dur * 100) / 100 : 0;
  return { tracks: tracks.length, clips, durationSec };
}
