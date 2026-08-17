import React, { useCallback, useRef, useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { Tooltip } from '@/components/ui';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { AssetPanel } from '@/features/assets/AssetPanel';
import { PreviewPanel } from '@/features/preview/PreviewPanel';
import { TimelinePanel } from '@/features/timeline/components/TimelinePanel';
import { PropertiesPanel } from '@/features/properties/PropertiesPanel';
import { AgentPanel } from '@/features/agent/AgentPanel';
import { ReviewPanel } from '@/features/agent/ReviewPanel';
import { EditorToolbar } from '@/features/timeline/components/EditorToolbar';
import { useAgentStore } from '@/stores/agentStore';
import { useProjectStore } from '@/stores/projectStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { useHistoryStore } from '@/stores/historyStore';
import { useSettingsStore } from '@/stores/settingsStore';
import type { SelectionMode } from '@/stores/selectionStore';
import { formatTimecode } from '@/lib/utils';

/**
 * EditorLayout — 4-panel Premiere-style layout
 *
 * ┌──────────────────────────────────────────────────────────┐
 * │                    EditorToolbar                          │
 * ├──────────┬───────────────────────────┬───────────────────┤
 * │          │                           │                   │
 * │  Assets  │      Preview Window       │   Properties      │
 * │  Panel   │                           │   Panel           │
 * │          ├───────────────────────────┤                   │
 * │          │      Timeline Panel       │                   │
 * │          │                           │                   │
 * ├──────────┴───────────────────────────┴───────────────────┤
 * │                    Status Bar                             │
 * └──────────────────────────────────────────────────────────┘
 *
 * Agent panel docks to the right of Properties or as overlay.
 */
export function EditorLayout() {
  const { panels, panelWidths, timelineHeight, setPanelWidth, setTimelineHeight } =
    useWorkspaceStore();
  const reviewMode = useAgentStore((s) => s.reviewMode);
  const setReviewMode = useAgentStore((s) => s.setReviewMode);
  const creativeBrief = useAgentStore((s) => s.creativeBrief);
  const productionPlan = useAgentStore((s) => s.productionPlan);
  const handleCloseReview = useCallback(() => setReviewMode(null), [setReviewMode]);

  const [dragging, setDragging] = useState<'assets' | 'properties' | 'timeline' | 'agent' | null>(null);
  const dragStartRef = useRef({ x: 0, y: 0, w: 0, h: 0 });
  const activeDragRef = useRef<{
    cleanup: () => void;
  } | null>(null);

  // Ensure drag listeners are cleaned up on unmount (memory leak prevention)
  useEffect(() => {
    return () => {
      if (activeDragRef.current) {
        activeDragRef.current.cleanup();
        activeDragRef.current = null;
      }
    };
  }, []);

  // Shared drag core — both the mouse and pointer (touch/pen) paths funnel into
  // this so the resize math stays identical. Idempotent per coordinate: if both
  // pointer and compatibility mouse events fire for one gesture, applying the
  // same delta twice yields the same width.
  const beginDividerDrag = useCallback(
    (panel: 'assets' | 'properties' | 'timeline' | 'agent', clientX: number, clientY: number) => {
      // If a gesture fired both pointerdown and mousedown, drop the previous
      // listeners before re-arming so a single drag never accumulates doubles.
      if (activeDragRef.current) {
        activeDragRef.current.cleanup();
        activeDragRef.current = null;
      }

      setDragging(panel);
      dragStartRef.current = {
        x: clientX,
        y: clientY,
        w: panel === 'assets' ? panelWidths.assets
          : panel === 'agent' ? panelWidths.agent
          : panelWidths.properties,
        h: timelineHeight,
      };

      const applyDrag = (mx: number, my: number) => {
        const dx = mx - dragStartRef.current.x;
        const dy = my - dragStartRef.current.y;
        if (panel === 'assets') {
          setPanelWidth('assets', dragStartRef.current.w + dx);
        } else if (panel === 'properties') {
          setPanelWidth('properties', dragStartRef.current.w - dx);
        } else if (panel === 'agent') {
          // BUG2 修复：Agent 面板的 divider 位于面板左侧（与 properties 同侧），
          // 向右拖（dx>0）面板应收窄、向左拖（dx<0）应加宽 → 用 w - dx。
          setPanelWidth('agent', dragStartRef.current.w - dx);
        } else if (panel === 'timeline') {
          setTimelineHeight(dragStartRef.current.h - dy);
        }
      };

      const handleMouseMove = (ev: MouseEvent) => applyDrag(ev.clientX, ev.clientY);
      const handlePointerMove = (ev: PointerEvent) => applyDrag(ev.clientX, ev.clientY);

      const endDrag = () => {
        setDragging(null);
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        document.removeEventListener('pointermove', handlePointerMove);
        document.removeEventListener('pointerup', handlePointerUp);
        activeDragRef.current = null;
      };
      const handleMouseUp = () => endDrag();
      const handlePointerUp = () => endDrag();

      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.addEventListener('pointermove', handlePointerMove);
      document.addEventListener('pointerup', handlePointerUp);
      activeDragRef.current = { cleanup: endDrag };
    },
    [panelWidths, timelineHeight, setPanelWidth, setTimelineHeight],
  );

  const handleDividerMouseDown = useCallback(
    (panel: 'assets' | 'properties' | 'timeline' | 'agent', e: React.MouseEvent) => {
      e.preventDefault();
      beginDividerDrag(panel, e.clientX, e.clientY);
    },
    [beginDividerDrag],
  );

  // Fallback for touch/pen and pointer-event-only browsers (defect F4).
  const handleDividerPointerDown = useCallback(
    (panel: 'assets' | 'properties' | 'timeline' | 'agent', e: React.PointerEvent) => {
      e.preventDefault();
      beginDividerDrag(panel, e.clientX, e.clientY);
    },
    [beginDividerDrag],
  );

  return (
    <div className="flex flex-col h-full w-full bg-surface overflow-hidden">
      {/* Top Toolbar */}
      <EditorToolbar />

      {/* Main Content Area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Assets Panel */}
        {panels.assets && (
          <>
            <div
              className="h-full overflow-hidden shrink-0"
              style={{ width: panelWidths.assets }}
            >
              <AssetPanel />
            </div>
            <div
              className={cn('panel-divider shrink-0', dragging === 'assets' && 'bg-primary')}
              onMouseDown={(e) => handleDividerMouseDown('assets', e)}
              onPointerDown={(e) => handleDividerPointerDown('assets', e)}
            />
          </>
        )}

        {/* Center: Preview + Timeline */}
        <div className="flex flex-col flex-1 overflow-hidden min-w-0">
          {/* Preview */}
          <div className="flex-1 overflow-hidden min-h-0">
            <PreviewPanel />
          </div>

          {/* Timeline divider */}
          <div
            className={cn('panel-divider-h shrink-0', dragging === 'timeline' && 'bg-primary')}
            onMouseDown={(e) => handleDividerMouseDown('timeline', e)}
            onPointerDown={(e) => handleDividerPointerDown('timeline', e)}
          />

          {/* Timeline */}
          <div
            className="shrink-0 overflow-hidden"
            style={{ height: timelineHeight }}
          >
            <TimelinePanel />
          </div>
        </div>

        {/* Right: Properties + Agent */}
        {panels.properties && (
          <>
            <div
              className={cn('panel-divider shrink-0', dragging === 'properties' && 'bg-primary')}
              onMouseDown={(e) => handleDividerMouseDown('properties', e)}
              onPointerDown={(e) => handleDividerPointerDown('properties', e)}
            />
            <div
              className="h-full overflow-hidden shrink-0 flex flex-col"
              style={{ width: panelWidths.properties }}
            >
              <PropertiesPanel />
            </div>
          </>
        )}

        {panels.agent && (
          <>
            <div
              className={cn('panel-divider shrink-0', dragging === 'agent' && 'bg-primary')}
              onMouseDown={(e) => handleDividerMouseDown('agent', e)}
              onPointerDown={(e) => handleDividerPointerDown('agent', e)}
            />
            <div
              className="h-full overflow-hidden shrink-0"
              style={{ width: panelWidths.agent }}
            >
              <AgentPanel />
            </div>
          </>
        )}
      </div>

      {/* Status Bar */}
      <StatusBar />

      {/* Full-screen Review Panel overlay */}
      {reviewMode && (
        <div
          className="fixed inset-0 z-50 bg-surface flex flex-col"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <ReviewPanel
            brief={reviewMode === 'brief' ? creativeBrief : null}
            planMarkdown={reviewMode === 'plan' ? (productionPlan?.markdown_content || productionPlan?.markdown || '') : undefined}
            onBack={handleCloseReview}
          />
        </div>
      )}
    </div>
  );
}

function StatusBar() {
  const isSaving = useProjectStore((s) => s.isSaving);
  const lastSavedAt = useProjectStore((s) => s.lastSavedAt);
  const saveError = useProjectStore((s) => s.saveError);
  const currentTime = usePreviewStore((s) => s.currentTimeSec);
  const fps = useTimelineStore((s) => s.timeline.fps);
  const duration = useTimelineStore((s) => s.timeline.duration_sec);
  const tracks = useTimelineStore((s) => s.timeline.tracks);
  const loopRegion = usePreviewStore((s) => s.loopRegion);
  const isLooping = usePreviewStore((s) => s.isLooping);
  const toolMode = useSelectionStore((s) => s.toolMode);
  const showFramesInRuler = useSettingsStore((s) => s.showFramesInRuler);
  const setShowFramesInRuler = useSettingsStore((s) => s.setShowFramesInRuler);
  const undoCount = useHistoryStore((s) => s.undoStack.length);
  const redoCount = useHistoryStore((s) => s.redoStack.length);
  const undoLabel = useHistoryStore((s) => s.undoStack.length > 0 ? s.undoStack[s.undoStack.length - 1].label : '');
  const redoLabel = useHistoryStore((s) => s.redoStack.length > 0 ? s.redoStack[s.redoStack.length - 1].label : '');
  const [showFrames, setShowFrames] = useState(false);

  let statusText = 'Ready';
  if (isSaving) statusText = '保存中…';
  else if (saveError) statusText = '保存失败';
  else if (lastSavedAt) {
    const t = new Date(lastSavedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    statusText = `已保存 ${t}`;
  }

  const currentFrame = Math.round(currentTime * fps);
  const totalFrames = Math.round(duration * fps);

  return (
    <div className="flex items-center justify-between px-3 py-1 bg-surface-dim border-t border-outline-variant/30 text-caption text-on-surface-variant shrink-0">
      <div className="flex items-center gap-3">
        <span>ClipWright v0.1.0</span>
        <span className="text-on-surface-variant/60">
          {formatTimecode(duration, fps)} · {tracks.length} 轨 · {tracks.reduce((s, tr) => s + tr.clips.length, 0)} 片段
        </span>
        {undoCount > 0 && <span className="text-on-surface-variant/60">撤销 {undoCount} ({undoLabel})</span>}
        {redoCount > 0 && <span className="text-on-surface-variant/60">重做 {redoCount} ({redoLabel})</span>}
        <span className="text-primary font-medium">{toolLabel(toolMode)}</span>
        {isLooping && loopRegion && (
          <span className="text-track-video">
            循环: {formatTimecode(loopRegion.start, fps)} – {formatTimecode(loopRegion.end, fps)}
          </span>
        )}
        {saveError && (
          <button
            onClick={() => useProjectStore.getState().requestSave()}
            className="px-2 py-0.5 rounded-cw-xs bg-error/15 text-error text-caption font-medium hover:bg-error/25 transition-colors cursor-pointer"
          >
            保存失败 · 点击重试
          </button>
        )}
      </div>
      <div className="flex items-center gap-3">
        <Tooltip content="标尺显示">
          <button
            onClick={() => setShowFramesInRuler(!showFramesInRuler)}
            className="font-mono hover:text-primary transition-colors cursor-pointer"
          >
            {showFramesInRuler ? '帧' : '时间码'}
          </button>
        </Tooltip>
        <button
          onClick={() => setShowFrames(!showFrames)}
          className="font-mono hover:text-primary transition-colors cursor-pointer"
          title="点击切换时间码/帧显示"
        >
          {showFrames ? `帧 ${currentFrame} / ${totalFrames}` : formatTimecode(currentTime, fps)}
        </button>
        <span className="font-mono">{statusText}</span>
      </div>
    </div>
  );
}

function toolLabel(mode: SelectionMode): string {
  switch (mode) {
    case 'select': return '选择';
    case 'razor': return '剃刀';
    case 'range': return '范围';
    default: return mode;
  }
}
