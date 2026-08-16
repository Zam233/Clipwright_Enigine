import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useProjectStore } from '@/stores/projectStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useHistoryStore } from '@/stores/historyStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { toast } from '@/stores/toastStore';
import { Button, Tooltip } from '@/components/ui';
import { formatTimecode, uid } from '@/lib/utils';
import type { Clip } from '@/types/timeline';
import type { ClipKind } from '@/types/timeline';
import { createEmptyTimeline } from '@/types/timeline';
import {
  Play, Pause, SkipBack, SkipForward, StepBack, StepForward,
  Undo2, Redo2, Save, PanelLeft, PanelRight, Bot, Film,
  FileText, ArrowLeft, Check, Loader2, Mic, Download,
  Copy, ClipboardPaste, FileUp, Keyboard, FileJson, Upload,
  History as HistoryIcon, X,
} from 'lucide-react';

/**
 * EditorToolbar — top transport + panel toggles + project actions.
 */

/** In-memory clip clipboard (shared with keyboard shortcuts). */
export const clipClipboard: { clips: Clip[] } = { clips: [] };

/** 导入类操作错误归类：400 → 格式错误，422 → 数据校验失败，其他 → 后端不可达。 */
export function extractImportError(err: unknown, actionLabel: string): string {
  const status = (err as { response?: { status?: number } } | null)?.response?.status;
  if (status === 400) return `${actionLabel} — 格式错误：请检查文件内容后重试`;
  if (status === 422) return `${actionLabel} — 数据校验失败，请检查文件`;
  return `${actionLabel} — 后端不可达`;
}

export function EditorToolbar() {
  const navigate = useNavigate();
  const projectName = useProjectStore((s) => s.projectName);
  const setProjectName = useProjectStore((s) => s.setProjectName);
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState('');
  const isPlaying = usePreviewStore((s) => s.isPlaying);
  const togglePlay = usePreviewStore((s) => s.togglePlay);
  const currentTimeSec = usePreviewStore((s) => s.currentTimeSec);
  const durationSec = usePreviewStore((s) => s.durationSec);
  const fps = usePreviewStore((s) => s.fps);
  const stepForward = usePreviewStore((s) => s.stepForward);
  const stepBackward = usePreviewStore((s) => s.stepBackward);
  const seekToStart = usePreviewStore((s) => s.seekToStart);
  const seekToEnd = usePreviewStore((s) => s.seekToEnd);

  const panels = useWorkspaceStore((s) => s.panels);
  const togglePanel = useWorkspaceStore((s) => s.togglePanel);

  const undo = useHistoryStore((s) => s.undo);
  const redo = useHistoryStore((s) => s.redo);
  const canUndo = useHistoryStore((s) => s.undoStack.length > 0);
  const canRedo = useHistoryStore((s) => s.redoStack.length > 0);

  const handleUndo = () => {
    const tl = undo();
    if (tl) useTimelineStore.getState().setTimeline(tl);
  };
  const handleRedo = () => {
    const tl = redo();
    if (tl) useTimelineStore.getState().setTimeline(tl);
  };

  // W7: 撤销历史列表（跳转到任意历史快照）
  const [historyOpen, setHistoryOpen] = useState(false);
  const undoStack = useHistoryStore((s) => s.undoStack);
  const handleJumpTo = (index: number) => {
    const tl = useHistoryStore.getState().jumpTo(index);
    if (tl) useTimelineStore.getState().setTimeline(tl);
    setHistoryOpen(false);
  };

  const handleCopy = () => {
    const sel = useSelectionStore.getState().selectedClipIds;
    if (sel.length === 0) return;
    const store = useTimelineStore.getState();
    const found: Clip[] = [];
    for (const tr of store.timeline.tracks) {
      for (const c of tr.clips) {
        if (sel.includes(c.id)) found.push(c);
      }
    }
    if (found.length > 0) {
      // 按起始时间排序：粘贴偏移以最早的片段为锚点
      clipClipboard.clips = [...found].sort((a, b) => a.start_sec - b.start_sec);
    }
  };

  // C7: 代理工作流（生成代理 / 切换代理·原片）
  const [proxyBusy, setProxyBusy] = useState(false);
  const [proxyMode, setProxyMode] = useState<'full' | 'proxy'>('full');
  const [proxyNotice, setProxyNotice] = useState<string | null>(null);

  const handleGenerateProxy = async () => {
    // 以第一个视频片段/素材作为输入
    const store = useTimelineStore.getState();
    let inputPath = '';
    outer: for (const tr of store.timeline.tracks) {
      for (const c of tr.clips) {
        if (c.asset_id && (c.kind === 'video' || c.kind === 'image')) { inputPath = c.asset_id; break outer; }
      }
    }
    if (!inputPath) { setProxyNotice('时间轴中没有视频片段，无法生成代理'); return; }
    setProxyBusy(true);
    setProxyNotice(null);
    try {
      const { proxyApi } = await import('@/services/api');
      const res = await proxyApi.generate(inputPath);
      setProxyNotice(`代理已生成：${String((res as Record<string, unknown>).proxy_path ?? '完成')}`);
    } catch {
      setProxyNotice('代理生成失败（后端离线或文件不可达）');
    } finally {
      setProxyBusy(false);
    }
  };

  const handleToggleProxy = async () => {
    const store = useTimelineStore.getState();
    const tl = store.timeline;
    setProxyBusy(true);
    setProxyNotice(null);
    try {
      const { proxyApi } = await import('@/services/api');
      const next = proxyMode === 'full' ? 'proxy' : 'full';
      const result = next === 'proxy'
        ? await proxyApi.switchToProxy(tl as unknown as Record<string, unknown>)
        : await proxyApi.switchToFull(tl as unknown as Record<string, unknown>);
      useHistoryStore.getState().pushState(tl, 'proxy-switch');
      useTimelineStore.getState().setTimeline(result as unknown as ReturnType<typeof createEmptyTimeline>);
      setProxyMode(next);
      setProxyNotice(next === 'proxy' ? '已切换到代理素材（低分辨率）' : '已切回原始素材（全分辨率）');
    } catch {
      setProxyNotice('切换失败（后端离线）');
    } finally {
      setProxyBusy(false);
    }
  };

  const handlePaste = () => {
    if (clipClipboard.clips.length === 0) return;
    const store = useTimelineStore.getState();
    const t = usePreviewStore.getState().currentTimeSec;
    useHistoryStore.getState().pushState(store.timeline, 'paste');
    for (const src of clipClipboard.clips) {
      const newId = uid('clip');
      const track = store.timeline.tracks.find((tr) => tr.id === src.track_id || tr.kind === src.kind);
      if (!track || track.locked) continue;
      store.addClip(track.id, {
        ...src,
        id: newId,
        start_sec: t + (src.start_sec - clipClipboard.clips[0].start_sec),
        asset_id: src.asset_id,
        kind: src.kind,
        keyframes: src.keyframes?.map((kf) => ({ ...kf })),
      });
    }
  };

  const handleSrtImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.srt,.vtt';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const text = await file.text();
      const entries = parseSrt(text);
      if (entries.length === 0) return;
      const store = useTimelineStore.getState();
      useHistoryStore.getState().pushState(store.timeline, 'import srt');
      let subTrack = store.timeline.tracks.find((t) => t.kind === 'caption' || t.kind === 'text');
      if (!subTrack) {
        const tid = store.addTrack('caption', '字幕');
        subTrack = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
        if (!subTrack) return;
      }
      for (const e of entries) {
        store.addClip(subTrack!.id, {
          kind: 'caption' as const,
          asset_id: '',
          start_sec: e.start,
          duration_sec: e.end - e.start,
          source_offset_sec: 0,
          speed: 1,
          volume: 1,
          opacity: 1,
          text: e.text,
          font_size: 28,
          font_color: '#FFFFFF',
          keyframes: [],
          metadata: { title: `字幕 ${e.index}` },
        });
      }
    };
    input.click();
  };

  const handleAudioTranscribe = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.mp3,.wav,.m4a,.mp4';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const { getApiClient } = await import('@/services/api');
        const form = new FormData();
        form.append('file', file);
        const uploadRes = await getApiClient().post('/api/asset/upload', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        const assetPath: string = uploadRes.data?.file_path || uploadRes.data?.local_path;
        if (!assetPath) return;

        const { data } = await getApiClient().post('/api/subtitle/transcribe', {
          audio_path: assetPath,
          language: '',
          model_size: 'base',
        });
        const clips: Record<string, unknown>[] = data?.clips ?? [];
        if (clips.length === 0) return;

        const store = useTimelineStore.getState();
        useHistoryStore.getState().pushState(store.timeline, 'transcribe');
        let subTrack = store.timeline.tracks.find((t) => t.kind === 'caption' || t.kind === 'text');
        if (!subTrack) {
          const tid = store.addTrack('caption', '字幕');
          subTrack = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
          if (!subTrack) return;
        }
        for (const c of clips) {
          store.addClip(subTrack!.id, {
            kind: 'caption' as const,
            asset_id: '',
            start_sec: Number(c.start_sec) || 0,
            duration_sec: Math.max(0.5, Number(c.duration_sec) || 1),
            source_offset_sec: 0,
            speed: 1,
            volume: 1,
            opacity: 1,
            text: (c.text as string) ?? '',
            font_size: 28,
            font_color: '#FFFFFF',
            keyframes: [],
            metadata: { title: '字幕' },
          });
        }
      } catch (err) {
        toast(extractImportError(err, '音频转录失败'), 'error');
      }
    };
    input.click();
  };

  const handleEdlImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.edl,.fcpxml,.xml';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const isFcp = file.name.endsWith('.fcpxml') || text.trim().startsWith('<?xml');
        const { edlApi } = await import('@/services/api/edl');
        const result = isFcp ? await edlApi.importFCPXML(text) : await edlApi.importEDL(text);
        const clips = result.clips ?? [];
        if (clips.length === 0) return;

        const store = useTimelineStore.getState();
        useHistoryStore.getState().pushState(store.timeline, `import ${isFcp ? 'fcpxml' : 'edl'}`);
        for (const c of clips) {
          const kind = (c.kind as ClipKind) || 'video';
          let track = store.timeline.tracks.find((t) => t.kind === kind);
          if (!track) {
            const tid = store.addTrack(kind);
            track = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
          }
          if (!track) continue;
          const start = (c.start_sec as number) ?? (c.start as number) ?? 0;
          const dur = (c.duration_sec as number) ?? (c.duration as number) ?? 5;
          store.addClip(track.id, {
            kind,
            asset_id: (c.asset_id as string) ?? '',
            start_sec: start,
            duration_sec: Math.max(0.1, dur),
            source_offset_sec: 0,
            speed: 1,
            volume: 1,
            opacity: 1,
            metadata: { title: (c.title as string) ?? (c.name as string) ?? `${kind} clip` },
          });
        }
      } catch (err) {
        toast(extractImportError(err, 'EDL 导入失败'), 'error');
      }
    };
    input.click();
  };

  const handleEdlExport = async () => {
    const store = useTimelineStore.getState();
    const clips = store.timeline.tracks.flatMap((t) =>
      t.clips.map((c) => ({
        id: c.id,
        start_sec: c.start_sec,
        duration_sec: c.duration_sec,
        kind: c.kind,
        title: c.metadata?.title as string ?? c.asset_id,
      })),
    );
    if (clips.length === 0) return;
    try {
      const { edlApi } = await import('@/services/api/edl');
      const result = await edlApi.exportEDL(clips, store.timeline.fps);
      const blob = new Blob([result.edl], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${projectName || 'timeline'}.edl`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast('EDL 导出失败 — 后端不可达', 'error');
    }
  };

  const handleJsonExport = () => {
    const store = useTimelineStore.getState();
    const json = JSON.stringify(store.timeline, null, 2);
    const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${projectName || 'timeline'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleJsonImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        if (!data.tracks || !Array.isArray(data.tracks)) return;
        const store = useTimelineStore.getState();
        useHistoryStore.getState().pushState(store.timeline, 'import-json');
        store.setTimeline(data);
      } catch {
        toast('JSON 导入失败 — 文件格式无效', 'error');
      }
    };
    input.click();
  };

  const handleSrtExport = () => {
    const store = useTimelineStore.getState();
    const captions = store.timeline.tracks
      .flatMap((t) => t.clips)
      .filter((c) => (c.kind === 'caption' || c.kind === 'text') && c.text?.trim())
      .sort((a, b) => a.start_sec - b.start_sec);

    if (captions.length === 0) return;

    let srt = '';
    captions.forEach((c, i) => {
      const idx = i + 1;
      const start = formatSrtTime(c.start_sec);
      const end = formatSrtTime(c.start_sec + c.duration_sec);
      srt += `${idx}\n${start} --> ${end}\n${c.text}\n\n`;
    });

    const blob = new Blob([srt], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${projectName || 'captions'}.srt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-surface-dim border-b border-outline-variant/30 shrink-0">
      {/* Back + Logo + project name */}
      <div className="flex items-center gap-2 mr-2">
        <Tooltip side="bottom" content="返回首页">
          <button onClick={() => navigate({ to: '/' })}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <ArrowLeft className="w-4 h-4" />
          </button>
        </Tooltip>
        <div className="w-7 h-7 rounded-cw-sm bg-primary-container flex items-center justify-center">
          <Film className="w-4 h-4 text-on-primary-container" />
        </div>
        {editingName ? (
          <div className="flex items-center gap-1">
            <input
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  setProjectName(draftName.trim() || projectName);
                  setEditingName(false);
                  useProjectStore.getState().requestSave();
                } else if (e.key === 'Escape') {
                  setEditingName(false);
                }
              }}
              autoFocus
              className="bg-surface border border-primary rounded-cw-xs px-2 py-0.5 text-title-sm font-medium text-on-surface outline-none"
            />
            <button onClick={() => { setProjectName(draftName.trim() || projectName); setEditingName(false); useProjectStore.getState().requestSave(); }}
              className="p-0.5 rounded-cw-xs text-primary hover:bg-primary/10 cursor-pointer">
              <Check className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex flex-col cursor-pointer" onClick={() => { setDraftName(projectName); setEditingName(true); }}>
            <span className="text-title-sm font-medium text-on-surface leading-tight hover:text-primary transition-colors">{projectName}</span>
            <span className="text-caption text-on-surface-variant leading-tight">点击重命名</span>
          </div>
        )}
      </div>

      <div className="w-px h-6 bg-outline-variant/40" />

      {/* Undo / Redo */}
      <Tooltip side="bottom" content="撤销 (Ctrl+Z)">
        <button onClick={handleUndo} disabled={!canUndo}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer">
          <Undo2 className="w-4 h-4" />
        </button>
      </Tooltip>
      <Tooltip side="bottom" content="重做 (Ctrl+Shift+Z)">
        <button onClick={handleRedo} disabled={!canRedo}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer">
          <Redo2 className="w-4 h-4" />
        </button>
      </Tooltip>
      {/* W7: 历史列表 */}
      <div className="relative">
        <Tooltip side="bottom" content="历史记录">
          <button onClick={() => setHistoryOpen(!historyOpen)} disabled={undoStack.length === 0}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer">
            <HistoryIcon className="w-4 h-4" />
          </button>
        </Tooltip>
        {historyOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setHistoryOpen(false)} />
            <div className="absolute left-0 top-full mt-1.5 z-50 w-[260px] bg-surface-container-high border border-outline-variant/50
              rounded-cw-md shadow-2xl shadow-black/50 overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 border-b border-outline-variant/30">
                <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">历史记录 ({undoStack.length})</span>
                <button onClick={() => setHistoryOpen(false)} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="max-h-[280px] overflow-y-auto p-1.5 space-y-0.5">
                {undoStack.length === 0 && (
                  <p className="text-caption text-on-surface-variant text-center py-3">暂无历史</p>
                )}
                {undoStack.map((entry, i) => (
                  <button key={i} onClick={() => handleJumpTo(i)}
                    className="w-full flex items-center gap-2 px-2 py-1.5 rounded-cw-sm text-left
                      bg-surface-container hover:bg-surface transition-colors cursor-pointer">
                    <span className="font-mono text-caption text-on-surface-variant/60 w-6 shrink-0">{undoStack.length - i}</span>
                    <span className="text-label-sm text-on-surface truncate flex-1">{entry.label}</span>
                    <span className="text-caption text-on-surface-variant/50 font-mono shrink-0">
                      {new Date(entry.timestamp).toLocaleTimeString()}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
      <Tooltip side="bottom" content="复制 (Ctrl+C)">
        <button onClick={handleCopy}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
          <Copy className="w-3.5 h-3.5" />
        </button>
      </Tooltip>
      <Tooltip side="bottom" content="粘贴 (Ctrl+V)">
        <button onClick={handlePaste} disabled={clipClipboard.clips.length === 0}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer">
          <ClipboardPaste className="w-3.5 h-3.5" />
        </button>
      </Tooltip>

      <div className="w-px h-6 bg-outline-variant/40" />

      {/* Transport controls (centered) */}
      <div className="flex-1 flex items-center justify-center gap-1">
        <Tooltip side="bottom" content="跳到开头 (Home)">
          <button onClick={seekToStart}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <SkipBack className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="上一帧 (←)">
          <button onClick={stepBackward}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <StepBack className="w-4 h-4" />
          </button>
        </Tooltip>
        <button
          onClick={togglePlay}
          className="w-10 h-10 rounded-cw-full bg-primary text-on-primary flex items-center justify-center
            hover:scale-105 active:scale-95 transition-transform duration-short3 shadow-lg shadow-primary/25 cursor-pointer"
        >
          {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5 ml-0.5" />}
        </button>
        <Tooltip side="bottom" content="下一帧 (→)">
          <button onClick={stepForward}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <StepForward className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="跳到结尾 (End)">
          <button onClick={seekToEnd}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <SkipForward className="w-4 h-4" />
          </button>
        </Tooltip>

        {/* Timecode */}
        <div className="ml-3 font-mono text-body-sm text-on-surface bg-surface-container px-3 py-1 rounded-cw-xs border border-outline-variant/30">
          <span className="text-primary">{formatTimecode(currentTimeSec, fps)}</span>
          <span className="text-on-surface-variant"> / {formatTimecode(durationSec, fps)}</span>
        </div>
      </div>

      {/* Panel toggles */}
      <div className="flex items-center gap-1">
        <Tooltip side="bottom" content="导入字幕 (SRT)">
          <button onClick={handleSrtImport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <FileText className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="导出字幕 (SRT)">
          <button onClick={handleSrtExport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Download className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="音频转字幕">
          <button onClick={handleAudioTranscribe}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Mic className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="导入 EDL/FCPXML">
          <button onClick={handleEdlImport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <FileUp className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="导出 EDL">
          <button onClick={handleEdlExport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Download className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="导出 Timeline JSON">
          <button onClick={handleJsonExport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <FileJson className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="导入 Timeline JSON">
          <button onClick={handleJsonImport}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Upload className="w-3.5 h-3.5" />
          </button>
        </Tooltip>

        {/* C7: 代理工作流 */}
        <div className="flex items-center gap-0.5 px-1.5 py-1 rounded-cw-sm bg-surface-container border border-outline-variant/30 ml-1">
          <Tooltip side="bottom" content="生成代理（首个视频片段）">
            <button onClick={handleGenerateProxy} disabled={proxyBusy}
              className="p-1 rounded-cw-xs text-on-surface-variant hover:text-primary disabled:opacity-30 transition-colors cursor-pointer"
              aria-label="生成代理">
              {proxyBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Film className="w-3.5 h-3.5" />}
            </button>
          </Tooltip>
          <Tooltip side="bottom" content={proxyMode === 'full' ? '切换到代理素材' : '切回原始素材'}>
            <button onClick={handleToggleProxy} disabled={proxyBusy}
              className={`p-1 rounded-cw-xs transition-colors cursor-pointer disabled:opacity-30 ${
                proxyMode === 'proxy' ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-primary'
              }`}
              aria-label="切换代理/原片">
              {proxyBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : (
                <span className="text-caption font-mono px-0.5">{proxyMode === 'full' ? '原片' : '代理'}</span>
              )}
            </button>
          </Tooltip>
          {proxyNotice && (
            <span className="max-w-[160px] truncate text-caption text-on-surface-variant/80 px-1" title={proxyNotice}>
              {proxyNotice}
            </span>
          )}
        </div>
        <Tooltip side="bottom" content="素材面板">
          <button onClick={() => togglePanel('assets')}
            className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${panels.assets ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface'}`}>
            <PanelLeft className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="Agent 副驾驶">
          <button onClick={() => togglePanel('agent')}
            className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${panels.agent ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface'}`}>
            <Bot className="w-4 h-4" />
          </button>
        </Tooltip>
        <Tooltip side="bottom" content="属性面板">
          <button onClick={() => togglePanel('properties')}
            className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${panels.properties ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface'}`}>
            <PanelRight className="w-4 h-4" />
          </button>
        </Tooltip>

        <div className="w-px h-6 bg-outline-variant/40 mx-1" />

        <div className="w-px h-6 bg-outline-variant/40 mx-1" />

        <Tooltip side="bottom" content="快捷键速查 (Ctrl+/)">
          <button onClick={() => useSettingsStore.getState().setCheatSheetOpen(true)}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Keyboard className="w-4 h-4" />
          </button>
        </Tooltip>

        {/* Save status indicator */}
        <SaveStatusIndicator />

        <Button size="sm" variant="outline" onClick={() => useProjectStore.getState().requestSave()}
          disabled={useProjectStore.getState().isSaving}>
          <Save className="w-3.5 h-3.5" />
          保存
        </Button>
        <Button size="sm" variant="default" onClick={() => {
          const pid = useProjectStore.getState().projectId;
          if (pid) navigate({ to: '/export/$projectId', params: { projectId: pid } });
        }}>
          导出
        </Button>
      </div>
    </div>
  );
}

interface SrtEntry { index: number; start: number; end: number; text: string; }

function SaveStatusIndicator() {
  const isSaving = useProjectStore((s) => s.isSaving);
  const lastSavedAt = useProjectStore((s) => s.lastSavedAt);
  const saveError = useProjectStore((s) => s.saveError);

  if (isSaving) {
    return (
      <span className="flex items-center gap-1 text-caption text-on-surface-variant">
        <Loader2 className="w-3 h-3 animate-spin" />
        保存中…
      </span>
    );
  }
  if (saveError) {
    return <span className="text-caption text-error">保存失败</span>;
  }
  if (lastSavedAt) {
    const time = new Date(lastSavedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    return <span className="text-caption text-on-surface-variant">已保存 {time}</span>;
  }
  return null;
}

function parseSrt(raw: string): SrtEntry[] {
  const normalized = raw.replace(/\r\n/g, '\n').trim();
  const blocks = normalized.split(/\n\n+/);
  const entries: SrtEntry[] = [];
  for (const block of blocks) {
    const lines = block.trim().split('\n');
    if (lines.length < 2) continue;
    const timeLine = lines[1];
    const timeMatch = timeLine.match(/(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)/);
    if (!timeMatch) continue;
    const start = +timeMatch[1] * 3600 + +timeMatch[2] * 60 + +timeMatch[3] + +timeMatch[4] / 1000;
    const end = +timeMatch[5] * 3600 + +timeMatch[6] * 60 + +timeMatch[7] + +timeMatch[8] / 1000;
    const text = lines.slice(2).join('\n').replace(/<[^>]+>/g, '').trim();
    if (text) entries.push({ index: +lines[0] || entries.length + 1, start, end, text });
  }
  return entries;
}

function formatSrtTime(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = (sec % 60);
  const ms = Math.round((s - Math.floor(s)) * 1000);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(Math.floor(s)).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
}
