// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { hitTestTextClipForEdit, applyTransitionAlpha } from './PreviewPanel';
import type { Clip, Track } from '@/types/timeline';
import { createDefaultClip, createEmptyTimeline } from '@/types/timeline';

const FRAME = { fx: 100, fy: 100, fw: 800, fh: 450 };

function track(kind: 'text' | 'caption' | 'video', index: number, clip: Clip): Track {
  return { id: `t${index}`, name: kind, kind, index, locked: false, muted: false, clips: [clip] };
}

function textClip(overrides: Partial<Clip> = {}): Clip {
  return createDefaultClip({ id: 'c1', kind: 'text', track_id: 't0', start_sec: 0, duration_sec: 10, text: '你好', ...overrides });
}

describe('hitTestTextClipForEdit (C1 画布双击编辑文字)', () => {
  it('命中中心对齐文本锚点（帧中心）', () => {
    const clip = textClip();
    const hit = hitTestTextClipForEdit(500, 325, FRAME, [track('text', 0, clip)], 5);
    expect(hit).not.toBeNull();
    expect(hit!.clip.id).toBe('c1');
    expect(hit!.x).toBeCloseTo(500, 1);
  });

  it('playhead 不在片段范围内 → 未命中', () => {
    const clip = textClip({ start_sec: 20, duration_sec: 5 });
    expect(hitTestTextClipForEdit(500, 325, FRAME, [track('text', 0, clip)], 5)).toBeNull();
  });

  it('点击远离锚点 → 未命中', () => {
    const clip = textClip();
    expect(hitTestTextClipForEdit(100, 100, FRAME, [track('text', 0, clip)], 5)).toBeNull();
  });

  it('隐藏轨道 / 禁用片段不参与命中', () => {
    const clip = textClip();
    const hidden: Track = { ...track('text', 0, clip), hidden: true };
    expect(hitTestTextClipForEdit(500, 325, FRAME, [hidden], 5)).toBeNull();
    const disabled = textClip({ enabled: false });
    expect(hitTestTextClipForEdit(500, 325, FRAME, [track('text', 0, disabled)], 5)).toBeNull();
  });

  it('顶层 text 优先于下层 text（倒序命中）', () => {
    const low = textClip({ id: 'low', text: '下层' });
    const high = textClip({ id: 'high', text: '上层' });
    const tracks = [track('text', 0, low), track('text', 1, high)];
    const hit = hitTestTextClipForEdit(500, 325, FRAME, tracks, 5);
    expect(hit!.clip.id).toBe('high');
  });

  it('左对齐文本锚点偏左（fx + 5% fw + 变换偏移）', () => {
    const clip = textClip({ text_align: 'left', metadata: { transform: { x: 0.1 } } });
    const hit = hitTestTextClipForEdit(500, 325, FRAME, [track('text', 0, clip)], 5);
    expect(hit!.x).toBeCloseTo(FRAME.fx + FRAME.fw * 0.05 + 0.1 * FRAME.fw, 1);
  });

  it('caption 类型锚点位于字幕基线', () => {
    const clip = textClip({ kind: 'caption' });
    const hit = hitTestTextClipForEdit(500, FRAME.fy + FRAME.fh * 0.85, FRAME, [track('caption', 0, clip)], 5);
    expect(hit).not.toBeNull();
    expect(hit!.clip.kind).toBe('caption');
  });
});

describe('applyTransitionAlpha 与其他导出（防止回归）', () => {
  it('空时间线兼容', () => {
    const tl = createEmptyTimeline();
    expect(tl.markers).toEqual([]);
  });

  it('无转场不调制', () => {
    const c = textClip();
    expect(applyTransitionAlpha(1, c, 0.5)).toBeCloseTo(1, 5);
  });
});
