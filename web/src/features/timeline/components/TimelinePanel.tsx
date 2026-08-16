import { useCallback, useEffect, useRef, useState } from 'react';
import { TimelineEngine } from '../engine/TimelineEngine';
import { useTimelineStore } from '@/stores/timelineStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useHistoryStore } from '@/stores/historyStore';
import { keybindingEngine } from '@/features/keyboard/KeybindingEngine';
import { Tooltip } from '@/components/ui';
import { formatTimecode, cn } from '@/lib/utils';
import type { ClipKind } from '@/types/timeline';
import { TRACK_COLORS } from '@/types/timeline';
import { HEADER_W } from '../engine/types';
import {
  Magnet, Plus, ZoomIn, ZoomOut, Maximize2, Trash2, Scissors, ChevronsLeft,
  Layers, Lock, LockOpen, Volume2, VolumeX, ArrowUp, ArrowDown, X, Flag, FlagOff,
  Grid3x3, Eye, EyeOff, Settings, History as HistoryIcon, RotateCcw,
} from 'lucide-react';
import { useSettingsStore } from '@/stores/settingsStore';
import { useProjectStore } from '@/stores/projectStore';
import { versionApi } from '@/services/api/project';
import type { TimelineVersionEntry } from '@/services/api/project';
import { mediaManager } from '@/services/media/mediaManager';

/**
 * TimelinePanel — hosts the Canvas timeline engine plus transport/track controls.
 */
export function TimelinePanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<TimelineEngine | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [dropActive, setDropActive] = useState(false);

  const addTrack = useTimelineStore((s) => s.addTrack);
  const removeClip = useTimelineStore((s) => s.removeClip);
  const rippleDelete = useTimelineStore((s) => s.rippleDelete);
  const splitClip = useTimelineStore((s) => s.splitClip);
  const timeline = useTimelineStore((s) => s.timeline);
  const selectedClipIds = useSelectionStore((s) => s.selectedClipIds);
  const toolMode = useSelectionStore((s) => s.toolMode);
  const setToolMode = useSelectionStore((s) => s.setToolMode);
  const snapEnabled = useSettingsStore((s) => s.snapEnabled);
  const setSnapEnabled = useSettingsStore((s) => s.setSnapEnabled);
  const snapToGrid = useSettingsStore((s) => s.snapToGrid);
  const setSnapToGrid = useSettingsStore((s) => s.setSnapToGrid);
  const snapGridSec = useSettingsStore((s) => s.snapGridSec);
  const currentTimeSec = usePreviewStore((s) => s.currentTimeSec);
  const fps = usePreviewStore((s) => s.fps);
  const setMarkerIn = usePreviewStore((s) => s.setMarkerIn);
  const setMarkerOut = usePreviewStore((s) => s.setMarkerOut);
  const setLoopRegion = usePreviewStore((s) => s.setLoopRegion);
  const [trackMgrOpen, setTrackMgrOpen] = useState(false);
  // M8: 重命名标记弹层（{ time, name }）
  const [renamingMarker, setRenamingMarker] = useState<{ time: number; name: string } | null>(null);
  // C5: 画布设置弹层（分辨率/帧率）
  const [canvasSettingsOpen, setCanvasSettingsOpen] = useState(false);
  const updateTimelineMeta = useTimelineStore((s) => s.updateTimelineMeta);
  // G1: 版本历史弹层
  const [historyOpen, setHistoryOpen] = useState(false);
  const projectId = useProjectStore((s) => s.projectId);
  const [versions, setVersions] = useState<TimelineVersionEntry[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const refreshVersions = useCallback(async () => {
    if (!projectId) return;
    setHistoryBusy(true);
    setHistoryError(null);
    try {
      setVersions(await versionApi.list(projectId));
    } catch {
      setHistoryError('版本加载失败（后端离线？）');
    } finally {
      setHistoryBusy(false);
    }
  }, [projectId]);

  const handleSnapshot = useCallback(async () => {
    if (!projectId) return;
    setHistoryBusy(true);
    setHistoryError(null);
    try {
      await versionApi.snapshot(projectId, `手动快照 ${new Date().toLocaleTimeString()}`);
      await refreshVersions();
    } catch {
      setHistoryError('快照保存失败（时间线为空或后端离线）');
    } finally {
      setHistoryBusy(false);
    }
  }, [projectId, refreshVersions]);

  const handleRestore = useCallback(async (position: number) => {
    if (!projectId) return;
    setHistoryBusy(true);
    setHistoryError(null);
    try {
      const res = await versionApi.restore(projectId, position);
      useTimelineStore.getState().setTimeline(res.timeline);
      mediaManager.registerTimeline(res.timeline);
      await refreshVersions();
    } catch {
      setHistoryError('恢复失败（后端离线或版本已删除）');
    } finally {
      setHistoryBusy(false);
    }
  }, [projectId, refreshVersions]);

  const handleClearVersions = useCallback(async () => {
    if (!projectId) return;
    setHistoryBusy(true);
    setHistoryError(null);
    try {
      await versionApi.clear(projectId);
      setVersions([]);
    } catch {
      setHistoryError('清空失败（后端离线）');
    } finally {
      setHistoryBusy(false);
    }
  }, [projectId]);

  // Instantiate engine
  useEffect(() => {
    if (!canvasRef.current) return;
    const engine = new TimelineEngine(canvasRef.current);
    engineRef.current = engine;

    // M8: 引擎标记变更 → 写回 store（随项目 autosave 持久化）
    engine.onMarkersChange = (m) => useTimelineStore.getState().setTimelineMarkers(m);
    // M8: 双击已有标记 → 打开命名输入框
    engine.onMarkerRename = (time) => {
      const existing = useTimelineStore.getState().timeline.markers?.find((x) => Math.abs(x.time - time) < 0.01);
      setRenamingMarker({ time, name: existing?.name ?? '' });
    };
    // 初始同步 store 标记 → 引擎
    engine.setMarkers(useTimelineStore.getState().timeline.markers ?? []);

    // M14: 范围工具两击设置 In/Out 区间
    engine.onRangePoint = (t) => {
      const sel = useSelectionStore.getState();
      if (sel.rangeStart == null) {
        sel.setRange(Math.max(0, t), null);
        sel.setRangeSelecting(true);
      } else {
        const a = sel.rangeStart;
        const b = Math.max(0, t);
        sel.setRange(Math.min(a, b), Math.max(a, b));
        sel.setRangeSelecting(false);
        sel.setToolMode('select');
        usePreviewStore.getState().setLoopRegion({ start: Math.min(a, b), end: Math.max(a, b) });
      }
    };

    const handleResize = () => engine.resize();
    const ro = new ResizeObserver(handleResize);
    if (containerRef.current) ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  // M8: store 标记（项目加载/撤销/Agent 快照）→ 引擎同步（引擎自己写回的不触发循环）
  useEffect(() => {
    engineRef.current?.setMarkers(timeline.markers ?? []);
  }, [timeline.markers]);

  const handleRenameCommit = (name: string) => {
    if (renamingMarker && engineRef.current) {
      engineRef.current.renameMarker(renamingMarker.time, name.trim());
    }
    setRenamingMarker(null);
  };

  // Register timeline-scoped shortcuts with the global KeybindingEngine
  // (avoids dual-fire conflicts with the centralized engine)
  useEffect(() => {
    const unsub = keybindingEngine.registerMany([
      {
        id: 'timeline-zoom-in', combo: '=', label: '放大时间轴', category: '时间轴',
        handler: () => engineRef.current?.zoomIn(),
      },
      {
        id: 'timeline-zoom-out', combo: '-', label: '缩小时间轴', category: '时间轴',
        handler: () => engineRef.current?.zoomOut(),
      },
      {
        id: 'timeline-add-marker', combo: 'm', label: '添加标记 (M)', category: '时间轴',
        handler: () => engineRef.current?.addMarkerAtPlayhead(),
      },
      {
        id: 'timeline-next-marker', combo: 'shift+m', label: '跳到下一标记', category: '时间轴',
        handler: () => engineRef.current?.jumpToNextMarker(),
      },
      {
        id: 'timeline-prev-marker', combo: 'ctrl+shift+m', label: '跳到上一标记', category: '时间轴',
        handler: () => engineRef.current?.jumpToPrevMarker(),
      },
      {
        id: 'timeline-clear-markers', combo: 'ctrl+shift+delete', label: '清除所有标记', category: '时间轴',
        handler: () => engineRef.current?.clearMarkers(),
      },
      {
        id: 'timeline-next-edit', combo: 'arrowdown', label: '跳到下一编辑点', category: '时间轴',
        handler: () => engineRef.current?.jumpToNextEdit(),
      },
      {
        id: 'timeline-prev-edit', combo: 'arrowup', label: '跳到上一编辑点', category: '时间轴',
        handler: () => engineRef.current?.jumpToPrevEdit(),
      },
      {
        id: 'timeline-zoom-fit-sel', combo: 'shift+f', label: '缩放适配选中片段', category: '时间轴',
        handler: () => {
          const sel = useSelectionStore.getState().selectedClipIds;
          if (sel.length === 0) return;
          const tl = useTimelineStore.getState().timeline;
          let minT = Infinity, maxT = -Infinity;
          for (const tr of tl.tracks) {
            for (const c of tr.clips) {
              if (sel.includes(c.id)) {
                minT = Math.min(minT, c.start_sec);
                maxT = Math.max(maxT, c.start_sec + c.duration_sec);
              }
            }
          }
          if (minT < Infinity) {
            const dur = Math.max(0.5, maxT - minT);
            engineRef.current?.zoomPreset(dur * 1.3);
          }
        },
      },
      {
        id: 'timeline-ripple-delete', combo: 'shift+delete', label: '波纹删除', category: '编辑',
        handler: () => {
          const ids = useSelectionStore.getState().selectedClipIds;
          if (ids.length === 0) return;
          useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'ripple-delete');
          ids.forEach((id) => rippleDelete(id));
          useSelectionStore.getState().deselectAll();
        },
      },
    ]);
    return unsub;
  }, [removeClip, rippleDelete, splitClip]);

  const handleAddTrack = (kind: ClipKind) => addTrack(kind);

  const handleSplitAtPlayhead = () => {
    useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'split');
    const t = currentTimeSec;
    selectedClipIds.forEach((id) => splitClip(id, t));
  };

  const handleDelete = () => {
    useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'delete');
    selectedClipIds.forEach((id) => removeClip(id));
    useSelectionStore.getState().deselectAll();
  };

  const handleRippleDelete = () => {
    useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'ripple-delete');
    selectedClipIds.forEach((id) => rippleDelete(id));
    useSelectionStore.getState().deselectAll();
  };

  return (
    <div className="flex flex-col h-full bg-surface-container-low">
      {/* Timeline toolbar */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-outline-variant/30 shrink-0">
        <span className="text-label font-medium text-on-surface-variant mr-2 uppercase tracking-wide">
          时间轴
        </span>

        {/* Tool switcher */}
        <div className="flex items-center bg-surface-container rounded-cw-sm p-0.5 mr-2">
          <Tooltip content="选择工具 (V)">
            <button
              onClick={() => setToolMode('select')}
              className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${
                toolMode === 'select' ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M4 2l12 16-4.5-1.5L9 22l-2.5-1 2.5-5.5L4 14V2z"/></svg>
            </button>
          </Tooltip>
          <Tooltip content="剃刀工具 (C)">
            <button
              onClick={() => setToolMode('razor')}
              className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${
                toolMode === 'razor' ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <Scissors className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="范围选择 (R)">
            <button
              onClick={() => setToolMode('range')}
              className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${
                toolMode === 'range' ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="3 2"/></svg>
            </button>
          </Tooltip>
        </div>

        {/* Snap toggle */}
        <Tooltip content={snapEnabled ? '吸附：开' : '吸附：关'}>
          <button
            onClick={() => setSnapEnabled(!snapEnabled)}
            className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${
              snapEnabled ? 'text-snap-guide bg-snap-guide/10' : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Magnet className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        {/* Grid snap toggle */}
        <Tooltip content={snapToGrid ? `吸附到网格: ${snapGridSec}s` : '网格吸附：关'}>
          <button
            onClick={() => setSnapToGrid(!snapToGrid)}
            className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${
              snapToGrid ? 'text-snap-guide bg-snap-guide/10' : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Grid3x3 className="w-3.5 h-3.5" />
          </button>
        </Tooltip>

        {/* Split / Delete */}
        <Tooltip content="在播放头处分割 (S)">
          <button onClick={handleSplitAtPlayhead} disabled={selectedClipIds.length === 0}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer">
            <Scissors className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip content="删除选中 (Del)">
          <button onClick={handleDelete} disabled={selectedClipIds.length === 0}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-error disabled:opacity-30 transition-colors cursor-pointer">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip content="波纹删除 (Shift+Del) — 删除并闭合间隙">
          <button onClick={handleRippleDelete} disabled={selectedClipIds.length === 0}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-tertiary disabled:opacity-30 transition-colors cursor-pointer">
            <ChevronsLeft className="w-3.5 h-3.5" />
          </button>
        </Tooltip>

        {/* Track manager */}
        <div className="relative">
          <Tooltip content="轨道管理">
            <button onClick={() => setTrackMgrOpen(!trackMgrOpen)}
              className={cn('p-1.5 rounded-cw-xs transition-colors cursor-pointer',
                trackMgrOpen ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface')}>
              <Layers className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          {trackMgrOpen && (
            <TrackManagerDropdown onClose={() => setTrackMgrOpen(false)} />
          )}
        </div>

        {/* Marker controls */}
        <Tooltip content="添加标记 (M)">
          <button onClick={() => engineRef.current?.addMarkerAtPlayhead()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-marker transition-colors cursor-pointer">
            <Flag className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip content="删除最近标记">
          <button onClick={() => engineRef.current?.removeMarkerNearest()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-marker transition-colors cursor-pointer">
            <FlagOff className="w-3.5 h-3.5" />
          </button>
        </Tooltip>

        {/* M12: In/Out 区间播放按钮 */}
        <Tooltip content="设置入点 (I)">
          <button onClick={() => setMarkerIn()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-primary transition-colors cursor-pointer"
            aria-label="设置入点">
            <span className="w-3 h-3 rounded-sm bg-track-text inline-block" />
          </button>
        </Tooltip>
        <Tooltip content="设置出点 (O)">
          <button onClick={() => setMarkerOut()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-primary transition-colors cursor-pointer"
            aria-label="设置出点">
            <span className="w-3 h-3 rounded-sm bg-track-text/50 inline-block" />
          </button>
        </Tooltip>
        <Tooltip content="清除区间">
          <button onClick={() => setLoopRegion(null)}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-error transition-colors cursor-pointer"
            aria-label="清除区间">
            <X className="w-3.5 h-3.5" />
          </button>
        </Tooltip>

        <div className="flex-1" />

        {/* C5: 画布设置（分辨率/帧率） */}
        <div className="relative">
          <Tooltip content="画布设置">
            <button onClick={() => setCanvasSettingsOpen(!canvasSettingsOpen)}
              className={cn('p-1.5 rounded-cw-xs transition-colors cursor-pointer',
                canvasSettingsOpen ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface')}>
              <Settings className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          {canvasSettingsOpen && (
            <CanvasSettingsPopover
              width={timeline.width}
              height={timeline.height}
              fps={timeline.fps}
              onChange={(meta) => { updateTimelineMeta(meta); }}
              onClose={() => setCanvasSettingsOpen(false)}
            />
          )}
        </div>

        {/* G1: 版本历史 */}
        <div className="relative">
          <Tooltip content="版本历史">
            <button onClick={() => {
              setHistoryOpen(!historyOpen);
              if (!historyOpen) refreshVersions();
            }}
              className={cn('p-1.5 rounded-cw-xs transition-colors cursor-pointer',
                historyOpen ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:text-on-surface')}>
              <HistoryIcon className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          {historyOpen && (
            <VersionHistoryPopover
              versions={versions}
              busy={historyBusy}
              error={historyError}
              onSnapshot={handleSnapshot}
              onRestore={handleRestore}
              onClear={handleClearVersions}
              onClose={() => setHistoryOpen(false)}
            />
          )}
        </div>

        {/* Timecode readout */}
        <span className="font-mono text-mono text-primary bg-surface-container px-2 py-0.5 rounded-cw-xs mr-2">
          {formatTimecode(currentTimeSec, fps)}
        </span>

        {/* Zoom controls */}
        <Tooltip content="缩小 (-)">
          <button onClick={() => engineRef.current?.zoomOut()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip content="放大 (+)">
          <button onClick={() => engineRef.current?.zoomIn()}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
        <Tooltip content="缩放至 5s">
          <button onClick={() => engineRef.current?.zoomPreset(5)}
            className="px-1 py-0.5 rounded-cw-xs text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer font-mono">
            5s
          </button>
        </Tooltip>
        <Tooltip content="缩放至 10s">
          <button onClick={() => engineRef.current?.zoomPreset(10)}
            className="px-1 py-0.5 rounded-cw-xs text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer font-mono">
            10s
          </button>
        </Tooltip>
        <Tooltip content="缩放至 30s">
          <button onClick={() => engineRef.current?.zoomPreset(30)}
            className="px-1 py-0.5 rounded-cw-xs text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer font-mono">
            30s
          </button>
        </Tooltip>
        <Tooltip content="缩放至适配">
          <button onClick={() => engineRef.current?.zoomToFit(timeline.duration_sec)}
            className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </Tooltip>
      </div>

      {/* Canvas viewport */}
      <div
        ref={containerRef}
        className="flex-1 relative overflow-hidden min-h-0"
        onDragOver={(e) => {
          const types = e.dataTransfer.types;
          if (types.includes('application/x-clipwright-asset') || types.includes('text/plain')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            setDropActive(true);
          }
        }}
        onDragLeave={() => setDropActive(false)}
        onDrop={(e) => {
          setDropActive(false);
          const raw = e.dataTransfer.getData('application/x-clipwright-asset') || e.dataTransfer.getData('text/plain');
          if (!raw || !engineRef.current || !containerRef.current) return;
          e.preventDefault();
          try {
            const asset = JSON.parse(raw);
            const rect = containerRef.current.getBoundingClientRect();
            engineRef.current.dropAssetAt(e.clientX - rect.left, e.clientY - rect.top, asset);
          } catch (err) {
            console.warn('[TimelinePanel] drop parse failed:', err, raw);
          }
        }}
      >
        {/* Drop highlight overlay */}
        {dropActive && (
          <div className="absolute inset-0 z-10 pointer-events-none border-2 border-dashed border-primary/60 bg-primary/5 rounded-cw-sm" />
        )}
        {/* M8: 标记重命名输入框（定位在标尺对应 x） */}
        {renamingMarker && (
          <MarkerRenameInput
            time={renamingMarker.time}
            initialName={renamingMarker.name}
            engine={engineRef.current}
            onCommit={handleRenameCommit}
            onCancel={() => setRenamingMarker(null)}
          />
        )}
        <canvas
          ref={canvasRef}
          className="absolute inset-0 block"
          style={{ pointerEvents: 'auto' }}
        />
      </div>

      {/* Add track bar */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-t border-outline-variant/30 shrink-0">
        <span className="text-label-sm text-on-surface-variant mr-1">添加轨道:</span>
        {(['video', 'audio', 'text', 'image', 'caption', 'animation'] as ClipKind[]).map((kind) => (
          <button
            key={kind}
            onClick={() => handleAddTrack(kind)}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-cw-full text-label-sm
              bg-surface-container text-on-surface-variant hover:bg-primary-container hover:text-on-primary-container
              transition-colors duration-short3 cursor-pointer"
          >
            <Plus className="w-3 h-3" />
            {kindLabel(kind)}
          </button>
        ))}
        <div className="flex-1" />
        <span className="text-caption text-on-surface-variant/60 font-mono">
          {timeline.tracks.length} 轨 · {timeline.duration_sec.toFixed(1)}s · {timeline.fps}fps
        </span>
      </div>
    </div>
  );
}

/**
 * TrackManagerDropdown — per-track controls: lock, mute, reorder, delete.
 */
function TrackManagerDropdown({ onClose }: { onClose: () => void }) {
  const tracks = useTimelineStore((s) => s.timeline.tracks);
  const toggleTrackLock = useTimelineStore((s) => s.toggleTrackLock);
  const toggleTrackMute = useTimelineStore((s) => s.toggleTrackMute);
  const toggleTrackHidden = useTimelineStore((s) => s.toggleTrackHidden); // M7
  const reorderTrack = useTimelineStore((s) => s.reorderTrack);
  const removeTrack = useTimelineStore((s) => s.removeTrack);

  const handleRemove = (trackId: string) => {
    useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'remove-track');
    removeTrack(trackId);
  };

  return (
    <>
      {/* click-outside backdrop */}
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute left-0 top-full mt-1.5 z-50 w-[260px] bg-surface-container-high border border-outline-variant/50
        rounded-cw-md shadow-2xl shadow-black/50 overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-outline-variant/30">
          <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">轨道 ({tracks.length})</span>
          <button onClick={onClose} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="max-h-[280px] overflow-y-auto p-1.5 space-y-1">
          {tracks.length === 0 && (
            <p className="text-label-sm text-on-surface-variant text-center py-4">暂无轨道</p>
          )}
          {tracks.map((track, i) => {
            const color = TRACK_COLORS[track.kind] ?? '#4F8CFF';
            return (
              <div key={track.id}
                className="flex items-center gap-2 px-2 py-1.5 rounded-cw-sm bg-surface-container hover:bg-surface
                  border border-outline-variant/20 transition-colors duration-short3 group">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
                <span className="text-label-sm text-on-surface truncate flex-1">{track.name}</span>

                <button onClick={() => toggleTrackLock(track.id)} title={track.locked ? '解锁' : '锁定'}
                  className={cn('p-1 rounded-cw-xs transition-colors cursor-pointer',
                    track.locked ? 'text-track-text' : 'text-on-surface-variant/50 hover:text-on-surface')}>
                  {track.locked ? <Lock className="w-3 h-3" /> : <LockOpen className="w-3 h-3" />}
                </button>
                <button onClick={() => toggleTrackMute(track.id)} title={track.muted ? '取消静音' : '静音'}
                  className={cn('p-1 rounded-cw-xs transition-colors cursor-pointer',
                    track.muted ? 'text-error' : 'text-on-surface-variant/50 hover:text-on-surface')}>
                  {track.muted ? <VolumeX className="w-3 h-3" /> : <Volume2 className="w-3 h-3" />}
                </button>
                {/* M7: 隐藏/独显 */}
                <button onClick={() => toggleTrackHidden(track.id)} title={track.hidden ? '显示轨道' : '隐藏轨道'}
                  className={cn('p-1 rounded-cw-xs transition-colors cursor-pointer',
                    track.hidden ? 'text-primary' : 'text-on-surface-variant/50 hover:text-on-surface')}>
                  {track.hidden ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                </button>
                <button onClick={() => reorderTrack(track.id, i - 1)} disabled={i === 0} title="上移"
                  className="p-1 rounded-cw-xs text-on-surface-variant/50 hover:text-on-surface disabled:opacity-20 transition-colors cursor-pointer">
                  <ArrowUp className="w-3 h-3" />
                </button>
                <button onClick={() => reorderTrack(track.id, i + 1)} disabled={i === tracks.length - 1} title="下移"
                  className="p-1 rounded-cw-xs text-on-surface-variant/50 hover:text-on-surface disabled:opacity-20 transition-colors cursor-pointer">
                  <ArrowDown className="w-3 h-3" />
                </button>
                <button onClick={() => handleRemove(track.id)} title="删除轨道"
                  className="p-1 rounded-cw-xs text-on-surface-variant/50 hover:text-error opacity-0 group-hover:opacity-100 transition-all cursor-pointer">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

function kindLabel(kind: ClipKind): string {
  const map: Record<ClipKind, string> = {
    video: '视频', audio: '音频', text: '文字', image: '图片',
    caption: '字幕', shape: '形状', waveform: '波形', animation: '动画',
  };
  return map[kind];
}

/**
 * C5: 画布设置弹层 — 修改时间线分辨率与帧率（updateTimelineMeta）。
 * 提交即生效（history 在编辑器中由 autosave 承担），输入做边界钳制。
 */
function CanvasSettingsPopover({
  width, height, fps, onChange, onClose,
}: {
  width: number;
  height: number;
  fps: number;
  onChange: (meta: { width: number; height: number; fps: number }) => void;
  onClose: () => void;
}) {
  const [w, setW] = useState(String(width));
  const [h, setH] = useState(String(height));
  const [f, setF] = useState(String(fps));
  const [aspectLock, setAspectLock] = useState(true);

  const clampInt = (v: string, min: number, max: number, fallback: number) => {
    const n = Math.round(Number(v));
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  };

  const apply = () => {
    const cw = clampInt(w, 16, 7680, width);
    const ch = clampInt(h, 16, 4320, height);
    const cf = clampInt(f, 1, 120, fps);
    if (cw !== width || ch !== height || cf !== fps) onChange({ width: cw, height: ch, fps: cf });
    onClose();
  };

  const aspect = width > 0 && height > 0 ? width / height : 16 / 9;

  const onW = (v: string) => {
    setW(v);
    if (aspectLock) {
      const n = clampInt(v, 16, 7680, width);
      setH(String(Math.round(n / aspect)));
    }
  };

  const onH = (v: string) => {
    setH(v);
    if (aspectLock) {
      const n = clampInt(v, 16, 4320, height);
      setW(String(Math.round(n * aspect)));
    }
  };

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full mt-1.5 z-50 w-[240px] bg-surface-container-high border border-outline-variant/50
        rounded-cw-md shadow-2xl shadow-black/50 overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-outline-variant/30">
          <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">画布设置</span>
          <button onClick={onClose} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="p-3 space-y-2.5">
          <div className="flex items-center gap-2">
            <label className="text-label-sm text-on-surface-variant w-14 shrink-0">宽度</label>
            <input value={w} onChange={(e) => onW(e.target.value)} inputMode="numeric"
              className="flex-1 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-label-sm text-on-surface-variant w-14 shrink-0">高度</label>
            <input value={h} onChange={(e) => onH(e.target.value)} inputMode="numeric"
              className="flex-1 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-label-sm text-on-surface-variant w-14 shrink-0">帧率</label>
            <input value={f} onChange={(e) => setF(e.target.value)} inputMode="numeric"
              className="flex-1 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
          </div>
          <label className="flex items-center gap-2 text-label-sm text-on-surface-variant cursor-pointer select-none">
            <input type="checkbox" checked={aspectLock} onChange={(e) => setAspectLock(e.target.checked)}
              className="accent-primary" />
            锁定宽高比
          </label>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={onClose}
              className="px-3 py-1 rounded-cw-xs text-label-sm text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              取消
            </button>
            <button onClick={apply}
              className="px-3 py-1 rounded-cw-xs text-label-sm bg-primary text-on-primary hover:bg-primary/90 transition-colors cursor-pointer">
              应用
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * G1: 版本历史弹层 — 列出项目快照，支持 保存快照 / 恢复 / 清空。
 */
function VersionHistoryPopover({
  versions, busy, error, onSnapshot, onRestore, onClear, onClose,
}: {
  versions: TimelineVersionEntry[];
  busy: boolean;
  error: string | null;
  onSnapshot: () => void;
  onRestore: (position: number) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full mt-1.5 z-50 w-[300px] bg-surface-container-high border border-outline-variant/50
        rounded-cw-md shadow-2xl shadow-black/50 overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-outline-variant/30">
          <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">版本历史</span>
          <div className="flex items-center gap-1">
            <button onClick={onSnapshot} disabled={busy}
              className="px-2 py-0.5 rounded-cw-xs text-label-sm bg-primary text-on-primary hover:bg-primary/90 disabled:opacity-40 transition-colors cursor-pointer">
              保存快照
            </button>
            <button onClick={onClose} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
        <div className="max-h-[280px] overflow-y-auto p-1.5 space-y-1">
          {error && <p className="text-label-sm text-error px-2 py-1">{error}</p>}
          {!error && versions.length === 0 && (
            <p className="text-label-sm text-on-surface-variant text-center py-4">
              {busy ? '加载中…' : '暂无版本 — 点击「保存快照」记录当前时间线'}
            </p>
          )}
          {versions.map((v) => (
            <div key={v.version_id}
              className={cn('flex items-center gap-2 px-2 py-1.5 rounded-cw-sm border transition-colors',
                v.is_current ? 'border-primary/50 bg-primary/5' : 'border-outline-variant/20 bg-surface-container hover:bg-surface')}>
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: v.is_current ? '#4F8CFF' : '#34D399' }} />
              <div className="flex-1 min-w-0">
                <p className="text-label-sm text-on-surface truncate">{v.label || `版本 ${v.position + 1}`}</p>
                <p className="text-caption text-on-surface-variant/70 font-mono">
                  {v.is_current ? '当前' : new Date(v.time).toLocaleString()}
                </p>
              </div>
              {!v.is_current && (
                <button onClick={() => onRestore(v.position)} disabled={busy}
                  className="p-1 rounded-cw-xs text-on-surface-variant hover:text-primary disabled:opacity-40 transition-colors cursor-pointer"
                  title="恢复此版本">
                  <RotateCcw className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
        {versions.length > 0 && (
          <div className="px-3 py-1.5 border-t border-outline-variant/30 flex justify-end">
            <button onClick={onClear} disabled={busy}
              className="px-2 py-0.5 rounded-cw-xs text-label-sm text-error hover:bg-error/10 disabled:opacity-40 transition-colors cursor-pointer">
              清空全部版本
            </button>
          </div>
        )}
      </div>
    </>
  );
}

/**
 * M8: 标记重命名输入框 — 渲染在画布标尺上方，跟随标记 x 位置。
 * 提交（Enter/失焦）→ 引擎 renameMarker；Esc → 取消。
 */
function MarkerRenameInput({
  time, initialName, engine, onCommit, onCancel,
}: {
  time: number;
  initialName: string;
  engine: TimelineEngine | null;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialName);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const x = engine
    ? Math.max(0, HEADER_W + time * engine.zoom - engine.scrollX)
    : 8;

  return (
    <div
      className="absolute z-20"
      style={{ left: x, top: 2 }}
      onClick={(e) => e.stopPropagation()}
    >
      <input
        ref={inputRef}
        value={value}
        placeholder="标记名称"
        maxLength={64}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onCommit(value);
          if (e.key === 'Escape') onCancel();
        }}
        onBlur={() => onCommit(value)}
        className="w-40 px-1.5 py-0.5 text-label-sm rounded-cw-xs
          bg-surface-container-high border border-primary/60 text-on-surface
          outline-none shadow-lg shadow-black/40"
        aria-label="标记名称"
      />
    </div>
  );
}
