import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useTimelineStore } from '@/stores/timelineStore';
import { useProjectStore } from '@/stores/projectStore';
import { renderApi, projectApi, assetApi, toolApi } from '@/services/api';
import { fetchSseToken, withSseToken } from '@/services/api/sse';
import { toast } from '@/stores/toastStore';
import { createEmptyTimeline } from '@/types/timeline';
import { StandardLayout } from '@/layouts/StandardLayout';
import { Button, Badge } from '@/components/ui';
import { uid, formatTimecode } from '@/lib/utils';
import type { ExportSettings, RenderProgress } from '@/types/api';
import {
  Download, Clapperboard, Gauge, Cpu, Film, Loader2, CheckCircle2,
  XCircle, ArrowLeft, HardDrive, Zap, RotateCcw, Wand2,
} from 'lucide-react';

interface PresetDef {
  name: string;
  width: number;
  height: number;
  fps: number;
  bitrate: string;
  icon: string;
}

const PRESETS: Record<string, PresetDef> = {
  bilibili: { name: 'Bilibili 1080p', width: 1920, height: 1080, fps: 30, bitrate: '6M', icon: '📺' },
  bilibili_4k: { name: 'Bilibili 4K', width: 3840, height: 2160, fps: 30, bitrate: '20M', icon: '🎞️' },
  youtube: { name: 'YouTube 1080p', width: 1920, height: 1080, fps: 30, bitrate: '8M', icon: '▶️' },
  tiktok: { name: '抖音竖屏', width: 1080, height: 1920, fps: 30, bitrate: '4M', icon: '📱' },
  weibo: { name: '微博 720p', width: 1280, height: 720, fps: 25, bitrate: '3M', icon: '🌐' },
  custom: { name: '自定义', width: 1920, height: 1080, fps: 30, bitrate: '5M', icon: '⚙️' },
};

interface QueueItem extends RenderProgress {
  label: string;
  presetName: string;
  startedAt: string;
  filename?: string;
  output_path?: string;
  simulated?: boolean;
  /** 重试所需的原始提交参数（U14） */
  retrySettings?: ExportSettings;
  retryFilename?: string;
}

/**
 * ExportPage — render console. Preset selection, custom params, and a live
 * render queue driven by SSE progress events.
 */
export function ExportPage() {
  const navigate = useNavigate();
  const { projectId } = useParams({ from: '/export/$projectId' });
  const timeline = useTimelineStore((s) => s.timeline);
  const projectName = useProjectStore((s) => s.projectName);

  // 刷新后 store 会重置：从 URL 的 projectId 重新加载项目，保证导出页始终持有项目上下文
  useEffect(() => {
    let alive = true;
    (async () => {
      const st = useProjectStore.getState();
      if (st.projectId === projectId && useTimelineStore.getState().timeline.tracks.length > 0) return;
      try {
        const project = await projectApi.load(projectId);
        if (!alive) return;
        useProjectStore.getState().setProjectId(project.id);
        useProjectStore.getState().setProjectName(project.name);
        useTimelineStore.getState().setTimeline(project.timeline ?? createEmptyTimeline());
      } catch {
        // 离线或项目不存在：仅设置 id，时间轴保持当前（可能为空）
        if (alive) useProjectStore.getState().setProjectId(projectId);
      }
    })();
    return () => { alive = false; };
  }, [projectId]);

  const [presetId, setPresetId] = useState('bilibili');
  const [settings, setSettings] = useState<ExportSettings>({
    preset: 'bilibili', width: 1920, height: 1080, fps: 30, bitrate: '6M',
  });
  // W11: BGM 素材源 — 从素材库选音频作为背景音乐
  const [bgmPath, setBgmPath] = useState('');
  const [audioAssets, setAudioAssets] = useState<{ id: string; name: string; path: string }[]>([]);
  useEffect(() => {
    let alive = true;
    assetApi.list(useProjectStore.getState().projectId ?? undefined)
      .then((list) => {
        if (!alive) return;
        const audios = (Array.isArray(list) ? list : [])
          .filter((a) => a.kind === 'audio')
          .map((a) => ({ id: a.id, name: a.filename || a.id, path: a.path || a.id }));
        setAudioAssets(audios);
      })
      .catch(() => { /* 离线：无 BGM 可选 */ });
    return () => { alive = false; };
  }, []);
  // C6: 自定义导出预设（localStorage）
  const [savedPresets, setSavedPresets] = useState<{ name: string; width: number; height: number; fps: number; bitrate: string }[]>(() => {
    try {
      const raw = localStorage.getItem('cw_export_presets');
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });
  const [presetName, setPresetName] = useState('');
  const [apiPresets, setApiPresets] = useState<Record<string, Partial<PresetDef>> | null>(null);
  const [loadingPresets, setLoadingPresets] = useState(true);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const esRefs = useRef<Map<string, EventSource>>(new Map());

  useEffect(() => {
    renderApi.getPresets()
      .then((presets) => {
        if (presets && typeof presets === 'object' && !Array.isArray(presets)) {
          setApiPresets(presets);
        }
      })
      .catch(() => {
        setApiPresets(null);
      })
      .finally(() => setLoadingPresets(false));
  }, []);

  // 刷新后从后端恢复在途渲染任务并重新挂接进度流
  useEffect(() => {
    let alive = true;
    renderApi.listQueue()
      .then((tasks) => {
        if (!alive) return;
        const active = tasks.filter((t) => t.status === 'queued' || t.status === 'rendering');
        if (active.length === 0) return;
        setQueue((q) => {
          const known = new Set(q.map((it) => it.task_id));
          const restored: QueueItem[] = active
            .filter((t) => !known.has(t.task_id))
            .map((t) => {
              // U18: 尽量从后端任务的文件名恢复原始项目名与预设信息
              const parsed = parseRestoredTask(t);
              return {
                task_id: t.task_id,
                status: t.status,
                progress: t.progress ?? 0,
                label: parsed.label,
                presetName: parsed.presetName,
                startedAt: new Date().toLocaleTimeString(),
                filename: parsed.filename,
              };
            });
          return [...restored, ...q];
        });
        active.forEach((t) => openSSE(t.task_id));
      })
      .catch(() => { /* offline：跳过恢复 */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 合并后端预设与内置预设；后端字段缺失时回落到内置默认值，避免 NaN/undefined 设置
  const presets = useMemo(() => {
    if (!apiPresets || Object.keys(apiPresets).length === 0) return PRESETS;
    const merged: Record<string, PresetDef> = { ...PRESETS };
    for (const [id, p] of Object.entries(apiPresets)) {
      // 过滤 undefined 字段：后端预设缺 name/icon 时保留内置预设的值（U15）
      const clean = Object.fromEntries(
        Object.entries(p).filter(([, v]) => v !== undefined),
      ) as Partial<PresetDef>;
      const fallback: PresetDef = {
        name: id, icon: merged[id]?.icon ?? '📦',
        width: 1920, height: 1080, fps: 30, bitrate: '6M',
      };
      merged[id] = { ...(merged[id] ?? fallback), ...clean };
    }
    return merged;
  }, [apiPresets]);

  const applyPreset = (id: string) => {
    const p = presets[id];
    if (!p) return;
    setPresetId(id);
    setSettings({ preset: id, width: p.width, height: p.height, fps: p.fps, bitrate: p.bitrate });
  };

  const submitRender = async () => {
    if (timeline.tracks.length === 0) return;
    setSubmitting(true);
    const taskId = uid('render');
    // 文件名消毒：去掉路径分隔符与非法字符，空名称用默认值兜底
    const safeName = (projectName.trim() || 'project').replace(/[\\/:*?"<>|\s]+/g, '_');
    const filename = `${safeName}_${settings.width}x${settings.height}.mp4`;
    const item: QueueItem = {
      task_id: taskId, status: 'pending', progress: 0,
      label: projectName, presetName: presets[presetId].name,
      startedAt: new Date().toLocaleTimeString(), filename,
      retrySettings: { ...settings }, retryFilename: filename,
    };
    setQueue((q) => [item, ...q]);

    try {
      const res = await renderApi.submitQueue({
        timeline: useTimelineStore.getState().exportTimeline(),
        output_path: `renders/${filename}`,
        settings,
        // W11: BGM 素材源（用户从素材库选择；无则后端走无 BGM 路径）
        ...(bgmPath ? { bgm_file_path: bgmPath } : {}),
      });
      // 后端返回真实 task_id（render_N_ts）；替换本地占位 ID 后再挂接进度流
      const realId = res.task_id ?? taskId;
      if (realId !== taskId) updateQueue(taskId, { task_id: realId });
      openSSE(realId);
    } catch {
      // Offline: simulate render progress — 明确提示这是演示模式（U3）
      toast('后端离线，无法真实渲染，已进入演示模式', 'error');
      simulateRender(taskId);
    } finally {
      setSubmitting(false);
    }
  };

  // U14: 使用原始提交参数重新提交渲染任务
  const retryRender = async (taskId: string) => {
    const item = queue.find((it) => it.task_id === taskId);
    if (!item?.retrySettings || !item.retryFilename) return;
    updateQueue(taskId, { status: 'pending', progress: 0, detail: undefined, phase: undefined, simulated: undefined });
    try {
      const res = await renderApi.submitQueue({
        timeline: useTimelineStore.getState().exportTimeline(),
        output_path: `renders/${item.retryFilename}`,
        settings: item.retrySettings,
      });
      const realId = res.task_id ?? taskId;
      if (realId !== taskId) updateQueue(taskId, { task_id: realId });
      openSSE(realId);
    } catch {
      updateQueue(taskId, { status: 'failed', detail: '重试失败 — 后端不可达' });
      toast('重试失败 — 后端不可达', 'error');
    }
  };

  const openSSE = (taskId: string) => {
    if (esRefs.current.has(taskId)) return;
    // P0-9/10: EventSource 无法带请求头 → 先取一次性 token 再挂接
    void fetchSseToken().then((tok) => {
      if (esRefs.current.has(taskId)) return;
      const es = new EventSource(withSseToken(renderApi.getQueueStreamUrl(taskId), tok));
      esRefs.current.set(taskId, es);
    // 后端发送的是未命名 data 消息（{type: progress/completed/failed/timeout}）
    es.onmessage = (e) => {
      let d: { type?: string; progress?: number; phase?: string; detail?: string; output_path?: string };
      try {
        d = JSON.parse((e as MessageEvent).data);
      } catch {
        return;
      }
      if (d.type === 'progress') {
        updateQueue(taskId, { progress: d.progress ?? 0, phase: d.phase, detail: d.detail, status: 'rendering' });
      } else if (d.type === 'completed') {
        updateQueue(taskId, { status: 'completed', progress: 100, output_path: d.output_path });
        es.close();
        esRefs.current.delete(taskId);
      } else if (d.type === 'failed') {
        updateQueue(taskId, { status: 'failed', detail: d.detail ?? '渲染失败', output_path: d.output_path });
        es.close();
        esRefs.current.delete(taskId);
      } else if (d.type === 'timeout') {
        updateQueue(taskId, { status: 'failed', detail: '进度流超时' });
        es.close();
        esRefs.current.delete(taskId);
      }
    };
    es.onerror = () => {
      // 连接错误：任务若仍显示进行中，标记为失败（避免永远卡在渲染中）；
      // 已完成的流（后端主动关闭）不会走到这里
      es.close();
      esRefs.current.delete(taskId);
      setQueue((q) => q.map((it) =>
        it.task_id === taskId && (it.status === 'pending' || it.status === 'rendering')
          ? { ...it, status: 'failed', detail: '进度流中断，请重试' }
          : it,
      ));
    };
    });
  };

  const simulateTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const simulateRender = (taskId: string) => {
    updateQueue(taskId, { status: 'rendering', progress: 0, simulated: true });
    const phases = [
      { p: 'trim', until: 40 },
      { p: 'concat', until: 70 },
      { p: 'text', until: 88 },
      { p: 'audio', until: 100 },
    ];
    let prog = 0;
    const timer = setInterval(() => {
      prog += 2 + Math.random() * 3;
      const phase = phases.find((ph) => prog <= ph.until) ?? phases[phases.length - 1];
      if (prog >= 100) {
        clearInterval(timer);
        simulateTimers.current.delete(taskId);
        updateQueue(taskId, { status: 'completed', progress: 100, phase: 'done' });
      } else {
        updateQueue(taskId, { status: 'rendering', progress: Math.round(prog), phase: phase.p });
      }
    }, 180);
    simulateTimers.current.set(taskId, timer);
  };

  const updateQueue = (taskId: string, patch: Partial<QueueItem>) => {
    setQueue((q) => q.map((it) => (it.task_id === taskId ? { ...it, ...patch } : it)));
  };

  useEffect(() => () => {
    esRefs.current.forEach((es) => es.close());
    simulateTimers.current.forEach((timer) => clearInterval(timer));
  }, []);

  const timelineEmpty = !timeline.duration_sec || timeline.tracks.length === 0;
  const estSize = timelineEmpty ? '—' : estimateSize(timeline.duration_sec, settings.bitrate);

  return (
    <StandardLayout title="导出与渲染">
      <button
        onClick={() => navigate({ to: '/editor/$projectId', params: { projectId } })}
        className="flex items-center gap-1.5 text-label-sm text-on-surface-variant hover:text-primary transition-colors mb-5 cursor-pointer"
      >
        <ArrowLeft className="w-3.5 h-3.5" /> 返回编辑器
      </button>

      <div className="grid grid-cols-12 gap-6 max-w-[1100px]">
        {/* ── Settings console ── */}
        <div className="col-span-12 lg:col-span-5 space-y-5">
          {/* project summary strip */}
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 flex items-center gap-4">
            <div className="w-11 h-11 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
              <Clapperboard className="w-5 h-5 text-on-primary-container" />
            </div>
            <div className="min-w-0">
              <p className="text-body-sm font-semibold text-on-surface truncate">{projectName}</p>
              <p className="text-caption text-on-surface-variant font-mono">
                {formatTimecode(timeline.duration_sec, timeline.fps)} · {timeline.tracks.length} 轨 · {timeline.width}×{timeline.height}
              </p>
            </div>
          </div>

          {/* preset grid */}
          <div>
            <h3 className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-2.5">
              <Film className="w-3.5 h-3.5" /> 导出预设
            </h3>
            <div className="grid grid-cols-2 gap-2" aria-busy={loadingPresets}>
              {Object.entries(presets).map(([id, p]) => (
                <button
                  key={id}
                  onClick={() => applyPreset(id)}
                  className={`text-left px-3 py-2.5 rounded-cw-sm border transition-all duration-short3 cursor-pointer ${
                    presetId === id
                      ? 'border-primary bg-primary/10 shadow-md shadow-primary/10'
                      : 'border-outline-variant/40 bg-surface-container hover:border-outline'
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="text-body">{p.icon}</span>
                    <span className={`text-body-sm font-medium ${presetId === id ? 'text-on-surface' : 'text-on-surface-variant'}`}>{p.name}</span>
                  </span>
                  <span className="block text-caption text-on-surface-variant/70 font-mono mt-0.5 ml-6">
                    {p.width}×{p.height} · {p.fps}fps · {p.bitrate}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* custom params */}
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 space-y-3.5">
            <h3 className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide">
              <Gauge className="w-3.5 h-3.5" /> 参数
            </h3>
            {/* C6: 自定义预设保存/应用 */}
            <div className="flex items-center gap-2">
              <input
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="预设名称（如 竖屏 4K）"
                className="flex-1 bg-surface rounded-cw-xs px-2 py-1.5 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary"
              />
              <button
                onClick={() => {
                  const name = presetName.trim();
                  if (!name) return;
                  const next = [...savedPresets.filter((p) => p.name !== name), { name, width: settings.width, height: settings.height, fps: settings.fps, bitrate: settings.bitrate }];
                  setSavedPresets(next);
                  localStorage.setItem('cw_export_presets', JSON.stringify(next));
                  setPresetName('');
                }}
                className="px-2.5 py-1.5 rounded-cw-xs bg-surface-container-high text-label-sm text-on-surface hover:bg-primary/20 cursor-pointer"
                title="保存当前参数为自定义预设"
              >
                另存为预设
              </button>
            </div>
            {savedPresets.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {savedPresets.map((p) => (
                  <span key={p.name}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-cw-full bg-surface-container-high border border-outline-variant/30 text-caption cursor-pointer hover:border-primary/60"
                    title={`${p.width}×${p.height} · ${p.fps}fps · ${p.bitrate}`}
                  >
                    <button onClick={() => { setSettings({ ...settings, width: p.width, height: p.height, fps: p.fps, bitrate: p.bitrate }); setPresetId(''); }}
                      className="hover:text-primary">{p.name}</button>
                    <button onClick={() => {
                      const next = savedPresets.filter((x) => x.name !== p.name);
                      setSavedPresets(next);
                      localStorage.setItem('cw_export_presets', JSON.stringify(next));
                    }} className="text-on-surface-variant/50 hover:text-error" aria-label={`删除预设 ${p.name}`}>×</button>
                  </span>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <NumField label="宽度" value={settings.width} onChange={(v) => setSettings({ ...settings, width: v })} min={320} max={7680} step={2} />
              <NumField label="高度" value={settings.height} onChange={(v) => setSettings({ ...settings, height: v })} min={240} max={4320} step={2} />
              <NumField label="帧率" value={settings.fps} onChange={(v) => setSettings({ ...settings, fps: v })} min={1} max={120} step={0.01} />
              <div>
                <label className="block text-label text-on-surface-variant mb-1">码率</label>
                <select
                  value={settings.bitrate}
                  onChange={(e) => setSettings({ ...settings, bitrate: e.target.value })}
                  className="w-full bg-surface rounded-cw-xs px-2 py-1.5 text-body-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                >
                  {['2M', '3M', '5M', '6M', '8M', '12M', '20M'].map((b) => <option key={b}>{b}</option>)}
                </select>
              </div>
            </div>
            <div className="flex items-center justify-between pt-1 border-t border-outline-variant/20">
              <span className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
                <HardDrive className="w-3.5 h-3.5" /> 预估体积
              </span>
              <span className="font-mono text-body-sm text-primary">{estSize}</span>
            </div>

            {/* W11: BGM 素材源 */}
            <div className="pt-2 border-t border-outline-variant/20">
              <label className="block text-label text-on-surface-variant mb-1">背景音乐 (BGM)</label>
              <select
                value={bgmPath}
                onChange={(e) => setBgmPath(e.target.value)}
                className="w-full bg-surface rounded-cw-xs px-2 py-1.5 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
              >
                <option value="">无（不混入 BGM）</option>
                {audioAssets.map((a) => (
                  <option key={a.id} value={a.path}>{a.name}</option>
                ))}
              </select>
              {audioAssets.length === 0 && (
                <p className="text-caption text-on-surface-variant/60 mt-1">素材库暂无音频（上传音频素材后可选用作 BGM）</p>
              )}
            </div>
          </div>

          <Button size="lg" className="w-full group" onClick={submitRender} disabled={submitting || timeline.tracks.length === 0}>
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            {submitting ? '提交中…' : '加入渲染队列'}
          </Button>
          {timeline.tracks.length === 0 && (
            <p className="text-caption text-error text-center">时间轴为空，请先在编辑器中添加内容</p>
          )}
        </div>

        {/* ── Render queue ── */}
        <div className="col-span-12 lg:col-span-7">
          <h3 className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-2.5">
            <Cpu className="w-3.5 h-3.5" /> 渲染队列
            <Badge variant="default" className="ml-1">{queue.filter((q) => q.status === 'rendering' || q.status === 'pending').length} 进行中</Badge>
          </h3>

          {queue.length === 0 ? (
            <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-10 text-center">
              <Cpu className="w-8 h-8 text-on-surface-variant/40 mx-auto mb-2" />
              <p className="text-body-sm text-on-surface-variant">队列为空</p>
              <p className="text-caption text-on-surface-variant/60 mt-1">选择预设并点击「加入渲染队列」开始</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {queue.map((item) => (
                <QueueCard key={item.task_id} item={item} onRetry={() => retryRender(item.task_id)} />
              ))}
            </div>
          )}
        </div>
      </div>
    </StandardLayout>
  );
}

function QueueCard({ item, onRetry }: { item: QueueItem; onRetry?: () => void }) {
  const active = item.status === 'rendering' || item.status === 'pending';
  return (
    <div className={`bg-surface-container border rounded-cw-md p-3.5 transition-colors duration-short3 ${
      item.status === 'completed' ? 'border-track-audio/40'
        : item.status === 'failed' ? 'border-error/40'
        : 'border-outline-variant/30'
    }`}>
      <div className="flex items-center gap-3">
        <span className={`w-8 h-8 rounded-cw-sm flex items-center justify-center shrink-0 ${
          item.status === 'completed' ? 'bg-track-audio/15 text-track-audio'
            : item.status === 'failed' ? 'bg-error/15 text-error'
            : 'bg-primary/15 text-primary'
        }`}>
          {item.status === 'completed' ? <CheckCircle2 className="w-4 h-4" />
            : item.status === 'failed' ? <XCircle className="w-4 h-4" />
            : <Loader2 className="w-4 h-4 animate-spin" />}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-body-sm font-medium text-on-surface truncate">{item.label}</p>
          <p className="text-caption text-on-surface-variant font-mono">
            {item.presetName} · {item.startedAt}
            {active && item.phase ? ` · ${phaseLabel(item.phase)}` : ''}
          </p>
        </div>
        <span className={`font-mono text-body-sm shrink-0 ${
          item.status === 'completed' ? 'text-track-audio' : 'text-on-surface-variant'
        }`}>
          {item.status === 'failed' ? '失败' : `${item.progress}%`}
        </span>
        {item.status === 'completed' && item.simulated && (
          <span className="px-2 py-1 rounded-cw-sm border border-track-text/50 bg-track-text/10 text-caption font-medium text-track-text"
            title="演示模式 — 未真实渲染，仅本地模拟进度">
            演示模式
          </span>
        )}
        {item.status === 'completed' && !item.simulated && (item.filename || item.output_path) && (
          <button
            onClick={() => {
              renderApi.downloadFile(item.filename ?? '', item.output_path)
                .catch(() => toast('下载失败 — 请重试', 'error'));
            }}
            className="p-2 rounded-cw-sm bg-track-audio/15 text-track-audio hover:bg-track-audio/25 transition-colors cursor-pointer"
            title="下载"
            aria-label="下载成片"
          >
            <Download className="w-4 h-4" />
          </button>
        )}
        {/* P8: 渲染后添加水印（工具级；调用后端 watermark 工具） */}
        {item.status === 'completed' && !item.simulated && (item.filename || item.output_path) && (
          <button
            onClick={() => {
              const base = item.output_path || (item.filename ? `renders/${item.filename}` : '');
              if (!base) { toast('无渲染产物路径', 'error'); return; }
              toast('正在添加水印…', 'info');
              toolApi.execute('watermark', { input_path: base, text: 'ClipWright' })
                .then((res) => {
                  if (res.status === 'success') {
                    toast('水印已添加', 'success');
                  } else {
                    toast(`水印失败：${res.error ?? '未知错误'}`, 'error');
                  }
                })
                .catch(() => toast('水印失败（后端离线）', 'error'));
            }}
            className="p-2 rounded-cw-sm bg-track-text/15 text-track-text hover:bg-track-text/25 transition-colors cursor-pointer"
            title="添加水印"
            aria-label="添加水印"
          >
            <Wand2 className="w-4 h-4" />
          </button>
        )}
        {item.status === 'failed' && onRetry && (
          <button
            onClick={onRetry}
            className="flex items-center gap-1 px-2 py-1 rounded-cw-sm border border-error/40 bg-error/10 text-caption font-medium text-error hover:bg-error/20 transition-colors cursor-pointer"
            title="使用相同参数重新提交渲染"
          >
            <RotateCcw className="w-3 h-3" /> 重试
          </button>
        )}
      </div>
      {/* 失败详情（U14） */}
      {item.status === 'failed' && item.detail && (
        <p className="mt-1.5 text-caption text-error">{item.detail}</p>
      )}
      {/* progress bar */}
      <div className="mt-2.5 h-1.5 bg-surface rounded-cw-full overflow-hidden">
        <div
          className={`h-full rounded-cw-full transition-all duration-medium2 ${
            item.status === 'failed' ? 'bg-error'
              : item.status === 'completed' ? 'bg-track-audio'
              : 'bg-primary'
          }`}
          style={{ width: `${item.progress}%` }}
        />
      </div>
    </div>
  );
}

function NumField({ label, value, onChange, min, max, step }: {
  label: string; value: number; onChange: (v: number) => void;
  min?: number; max?: number; step?: number;
}) {
  const [text, setText] = useState(String(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(String(value));
  }, [value, focused]);
  return (
    <div>
      <label className="block text-label text-on-surface-variant mb-1">{label}</label>
      <input
        type="number"
        value={text}
        onFocus={() => setFocused(true)}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          setFocused(false);
          const raw = Number(text);
          if (text.trim() === '' || !isFinite(raw) || raw <= 0) { setText(String(value)); return; }
          const lo = min ?? raw;
          const hi = max ?? raw;
          onChange(Math.min(Math.max(raw, lo), hi));
        }}
        min={min} max={max} step={step}
        className="w-full bg-surface rounded-cw-xs px-2 py-1.5 text-body-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary"
      />
    </div>
  );
}

/**
 * U18: 从恢复的后端任务中解析原始标签与预设名。
 * 文件名形如 `renders/项目名_1920x1080.mp4`：去前缀与 `_WxH.mp4` 后缀恢复项目名，
 * 由高度推导预设名（如 `1080p`）；无法解析时回退到通用占位。
 */
function parseRestoredTask(t: RenderProgress): { label: string; presetName: string; filename?: string } {
  const extra = t as RenderProgress & { filename?: string; output_path?: string };
  const raw = extra.filename ?? extra.output_path;
  if (raw) {
    const base = raw.split(/[/\\]/).filter(Boolean).pop() ?? '';
    const m = base.match(/^(.+)_(\d+)x(\d+)\.mp4$/i);
    if (m) return { label: m[1], presetName: `${m[3]}p`, filename: base };
    if (base) return { label: base.replace(/\.mp4$/i, ''), presetName: '—', filename: base };
  }
  return { label: '恢复的任务', presetName: '—' };
}

function phaseLabel(p: string): string {  const map: Record<string, string> = { prepare: '准备', trim: '裁剪', concat: '拼接', text: '文字', mg: '动画', overlay: '叠加', audio: '音频', done: '完成' };
  return map[p] ?? p;
}

function estimateSize(durationSec: number, bitrate: string): string {
  const mbps = parseFloat(bitrate.replace('M', '')) || 5;
  const mb = (mbps * durationSec) / 8;
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
}
