/**
 * clipAttributeClipboard — 复制/粘贴片段属性（M3：跨项目）。
 *
 * 复制当前片段的样式/效果/播放属性快照，粘贴到目标片段（同项目或跨项目）。
 * localStorage 持久化（clipwright.clipAttributeClipboard），刷新页面/切换项目仍可用。
 */
import { create } from 'zustand';
import { loadPref, savePref } from '@/services/storage/localPrefs';
import type { Clip } from '@/types/timeline';

const STORAGE_KEY = 'clipAttributeClipboard';

/** 可粘贴的属性字段（粘贴时逐项校验类型兼容，避免把视频字段贴到文字上）。 */
export const COPYABLE_FIELDS = [
  'speed', 'volume', 'opacity', 'blend_mode', 'eq_preset',
  'audio_fade_in_sec', 'audio_fade_out_sec',
  'fx_brightness', 'fx_contrast', 'fx_saturation', 'fx_blur', 'fx_hue',
  'image_fit', 'font', 'font_size', 'font_color', 'text_align',
  'font_weight', 'font_italic', 'letter_spacing', 'stroke_width', 'stroke_color',
  'shadow_x', 'shadow_y', 'shadow_color', 'shadow_blur',
  'glow_color', 'glow_width', 'transition_in', 'transition_out', 'transition_duration_sec',
] as const;

export type CopyableField = (typeof COPYABLE_FIELDS)[number];

/** 每种轨道类型允许粘贴的字段子集（字段名 → 适用 kind 集合）。 */
const FIELD_KIND_ALLOW: Partial<Record<CopyableField, Clip['kind'][]>> = {
  eq_preset: ['audio', 'waveform'],
  audio_fade_in_sec: ['audio', 'waveform'],
  audio_fade_out_sec: ['audio', 'waveform'],
  fx_brightness: ['video', 'image'],
  fx_contrast: ['video', 'image'],
  fx_saturation: ['video', 'image'],
  fx_blur: ['video', 'image'],
  fx_hue: ['video', 'image'],
  image_fit: ['video', 'image'],
  font: ['text', 'caption'],
  font_size: ['text', 'caption'],
  font_color: ['text', 'caption'],
  text_align: ['text', 'caption'],
  font_weight: ['text', 'caption'],
  font_italic: ['text', 'caption'],
  letter_spacing: ['text', 'caption'],
  stroke_width: ['text', 'caption'],
  stroke_color: ['text', 'caption'],
  shadow_x: ['text', 'caption'],
  shadow_y: ['text', 'caption'],
  shadow_color: ['text', 'caption'],
  shadow_blur: ['text', 'caption'],
  glow_color: ['text', 'caption'],
  glow_width: ['text', 'caption'],
};

/** 从片段提取可粘贴属性快照（只取 COPYABLE_FIELDS 中的非空值）。 */
export function extractCopyableAttributes(clip: Clip): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of COPYABLE_FIELDS) {
    const v = clip[f];
    if (v !== undefined && v !== null) out[f] = v;
  }
  return out;
}

/** 目标片段可接受的字段（按 kind 过滤）。 */
export function filterFieldsForKind(fields: Record<string, unknown>, kind: Clip['kind']): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(fields)) {
    const allowed = FIELD_KIND_ALLOW[k as CopyableField];
    if (!allowed || allowed.includes(kind)) out[k] = v;
  }
  return out;
}

interface ClipAttrClipboardState {
  fields: Record<string, unknown> | null;
  sourceKind: Clip['kind'] | null;
  sourceTime: string;
  set: (fields: Record<string, unknown>, sourceKind: Clip['kind']) => void;
  clear: () => void;
}

function loadClipboard(): Pick<ClipAttrClipboardState, 'fields' | 'sourceKind' | 'sourceTime'> {
  const raw = loadPref<Record<string, unknown>>(STORAGE_KEY, {});
  if (!raw || typeof raw.fields !== 'object' || raw.fields === null) {
    return { fields: null, sourceKind: null, sourceTime: '' };
  }
  return {
    fields: raw.fields as Record<string, unknown>,
    sourceKind: (raw.sourceKind as Clip['kind']) ?? null,
    sourceTime: typeof raw.sourceTime === 'string' ? raw.sourceTime : '',
  };
}

export const useClipAttributeClipboard = create<ClipAttrClipboardState>((set) => ({
  ...loadClipboard(),

  set: (fields, sourceKind) => {
    const payload = { fields, sourceKind, sourceTime: new Date().toISOString() };
    savePref(STORAGE_KEY, payload);
    set(payload);
  },

  clear: () => {
    savePref(STORAGE_KEY, { fields: null, sourceKind: null, sourceTime: '' });
    set({ fields: null, sourceKind: null, sourceTime: '' });
  },
}));
