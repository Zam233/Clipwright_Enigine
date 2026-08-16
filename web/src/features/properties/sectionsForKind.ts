import type { ClipKind } from '@/types/timeline';

/** 属性面板分区标识——每个分区对应一组控件。 */
export type SectionId =
  | 'timing'
  | 'notes'
  | 'playback'
  | 'transform'
  | 'fx'
  | 'text'
  | 'captionStyle'
  | 'shape'
  | 'waveform'
  | 'image'
  | 'transitions'
  | 'animation'
  | 'keyframes';

/**
 * sectionsForKind — 按素材类型返回属性面板应渲染的分区集合。
 *
 * 规则（与既有渲染逻辑等价）：
 * - 全部类型：timing / notes / playback / transitions / animation / keyframes
 * - video：额外 transform + fx
 * - image：额外 image（适配方式） + transform + fx
 * - text / caption：额外 text（内容） + captionStyle（字幕样式）
 * - shape：额外 shape（形状/填充）
 * - waveform：额外 waveform（柱数/柱宽）
 */
export function sectionsForKind(kind: ClipKind): SectionId[] {
  switch (kind) {
    case 'video':
      return ['timing', 'notes', 'playback', 'transform', 'fx', 'transitions', 'animation', 'keyframes'];
    case 'image':
      return ['timing', 'notes', 'playback', 'image', 'transform', 'fx', 'transitions', 'animation', 'keyframes'];
    case 'text':
    case 'caption':
      return ['timing', 'notes', 'playback', 'text', 'captionStyle', 'transitions', 'animation', 'keyframes'];
    case 'shape':
      return ['timing', 'notes', 'playback', 'shape', 'transitions', 'animation', 'keyframes'];
    case 'waveform':
      return ['timing', 'notes', 'playback', 'waveform', 'transitions', 'animation', 'keyframes'];
    case 'audio':
    case 'animation':
      return ['timing', 'notes', 'playback', 'transitions', 'animation', 'keyframes'];
  }
}
