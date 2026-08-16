import { useState, useEffect } from 'react';
import { useSelectionStore } from '@/stores/selectionStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { useHistoryStore } from '@/stores/historyStore';
import { usePreviewStore } from '@/stores/previewStore';
import { Slider, Badge } from '@/components/ui';
import { Tooltip } from '@/components/ui';
import { TRACK_COLORS } from '@/types/timeline';
import { EASING_NAMES, interpolateProperties } from '@/features/timeline/engine/easing';
import {
  ANIMATION_PRESETS, presetKeyframes, backendPresetsToPresets,
  type AnimationPreset, type BackendAnimationDef,
} from './animationPresets';
import { animationApi } from '@/services/api';
import { shouldPush } from './historyCoalesce';
import { sectionsForKind } from './sectionsForKind';
import type { Clip, ClipKind } from '@/types/timeline';
import {
  SlidersHorizontal, Type, Diamond, Plus, Trash2, ChevronLeft, ChevronRight,
  Move, RotateCcw, Wand2, Eye, EyeOff, Shapes, BarChart3, Image as ImageIcon,
} from 'lucide-react';

// Coalesce rapid history pushes (slider drag / number input / typing) into a single
// undo point, scoped PER CLIP so switching clips always opens a fresh window.
// The first push captures the pre-edit state; pushes within the window are skipped
// so one gesture ≠ dozens of undo steps. 合并窗口按片段隔离。
function pushHistoryCoalesced(clipId: string, label: string) {
  if (!shouldPush(clipId)) return;
  useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, label);
}

/**
 * PropertiesPanel — inspects and edits the selected clip's attributes.
 */
export function PropertiesPanel() {
  const selectedClipIds = useSelectionStore((s) => s.selectedClipIds);
  const timeline = useTimelineStore((s) => s.timeline);
  const updateClip = useTimelineStore((s) => s.updateClip);
  const updateTrackClips = useTimelineStore((s) => s.updateTrackClips);

  // Resolve first selected clip
  let clip: Clip | null = null;
  let trackKind = 'video';
  let trackName = '';
  let trackId = '';
  if (selectedClipIds.length > 0) {
    outer: for (const track of timeline.tracks) {
      for (const c of track.clips) {
        if (c.id === selectedClipIds[0]) {
          clip = c;
          trackKind = track.kind;
          trackName = track.name;
          trackId = track.id;
          break outer;
        }
      }
    }
  }

  const pushHistory = () => pushHistoryCoalesced(clip?.id ?? selectedClipIds[0] ?? '', 'property');

  const batchUpdate = (updates: Partial<Clip>) => {
    if (selectedClipIds.length > 1) {
      pushHistory();
      selectedClipIds.forEach((id) => updateClip(id, updates));
    } else if (clip) {
      updateClip(clip.id, updates);
    }
  };

  const set = (updates: Partial<Clip>) => {
    if (selectedClipIds.length > 1) {
      batchUpdate(updates);
    } else if (clip) {
      updateClip(clip.id, updates);
    }
  };

  // Style edits cascade to every clip on the same caption layer (ONE history point);
  // text clips keep per-clip updateClip.
  const applyStyle = (updates: Partial<Clip>) => {
    pushHistory();
    if (trackKind === 'caption' && trackId) {
      updateTrackClips(trackId, updates);
    } else {
      set(updates);
    }
  };

  const color = TRACK_COLORS[trackKind as keyof typeof TRACK_COLORS] ?? '#4F8CFF';

  // 按素材类型决定渲染哪些分区（sectionsForKind 为单一事实来源）
  const sections = clip ? sectionsForKind(trackKind as ClipKind) : [];

  return (
    <div className="flex flex-col h-full bg-surface-container-low">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-outline-variant/30 shrink-0">
        <SlidersHorizontal className="w-4 h-4 text-on-surface-variant" />
        <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">
          属性
        </span>
        {trackName && (
          <span className="text-caption text-on-surface-variant/60 ml-auto truncate">
            {trackName}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {selectedClipIds.length === 0 && <NoSelection />}
        {selectedClipIds.length > 1 && (
          <BatchEditSection count={selectedClipIds.length} set={set} pushHistory={pushHistory}
            initial={clip ? { speed: clip.speed, volume: clip.volume, opacity: clip.opacity } : { speed: 1, volume: 1, opacity: 1 }} />
        )}
        {clip && (
          <div className="p-3 space-y-4">
            {/* Clip identity */}
            <div className="flex items-center gap-2">
              <label title="点击更换标签颜色" className="cursor-pointer">
                <span className="block w-2.5 h-2.5 rounded-cw-full shrink-0" style={{ background: clip.label_color || color }} />
                <input
                  type="color"
                  value={clip.label_color || color}
                  onChange={(e) => { pushHistory(); set({ label_color: e.target.value }); }}
                  className="sr-only"
                />
              </label>
              <div className="flex-1 min-w-0">
                <p className="text-body-sm font-medium text-on-surface truncate">
                  {clipLabel(clip, trackKind)}
                </p>
                <p className="text-caption text-on-surface-variant font-mono">{clip.id}</p>
                {clip.asset_id && (
                  <p className="text-caption text-on-surface-variant/60 font-mono truncate" title={clip.asset_id}>
                    源: {clip.asset_id.length > 20 ? clip.asset_id.slice(0, 20) + '…' : clip.asset_id}
                  </p>
                )}
              </div>
              <Badge variant="info">{trackKind}</Badge>
              <Tooltip content={clip.enabled !== false ? '禁用片段' : '启用片段'}>
                <button
                  onClick={() => { pushHistory(); set({ enabled: clip.enabled === false }); }}
                  className={`p-1 rounded-cw-xs transition-colors cursor-pointer ${clip.enabled !== false ? 'text-on-surface-variant hover:text-on-surface' : 'text-error'}`}
                >
                  {clip.enabled !== false ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                </button>
              </Tooltip>
            </div>

            {/* Timing */}
            <Section title="时间">
              <Row label="起点 (s)">
                <NumberInput value={round2(clip.start_sec)} onChange={(v) => { pushHistory(); set({ start_sec: Math.max(0, v) }); }} />
              </Row>
              <Row label="时长 (s)">
                <NumberInput value={round2(clip.duration_sec)} onChange={(v) => { pushHistory(); set({ duration_sec: Math.max(0.1, v) }); }} />
              </Row>
              <Row label="素材偏移 (s)">
                <NumberInput value={round2(clip.source_offset_sec)} onChange={(v) => { pushHistory(); set({ source_offset_sec: Math.max(0, v) }); }} />
              </Row>
            </Section>

            {/* Notes */}
            <Section title="备注">
              <textarea
                value={clip.notes ?? ''}
                onChange={(e) => { pushHistory(); set({ notes: e.target.value || null }); }}
                rows={3}
                placeholder="添加备注…"
                className="w-full bg-surface-container rounded-cw-xs px-2 py-1.5 text-body-sm text-on-surface
                  outline-none border border-outline-variant/30 focus:border-primary resize-none
                  placeholder:text-on-surface-variant/40"
              />
            </Section>

            {/* Playback */}
            <Section title="播放">
              <Slider label="速度" min={0.25} max={4} step={0.25} value={clip.speed}
                onChange={(v) => { pushHistory(); set({ speed: v }); }} />
              <Slider label="音量" min={0} max={1} step={0.05} value={round2(clip.volume)}
                onChange={(v) => { pushHistory(); set({ volume: v }); }} />
              {trackKind === 'audio' && (
                <Row label="预设">
                  <select
                    value={clip.eq_preset ?? 'none'}
                    onChange={(e) => { pushHistory(); set({ eq_preset: e.target.value || null }); }}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                  >
                    {EQ_PRESETS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </Row>
              )}
              <Slider label="不透明度" min={0} max={1} step={0.05} value={round2(clip.opacity)}
                onChange={(v) => { pushHistory(); set({ opacity: v }); }} />
              {(trackKind === 'video' || trackKind === 'image') && (
                <Row label="混合模式">
                  <select
                    value={clip.blend_mode ?? 'normal'}
                    onChange={(e) => { pushHistory(); set({ blend_mode: e.target.value || null }); }}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary"
                  >
                    {BLEND_MODES.map((m) => (
                      <option key={m} value={m}>{BLEND_LABELS[m] ?? m}</option>
                    ))}
                  </select>
                </Row>
              )}
            </Section>

            {/* Shape (shape only) */}
            {sections.includes('shape') && (
              <ShapeSection clip={clip} pushHistory={pushHistory} set={set} />
            )}

            {/* Waveform (waveform only) */}
            {sections.includes('waveform') && (
              <WaveformSection clip={clip} pushHistory={pushHistory} set={set} />
            )}

            {/* Image fit (image only) */}
            {sections.includes('image') && (
              <ImageSection clip={clip} pushHistory={pushHistory} set={set} />
            )}

            {/* Transform (video / image) */}
            {sections.includes('transform') && sections.includes('fx') && (
              <>
                <TransformSection clip={clip} pushHistory={pushHistory} set={set} />
                <Section title="效果">
                  <Slider label="亮度" min={0} max={2} step={0.05} value={round2(clip.fx_brightness ?? 1)}
                    onChange={(v) => { pushHistory(); set({ fx_brightness: v }); }} />
                  <Slider label="对比度" min={0} max={2} step={0.05} value={round2(clip.fx_contrast ?? 1)}
                    onChange={(v) => { pushHistory(); set({ fx_contrast: v }); }} />
                  <Slider label="饱和度" min={0} max={2} step={0.05} value={round2(clip.fx_saturation ?? 1)}
                    onChange={(v) => { pushHistory(); set({ fx_saturation: v }); }} />
                  <Slider label="模糊 (px)" min={0} max={10} step={0.5} value={round2(clip.fx_blur ?? 0)}
                    onChange={(v) => { pushHistory(); set({ fx_blur: v }); }} />
                  <Slider label="色相 (°)" min={0} max={360} step={5} value={round2(clip.fx_hue ?? 0)}
                    onChange={(v) => { pushHistory(); set({ fx_hue: v }); }} />
                </Section>
              </>
            )}

            {/* Text content (style fields live in the 字幕样式 section below) */}
            {sections.includes('text') && (
              <Section title="文字" icon={<Type className="w-3 h-3" />}>
                <Row label="内容">
                  <textarea
                    value={clip.text ?? ''}
                      onChange={(e) => { pushHistory(); set({ text: e.target.value }); }}
                    rows={2}
                    className="w-full bg-surface-container rounded-cw-xs px-2 py-1.5 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary resize-none"
                  />
                </Row>
              </Section>
            )}

            {/* Caption / text style — on caption tracks every control cascades to the whole layer */}
            {sections.includes('captionStyle') && (
              <Section title="字幕样式" icon={<Type className="w-3 h-3" />}>
                <Row label="字体族">
                  <select
                    value={clip.font ?? 'Inter'}
                    onChange={(e) => applyStyle({ font: e.target.value })}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                  >
                    {FONT_FAMILIES.map((f) => (
                      <option key={f} value={f} style={{ fontFamily: f }}>{f}</option>
                    ))}
                  </select>
                </Row>
                <Row label="字号">
                  <NumberInput value={clip.font_size ?? 48} onChange={(v) => applyStyle({ font_size: Math.max(8, v) })} />
                </Row>
                <Row label="颜色">
                  <input
                    type="color"
                    value={clip.font_color ?? '#FFFFFF'}
                    onChange={(e) => applyStyle({ font_color: e.target.value })}
                    className="w-8 h-7 rounded-cw-xs border border-outline-variant/40 bg-transparent cursor-pointer"
                  />
                </Row>
                <Row label="粗细">
                  <select
                    value={clip.font_weight ?? 'normal'}
                    onChange={(e) => applyStyle({ font_weight: e.target.value })}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                  >
                    <option value="normal">常规</option>
                    <option value="bold">加粗</option>
                  </select>
                </Row>
                <Row label="斜体">
                  <select
                    value={clip.font_italic ? 'italic' : 'normal'}
                    onChange={(e) => applyStyle({ font_italic: e.target.value === 'italic' })}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                  >
                    <option value="normal">常规</option>
                    <option value="italic">斜体</option>
                  </select>
                </Row>
                <Row label="对齐">
                  <select
                    value={clip.text_align ?? (trackKind === 'caption' ? 'center' : 'left')}
                    onChange={(e) => applyStyle({ text_align: e.target.value as 'left' | 'center' | 'right' })}
                    className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
                      outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                  >
                    <option value="left">左对齐</option>
                    <option value="center">居中对齐</option>
                    <option value="right">右对齐</option>
                  </select>
                </Row>
                <Row label="字距">
                  <NumberInput value={clip.letter_spacing ?? 0} onChange={(v) => applyStyle({ letter_spacing: v })} />
                </Row>
                <Row label="描边宽度">
                  <NumberInput value={clip.stroke_width ?? 0} onChange={(v) => applyStyle({ stroke_width: Math.max(0, v) })} />
                </Row>
                <Row label="描边颜色">
                  <input
                    type="color"
                    value={clip.stroke_color ?? '#000000'}
                    onChange={(e) => applyStyle({ stroke_color: e.target.value })}
                    className="w-8 h-7 rounded-cw-xs border border-outline-variant/40 bg-transparent cursor-pointer"
                  />
                </Row>
                <Row label="阴影 X">
                  <NumberInput value={clip.shadow_x ?? 0} onChange={(v) => applyStyle({ shadow_x: v })} />
                </Row>
                <Row label="阴影 Y">
                  <NumberInput value={clip.shadow_y ?? 0} onChange={(v) => applyStyle({ shadow_y: v })} />
                </Row>
                <Row label="阴影模糊">
                  <NumberInput value={clip.shadow_blur ?? 0} onChange={(v) => applyStyle({ shadow_blur: Math.max(0, v) })} />
                </Row>
                <Row label="阴影颜色">
                  <input
                    type="color"
                    value={clip.shadow_color ?? '#000000'}
                    onChange={(e) => applyStyle({ shadow_color: e.target.value })}
                    className="w-8 h-7 rounded-cw-xs border border-outline-variant/40 bg-transparent cursor-pointer"
                  />
                </Row>
                <Row label="发光宽度">
                  <NumberInput value={clip.glow_width ?? 0} onChange={(v) => applyStyle({ glow_width: Math.max(0, v) })} />
                </Row>
                <Row label="发光颜色">
                  <input
                    type="color"
                    value={clip.glow_color ?? '#FFFFFF'}
                    onChange={(e) => applyStyle({ glow_color: e.target.value })}
                    className="w-8 h-7 rounded-cw-xs border border-outline-variant/40 bg-transparent cursor-pointer"
                  />
                </Row>
              </Section>
            )}

            {/* Transitions */}
            <Section title="转场">
              <Row label="入场">
                <TransitionSelect value={clip.transition_in ?? ''} onChange={(v) => { pushHistory(); set({ transition_in: v || null }); }} />
              </Row>
              <Row label="出场">
                <TransitionSelect value={clip.transition_out ?? ''} onChange={(v) => { pushHistory(); set({ transition_out: v || null }); }} />
              </Row>
              {clip.transition_in && (
                <Slider label="转场时长" min={0.1} max={2} step={0.1} value={round2(clip.transition_duration_sec ?? 0.5)}
                  onChange={(v) => { pushHistory(); set({ transition_duration_sec: v }); }} />
              )}
            </Section>

            {/* Animation presets + keyframe timeline */}
            <AnimationSection clip={clip} />

            {/* Keyframes */}
            <KeyframeEditor clip={clip} />
          </div>
        )}
      </div>

      {selectedClipIds.length > 1 && (
        <div className="px-3 py-2 border-t border-outline-variant/30 text-label-sm text-on-surface-variant shrink-0">
          已选择 {selectedClipIds.length} 个片段（显示第一个）
        </div>
      )}
    </div>
  );
}

/**
 * AnimationSection — apply animation presets and visualize keyframes on a
 * mini timeline strip (P4-7 / P4-9).
 */
function AnimationSection({ clip }: { clip: Clip }) {
  const updateClip = useTimelineStore((s) => s.updateClip);
  const setCurrentTime = usePreviewStore((s) => s.setCurrentTime);
  const currentTimeSec = usePreviewStore((s) => s.currentTimeSec);
  const [activeCat, setActiveCat] = useState<string>('入场');
  const [presets, setPresets] = useState<AnimationPreset[]>(ANIMATION_PRESETS);
  const [presetSource, setPresetSource] = useState<'backend' | 'static'>('static');

  // 挂载时请求后端动画预设（list + onscreen + transitions）；离线回退静态 ANIMATION_PRESETS
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [list, onscreen, transitions] = await Promise.all([
          animationApi.list(),
          animationApi.onscreen(),
          animationApi.transitions(),
        ]);
        const mapped = backendPresetsToPresets(
          [...list, ...onscreen, ...transitions] as unknown as BackendAnimationDef[],
        );
        if (alive && mapped.length > 0) {
          setPresets(mapped);
          setPresetSource('backend');
        }
      } catch {
        // 后端离线：保持静态预设兜底
      }
    })();
    return () => { alive = false; };
  }, []);

  const pushHistory = () => pushHistoryCoalesced(clip.id, 'animation');

  const applyPreset = (presetId: string) => {
    const preset = presets.find((p) => p.id === presetId);
    if (!preset) return;
    pushHistory();
    // Merge preset keyframes with existing ones (preset wins on same time)
    const existing = clip.keyframes ?? [];
    const presetKfs = presetKeyframes(preset);
    const presetTimes = new Set(presetKfs.map((k) => k.time));
    const kept = existing.filter((k) => !presetTimes.has(k.time));
    updateClip(clip.id, { keyframes: [...kept, ...presetKfs].sort((a, b) => a.time - b.time) });
  };

  const categories = ['入场', '出场', '强调', '循环'];
  const visible = presets.filter((p) => p.category === activeCat);

  // Playhead position within clip (0-1) for the strip
  const localT = clip.duration_sec > 0
    ? Math.min(1, Math.max(0, (currentTimeSec - clip.start_sec) / clip.duration_sec))
    : 0;
  const inClip = currentTimeSec >= clip.start_sec && currentTimeSec <= clip.start_sec + clip.duration_sec;

  const seekStrip = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setCurrentTime(clip.start_sec + frac * clip.duration_sec);
  };

  return (
    <Section title="动画 Animation" icon={<Wand2 className="w-3 h-3" />}>
      <p className="text-caption text-on-surface-variant/60 mb-2">
        {presetSource === 'backend' ? `在线预设 · ${presets.length} 个（来自后端）` : '离线预设 · 静态内置'}
      </p>
      {/* category tabs */}
      <div className="flex gap-1 mb-2">
        {categories.map((c) => (
          <button key={c} onClick={() => setActiveCat(c)}
            className={`flex-1 px-1.5 py-1 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
              activeCat === c ? 'bg-primary-container text-on-primary-container' : 'bg-surface-container-high text-on-surface-variant hover:text-on-surface'
            }`}>
            {c}
          </button>
        ))}
      </div>

      {/* preset grid */}
      <div className="grid grid-cols-3 gap-1.5 mb-3">
        {visible.length === 0 && (
          <p className="col-span-3 text-center text-caption text-on-surface-variant/50 py-1.5">该分类暂无预设</p>
        )}
        {visible.map((p) => (
          <button key={p.id} onClick={() => applyPreset(p.id)} title={`应用「${p.name}」动画`}
            className="flex flex-col items-center gap-1 px-1 py-2 rounded-cw-sm bg-surface-container-high border border-outline-variant/20
              hover:border-primary/50 hover:bg-primary/10 transition-all duration-short3 cursor-pointer group">
            <span className="text-body text-on-surface-variant group-hover:text-primary transition-colors">{p.icon}</span>
            <span className="text-caption text-on-surface-variant group-hover:text-on-surface transition-colors">{p.name}</span>
          </button>
        ))}
      </div>

      {/* keyframe timeline strip */}
      <div>
        <p className="text-caption text-on-surface-variant mb-1.5">关键帧时间轴（点击定位）</p>
        <div
          onClick={seekStrip}
          className="relative h-8 bg-surface rounded-cw-xs border border-outline-variant/30 cursor-pointer overflow-hidden group"
        >
          {/* playhead */}
          {inClip && (
            <span className="absolute top-0 bottom-0 w-px bg-playhead z-10" style={{ left: `${localT * 100}%` }} />
          )}
          {/* keyframe diamonds */}
          {clip.keyframes.map((kf, i) => (
            <span key={i}
              className="absolute top-1/2 w-2.5 h-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-keyframe-dot border border-black/30
                hover:scale-125 transition-transform z-20"
              style={{ left: `${kf.time * 100}%` }}
              title={`t=${kf.time.toFixed(2)}`}
            />
          ))}
          {clip.keyframes.length === 0 && (
            <span className="absolute inset-0 flex items-center justify-center text-caption text-on-surface-variant/50">
              无关键帧 — 应用预设或手动添加
            </span>
          )}
        </div>
      </div>
    </Section>
  );
}

/**
 * TransformSection — edit a video/image clip's static transform
 * (position / scale / rotation), stored in metadata.transform and applied
 * by the preview compositor.
 */
function TransformSection({ clip, pushHistory, set }: {
  clip: Clip;
  pushHistory: () => void;
  set: (u: Partial<Clip>) => void;
}) {
  const t = (clip.metadata?.transform ?? {}) as { x?: number; y?: number; scale?: number; rotation?: number };
  const x = t.x ?? 0;
  const y = t.y ?? 0;
  const scale = t.scale ?? 1;
  const rotation = t.rotation ?? 0;
  const { updateClip } = useTimelineStore.getState();

  const setTransform = (patch: Partial<{ x: number; y: number; scale: number; rotation: number }>) => {
    pushHistory();
    const apply = (c: Clip) => {
      const prev = (c.metadata?.transform ?? {}) as { x?: number; y?: number; scale?: number; rotation?: number };
      updateClip(c.id, {
        metadata: { ...c.metadata, transform: { x: prev.x ?? 0, y: prev.y ?? 0, scale: prev.scale ?? 1, rotation: prev.rotation ?? 0, ...patch } },
      });
    };
    const sel = useSelectionStore.getState().selectedClipIds;
    if (sel.length > 1) {
      // 多选时逐个合并各自 metadata，避免用第一个片段的 metadata 覆盖其他片段
      sel.forEach((id) => {
        const c = useTimelineStore.getState().getClip(id);
        if (c) apply(c);
      });
    } else {
      apply(clip);
    }
  };

  const reset = () => {
    pushHistory();
    const sel = useSelectionStore.getState().selectedClipIds;
    if (sel.length > 1) {
      sel.forEach((id) => {
        const c = useTimelineStore.getState().getClip(id);
        if (c) updateClip(c.id, { metadata: { ...c.metadata, transform: { x: 0, y: 0, scale: 1, rotation: 0 } } });
      });
    } else {
      set({ metadata: { ...clip.metadata, transform: { x: 0, y: 0, scale: 1, rotation: 0 } } });
    }
  };

  return (
    <Section title="变换 Transform" icon={<Move className="w-3 h-3" />}>
      <Slider label="位置 X" min={-1} max={1} step={0.01} value={round2(x)}
        onChange={(v) => setTransform({ x: v })} />
      <Slider label="位置 Y" min={-1} max={1} step={0.01} value={round2(y)}
        onChange={(v) => setTransform({ y: v })} />
      <Slider label="缩放" min={0.1} max={3} step={0.05} value={round2(scale)}
        onChange={(v) => setTransform({ scale: v })} />
      <Slider label="旋转 °" min={-180} max={180} step={1} value={rotation}
        onChange={(v) => setTransform({ rotation: v })} />
      <button onClick={reset}
        className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-cw-xs
          bg-surface-container-high text-on-surface-variant text-label-sm hover:text-on-surface
          hover:bg-surface-container transition-colors cursor-pointer">
        <RotateCcw className="w-3 h-3" /> 重置变换
      </button>
    </Section>
  );
}

/**
 * ShapeSection — shape clip 专属：形状类型 + 填充色。
 */
function ShapeSection({ clip, pushHistory, set }: {
  clip: Clip;
  pushHistory: () => void;
  set: (u: Partial<Clip>) => void;
}) {
  return (
    <Section title="形状" icon={<Shapes className="w-3 h-3" />}>
      <Row label="形状">
        <select
          value={clip.shape ?? 'rect'}
          onChange={(e) => { pushHistory(); set({ shape: e.target.value }); }}
          className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
        >
          <option value="rect">矩形</option>
          <option value="ellipse">椭圆</option>
        </select>
      </Row>
      <Row label="填充色">
        <input
          type="color"
          value={clip.fill ?? '#FFFFFF'}
          onChange={(e) => { pushHistory(); set({ fill: e.target.value }); }}
          className="w-8 h-7 rounded-cw-xs border border-outline-variant/40 bg-transparent cursor-pointer"
        />
      </Row>
    </Section>
  );
}

/**
 * WaveformSection — waveform clip 专属：柱数 + 柱宽。
 */
function WaveformSection({ clip, pushHistory, set }: {
  clip: Clip;
  pushHistory: () => void;
  set: (u: Partial<Clip>) => void;
}) {
  return (
    <Section title="波形" icon={<BarChart3 className="w-3 h-3" />}>
      <Row label="柱数">
        <NumberInput value={clip.bar_count ?? 32} onChange={(v) => { pushHistory(); set({ bar_count: Math.round(Math.min(256, Math.max(8, v))) }); }} />
      </Row>
      <Row label="柱宽">
        <NumberInput value={clip.bar_width ?? 0.5} onChange={(v) => { pushHistory(); set({ bar_width: Math.min(1, Math.max(0.1, v)) }); }} />
      </Row>
    </Section>
  );
}

/**
 * ImageSection — image clip 专属：适配方式 + 重置裁切区域。
 */
function ImageSection({ clip, pushHistory, set }: {
  clip: Clip;
  pushHistory: () => void;
  set: (u: Partial<Clip>) => void;
}) {
  return (
    <Section title="适配" icon={<ImageIcon className="w-3 h-3" />}>
      <Row label="适配方式">
        <select
          value={clip.image_fit ?? 'cover'}
          onChange={(e) => { pushHistory(); set({ image_fit: e.target.value as Clip['image_fit'] }); }}
          className="flex-1 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
        >
          <option value="cover">铺满 (cover)</option>
          <option value="contain">完整 (contain)</option>
          <option value="free">自由 (free)</option>
        </select>
      </Row>
      <button
        onClick={() => { pushHistory(); set({ image_rect: null }); }}
        disabled={!clip.image_rect}
        className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-cw-xs
          bg-surface-container-high text-on-surface-variant text-label-sm hover:text-on-surface
          hover:bg-surface-container transition-colors disabled:opacity-30 disabled:cursor-default cursor-pointer"
      >
        <RotateCcw className="w-3 h-3" /> 重置裁切区域
      </button>
    </Section>
  );
}

/**
 * KeyframeEditor — add/remove keyframes at the playhead, pick easing,
 * and jump between keyframes. Writes through timelineStore with history.
 */
function KeyframeEditor({ clip }: { clip: Clip }) {
  const addKeyframe = useTimelineStore((s) => s.addKeyframe);
  const removeKeyframe = useTimelineStore((s) => s.removeKeyframe);
  const updateClip = useTimelineStore((s) => s.updateClip);
  const currentTimeSec = usePreviewStore((s) => s.currentTimeSec);
  const setCurrentTime = usePreviewStore((s) => s.setCurrentTime);

  // Normalized playhead position within this clip (0-1)
  const localT = clip.duration_sec > 0
    ? Math.min(1, Math.max(0, (currentTimeSec - clip.start_sec) / clip.duration_sec))
    : 0;
  const inClip = currentTimeSec >= clip.start_sec && currentTimeSec <= clip.start_sec + clip.duration_sec;

  const pushHistory = () => pushHistoryCoalesced(clip.id, 'keyframe');

  // Current interpolated values at playhead (for the "add keyframe" snapshot)
  const liveProps = interpolateProperties(clip.keyframes, localT);
  const snapshotProps: Record<string, number> = Object.keys(liveProps).length > 0
    ? liveProps
    : { opacity: clip.opacity };

  const addAtPlayhead = () => {
    if (!inClip) return;
    pushHistory();
    addKeyframe(clip.id, Math.round(localT * 1000) / 1000, snapshotProps);
  };

  const removeAt = (time: number) => {
    pushHistory();
    removeKeyframe(clip.id, time);
  };

  const setEasing = (time: number, easing: string) => {
    pushHistory();
    const kfs = clip.keyframes.map((k) => (Math.abs(k.time - time) < 0.001 ? { ...k, easing } : k));
    updateClip(clip.id, { keyframes: kfs });
  };

  const jumpTo = (dir: 1 | -1) => {
    const times = clip.keyframes.map((k) => k.time).sort((a, b) => a - b);
    if (times.length === 0) return;
    const target = dir === 1
      ? times.find((t) => t > localT + 0.001)
      : [...times].reverse().find((t) => t < localT - 0.001);
    if (target !== undefined) {
      setCurrentTime(clip.start_sec + target * clip.duration_sec);
    }
  };

  return (
    <Section title="关键帧动画" icon={<Diamond className="w-3 h-3" />}>
      {/* add-at-playhead row */}
      <div className="flex items-center gap-2">
        <button
          onClick={addAtPlayhead}
          disabled={!inClip}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-cw-xs
            bg-primary-container text-on-primary-container text-label-sm font-medium
            hover:opacity-90 disabled:opacity-30 transition-opacity cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          {inClip ? `在播放头添加 (${localT.toFixed(2)})` : '播放头不在片段内'}
        </button>
        <button onClick={() => jumpTo(-1)} disabled={clip.keyframes.length === 0}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer"
          title="上一个关键帧">
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <button onClick={() => jumpTo(1)} disabled={clip.keyframes.length === 0}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface disabled:opacity-30 transition-colors cursor-pointer"
          title="下一个关键帧">
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>

      {clip.keyframes.length === 0 ? (
        <p className="text-label-sm text-on-surface-variant leading-relaxed">
          无关键帧。将播放头移到片段内，点击「添加」记录当前属性值，即可创建动画。
        </p>
      ) : (
        <div className="space-y-1.5">
          {clip.keyframes.map((kf, i) => (
            <div key={i} className="flex items-center gap-2 bg-surface-container rounded-cw-xs px-2 py-1.5 group">
              <Diamond className="w-3 h-3 text-keyframe-dot shrink-0" />
              <button
                onClick={() => setCurrentTime(clip.start_sec + kf.time * clip.duration_sec)}
                className="font-mono text-label-sm text-primary hover:underline cursor-pointer shrink-0"
                title="跳转到此关键帧"
              >
                {kf.time.toFixed(2)}
              </button>
              <select
                value={kf.easing ?? 'linear'}
                onChange={(e) => setEasing(kf.time, e.target.value)}
                className="flex-1 min-w-0 bg-surface rounded-cw-xs px-1.5 py-0.5 text-caption font-mono text-on-surface-variant
                  outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
                title="缓动函数"
              >
                {EASING_NAMES.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <button
                onClick={() => removeAt(kf.time)}
                className="p-1 rounded-cw-xs text-on-surface-variant hover:text-error opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
                title="删除关键帧"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function NoSelection() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      <div className="w-12 h-12 rounded-cw-full bg-surface-container flex items-center justify-center mb-3">
        <SlidersHorizontal className="w-5 h-5 text-on-surface-variant/50" />
      </div>
      <p className="text-body-sm text-on-surface font-medium mb-1">未选择片段</p>
      <p className="text-label-sm text-on-surface-variant leading-relaxed">
        在时间轴上点击任意片段，即可在此查看和编辑其属性。
      </p>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="flex items-center gap-1.5 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-2">
        {icon}
        {title}
      </h3>
      <div className="space-y-2 bg-surface-container/50 rounded-cw-sm p-2.5 border border-outline-variant/20">
        {children}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-label text-on-surface-variant whitespace-nowrap">{label}</span>
      <div className="flex items-center gap-1 flex-1 justify-end">{children}</div>
    </div>
  );
}

function NumberInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(String(round2(value)));
  const [focused, setFocused] = useState(false);
  // Sync from the clip value while the field is not being edited
  useEffect(() => {
    if (!focused) setText(String(round2(value)));
  }, [value, focused]);
  const commit = () => {
    const v = Number(text);
    // Infinity（如 1e999）会污染时间线时长并让合成器 NaN，必须拒绝
    if (text.trim() !== '' && !isNaN(v) && isFinite(v)) onChange(v);
    else setText(String(round2(value))); // revert invalid/empty input
  };
  return (
    <input
      type="number"
      step={0.1}
      value={text}
      onFocus={() => setFocused(true)}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => { setFocused(false); commit(); }}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
      className="w-20 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm font-mono text-on-surface
        outline-none border border-outline-variant/30 focus:border-primary text-right"
    />
  );
}

const TRANSITIONS = ['', 'hard_cut', 'fade', 'dissolve', 'glitch', 'pixel_dissolve', 'slide', 'wipe'];

const TRANSITION_LABELS: Record<string, string> = {
  '': '无', hard_cut: '硬切', fade: '淡入淡出', dissolve: '溶解', glitch: '故障',
  pixel_dissolve: '像素溶解', slide: '滑动', wipe: '擦除',
};

const BLEND_MODES = ['normal', 'multiply', 'screen', 'overlay', 'darken', 'lighten', 'color-dodge', 'color-burn', 'hard-light', 'soft-light', 'difference', 'exclusion'];

const BLEND_LABELS: Record<string, string> = {
  normal: '正常', multiply: '正片叠底', screen: '滤色', overlay: '叠加', darken: '变暗',
  lighten: '变亮', 'color-dodge': '颜色减淡', 'color-burn': '颜色加深', 'hard-light': '强光',
  'soft-light': '柔光', difference: '差值', exclusion: '排除',
};

const FONT_FAMILIES = ['Inter', 'Noto Sans SC', 'JetBrains Mono', 'system-ui', 'Arial', 'Helvetica', 'Georgia', 'Times New Roman', 'Courier New', 'Impact', 'Verdana'];

const EQ_PRESETS = [
  { value: 'none', label: '无' },
  { value: 'bass-boost', label: '低音增强' },
  { value: 'vocal-boost', label: '人声增强' },
  { value: 'treble-boost', label: '高音增强' },
  { value: 'bass-reduce', label: '降低低音' },
  { value: 'loudness', label: '响度均衡' },
  { value: 'podcast', label: '播客优化' },
  { value: 'voice', label: '语音清晰' },
];
function TransitionSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-28 bg-surface-container rounded-cw-xs px-2 py-1 text-body-sm text-on-surface
        outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
    >
      {TRANSITIONS.map((t) => (
        <option key={t} value={t}>{TRANSITION_LABELS[t] ?? t}</option>
      ))}
    </select>
  );
}

function BatchEditSection({ count, set, pushHistory, initial }: {
  count: number;
  set: (u: Partial<Clip>) => void;
  pushHistory: () => void;
  initial: Pick<Clip, 'speed' | 'volume' | 'opacity'>;
}) {
  return (
    <Section title={`批量编辑 · ${count} 个片段`}>
      <Slider label="速度（全部）" min={0.25} max={4} step={0.25} value={initial.speed}
        onChange={(v) => { pushHistory(); set({ speed: v }); }} />
      <Slider label="音量（全部）" min={0} max={1} step={0.05} value={initial.volume}
        onChange={(v) => { pushHistory(); set({ volume: v }); }} />
      <Slider label="不透明度（全部）" min={0} max={1} step={0.05} value={initial.opacity}
        onChange={(v) => { pushHistory(); set({ opacity: v }); }} />
    </Section>
  );
}

function clipLabel(clip: Clip, kind: string): string {
  if ((kind === 'text' || kind === 'caption') && clip.text) return clip.text;
  if (clip.metadata && typeof clip.metadata.title === 'string') return clip.metadata.title as string;
  if (clip.asset_id) return clip.asset_id;
  return kind;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
