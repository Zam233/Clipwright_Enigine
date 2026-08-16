// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { applyTransitionAlpha } from './PreviewPanel';
import type { Clip } from '@/types/timeline';
import { createDefaultClip } from '@/types/timeline';

function clip(overrides: Partial<Clip>): Clip {
  return createDefaultClip({ id: 'c1', kind: 'video', track_id: 't1', ...overrides });
}

describe('applyTransitionAlpha (M11 转场可见性)', () => {
  it('无转场 → 透明度不变', () => {
    const c = clip({ duration_sec: 5 });
    expect(applyTransitionAlpha(0.8, c, 0.01)).toBeCloseTo(0.8, 5);
    expect(applyTransitionAlpha(0.8, c, 0.99)).toBeCloseTo(0.8, 5);
  });

  it('hard_cut → 不调制', () => {
    const c = clip({ duration_sec: 5, transition_in: 'hard_cut', transition_out: 'hard_cut' });
    expect(applyTransitionAlpha(0.8, c, 0.01)).toBeCloseTo(0.8, 5);
    expect(applyTransitionAlpha(0.8, c, 0.99)).toBeCloseTo(0.8, 5);
  });

  it('transition_in=fade → 开头淡入（0→1 线性）', () => {
    const c = clip({ duration_sec: 5, transition_in: 'fade', transition_duration_sec: 0.5 });
    // 0.5s 窗口在 localT 0→0.1
    expect(applyTransitionAlpha(1, c, 0)).toBeCloseTo(0, 5);
    expect(applyTransitionAlpha(1, c, 0.05)).toBeCloseTo(0.5, 5);
    expect(applyTransitionAlpha(1, c, 0.1)).toBeCloseTo(1, 5);
    expect(applyTransitionAlpha(1, c, 0.5)).toBeCloseTo(1, 5);
  });

  it('transition_out=dissolve → 结尾淡出（1→0 线性）', () => {
    const c = clip({ duration_sec: 5, transition_out: 'dissolve', transition_duration_sec: 0.5 });
    expect(applyTransitionAlpha(1, c, 0.9)).toBeCloseTo(1, 5);
    expect(applyTransitionAlpha(1, c, 0.95)).toBeCloseTo(0.5, 5);
    expect(applyTransitionAlpha(1, c, 1)).toBeCloseTo(0, 5);
  });

  it('与 clip.opacity 相乘调制（0.5 基础 × 淡入系数）', () => {
    const c = clip({ duration_sec: 5, transition_in: 'fade', transition_duration_sec: 0.5 });
    expect(applyTransitionAlpha(0.5, c, 0.05)).toBeCloseTo(0.25, 5);
  });

  it('无效转场类型（空/未知字符串）→ 不调制', () => {
    const c = clip({ duration_sec: 5, transition_in: 'not_a_real_type' });
    expect(applyTransitionAlpha(0.7, c, 0.01)).toBeCloseTo(0.7, 5);
  });
});
