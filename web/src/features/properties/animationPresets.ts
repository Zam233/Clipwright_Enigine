/**
 * Animation presets — ready-made keyframe templates applied to a clip.
 * Property keys match the preview compositor's interpolation
 * (opacity / scale / position_x / position_y / rotation).
 */
import type { Keyframe } from '@/types/timeline';

export interface AnimationPreset {
  id: string;
  name: string;
  category: '入场' | '出场' | '强调' | '循环';
  icon: string;
  keyframes: Keyframe[];
}

export const ANIMATION_PRESETS: AnimationPreset[] = [
  {
    id: 'fade_in', name: '淡入', category: '入场', icon: '◐',
    keyframes: [
      { time: 0, properties: { opacity: 0 } },
      { time: 0.25, properties: { opacity: 1 }, easing: 'ease-out' },
    ],
  },
  {
    id: 'fade_out', name: '淡出', category: '出场', icon: '◑',
    keyframes: [
      { time: 0.75, properties: { opacity: 1 } },
      { time: 1, properties: { opacity: 0 }, easing: 'ease-in' },
    ],
  },
  {
    id: 'slide_up', name: '上滑入场', category: '入场', icon: '⬆',
    keyframes: [
      { time: 0, properties: { opacity: 0, position_y: 0.25 } },
      { time: 0.25, properties: { opacity: 1, position_y: 0 }, easing: 'ease-out-cubic' },
    ],
  },
  {
    id: 'slide_down', name: '下滑入场', category: '入场', icon: '⬇',
    keyframes: [
      { time: 0, properties: { opacity: 0, position_y: -0.25 } },
      { time: 0.25, properties: { opacity: 1, position_y: 0 }, easing: 'ease-out-cubic' },
    ],
  },
  {
    id: 'slide_left', name: '左滑入场', category: '入场', icon: '⬅',
    keyframes: [
      { time: 0, properties: { opacity: 0, position_x: 0.3 } },
      { time: 0.25, properties: { opacity: 1, position_x: 0 }, easing: 'ease-out-cubic' },
    ],
  },
  {
    id: 'zoom_in', name: '缩放进入', category: '入场', icon: '⊕',
    keyframes: [
      { time: 0, properties: { opacity: 0, scale: 0.6 } },
      { time: 0.3, properties: { opacity: 1, scale: 1 }, easing: 'ease-out-back' },
    ],
  },
  {
    id: 'zoom_out', name: '缩放退出', category: '出场', icon: '⊖',
    keyframes: [
      { time: 0.7, properties: { opacity: 1, scale: 1 } },
      { time: 1, properties: { opacity: 0, scale: 0.6 }, easing: 'ease-in-back' },
    ],
  },
  {
    id: 'pop', name: '弹出', category: '强调', icon: '✦',
    keyframes: [
      { time: 0, properties: { scale: 0.3, opacity: 0 } },
      { time: 0.2, properties: { scale: 1.15, opacity: 1 }, easing: 'ease-out-back' },
      { time: 0.35, properties: { scale: 1 }, easing: 'ease-in-out' },
    ],
  },
  {
    id: 'spin_in', name: '旋转入场', category: '入场', icon: '↻',
    keyframes: [
      { time: 0, properties: { opacity: 0, rotation: -90, scale: 0.5 } },
      { time: 0.35, properties: { opacity: 1, rotation: 0, scale: 1 }, easing: 'ease-out-cubic' },
    ],
  },
  {
    id: 'shake', name: '抖动', category: '强调', icon: '≈',
    keyframes: [
      { time: 0, properties: { position_x: 0 } },
      { time: 0.1, properties: { position_x: -0.02 } },
      { time: 0.2, properties: { position_x: 0.02 } },
      { time: 0.3, properties: { position_x: -0.015 } },
      { time: 0.4, properties: { position_x: 0.015 } },
      { time: 0.5, properties: { position_x: 0 } },
    ],
  },
  {
    id: 'pulse', name: '脉冲', category: '循环', icon: '◉',
    keyframes: [
      { time: 0, properties: { scale: 1 } },
      { time: 0.25, properties: { scale: 1.08 }, easing: 'ease-in-out' },
      { time: 0.5, properties: { scale: 1 }, easing: 'ease-in-out' },
      { time: 0.75, properties: { scale: 1.08 }, easing: 'ease-in-out' },
      { time: 1, properties: { scale: 1 }, easing: 'ease-in-out' },
    ],
  },
  {
    id: 'float', name: '漂浮', category: '循环', icon: '∿',
    keyframes: [
      { time: 0, properties: { position_y: 0 } },
      { time: 0.5, properties: { position_y: -0.03 }, easing: 'ease-in-out' },
      { time: 1, properties: { position_y: 0 }, easing: 'ease-in-out' },
    ],
  },
];

/** Deep-copy a preset's keyframes (so edits don't mutate the template). */
export function presetKeyframes(preset: AnimationPreset): Keyframe[] {
  return preset.keyframes.map((k) => ({
    time: k.time,
    properties: { ...k.properties },
    easing: k.easing,
  }));
}

/**
 * Backend animation definition — aligned with clipwright/schema/animation.py.
 * Returned by GET /api/animation/list | /onscreen | /transitions.
 */
export interface BackendAnimationDef {
  animation_id: string;
  name: string;
  type: 'onscreen' | 'text' | 'transition';
  easing?: string;
  keyframes?: Array<{ time: number; properties: Record<string, number> }>;
  [key: string]: unknown;
}

const PRESET_ICON_RULES: Array<[RegExp, string]> = [
  [/打字|逐字|高亮|typewriter|char/i, '✎'],
  [/旋转|rotate/i, '↻'],
  [/弹跳|bounce/i, '⇪'],
  [/脉冲|强调|emphas/i, '✦'],
  [/模糊|blur/i, '◌'],
  [/缩放|zoom|scale/i, '⊕'],
  [/滑|slide/i, '→'],
  [/淡|fade/i, '◐'],
];

/** 由后端动画名称推断一个图标字形（后端无图标字段）。 */
function presetIcon(name: string): string {
  for (const [re, glyph] of PRESET_ICON_RULES) if (re.test(name)) return glyph;
  return '◈';
}

/** 由后端动画名称/类型推断前端分类标签（入场/出场/强调/循环）。 */
function presetCategory(def: BackendAnimationDef): AnimationPreset['category'] {
  if (def.type === 'transition') return '出场';
  const n = def.name;
  if (/出|退|out/i.test(n)) return '出场';
  if (/强调|脉冲|抖动|闪烁|打字|逐字|高亮|emphas|pulse/i.test(n)) return '强调';
  if (/循环|loop|漂浮/i.test(n)) return '循环';
  return '入场';
}

/** 后端属性键 → 前端合成器识别的键（scale_x/scale_y→scale、translate_*→position_*、rotate→rotation）。 */
function normalizeProperties(props: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(props)) {
    if (k === 'translate_x') out.position_x = v;
    else if (k === 'translate_y') out.position_y = v;
    else if (k === 'rotate') out.rotation = v;
    else if (k === 'scale_x' || k === 'scale_y') out.scale = v;
    else out[k] = v;
  }
  return out;
}

/** 将单个后端动画定义转换为前端预设（无关键帧的转场定义返回 null）。 */
export function toAnimationPreset(def: BackendAnimationDef): AnimationPreset | null {
  if (!def.keyframes || def.keyframes.length === 0) return null;
  return {
    id: def.animation_id,
    name: def.name || def.animation_id,
    category: presetCategory(def),
    icon: presetIcon(def.name || def.animation_id),
    keyframes: def.keyframes.map((k) => ({
      time: k.time,
      properties: normalizeProperties(k.properties ?? {}),
      easing: def.easing,
    })),
  };
}

/** 合并多个后端列表（list/onscreen/transitions）并按 animation_id 去重转换为预设。 */
export function backendPresetsToPresets(defs: BackendAnimationDef[]): AnimationPreset[] {
  const seen = new Set<string>();
  const out: AnimationPreset[] = [];
  for (const def of defs) {
    if (seen.has(def.animation_id)) continue;
    seen.add(def.animation_id);
    const p = toAnimationPreset(def);
    if (p) out.push(p);
  }
  return out;
}
