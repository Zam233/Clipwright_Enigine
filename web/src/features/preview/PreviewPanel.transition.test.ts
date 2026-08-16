// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { applyTransitionAlpha } from './PreviewPanel';
import { interpolateProperties } from '@/features/timeline/engine/easing';
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

describe('M5 时间重映射（关键帧 speed 插值）', () => {
  it('双关键帧 speed 线性插值', () => {
    const props = interpolateProperties([
      { time: 0, properties: { speed: 0.5 } },
      { time: 1, properties: { speed: 2 } },
    ], 0.5);
    expect(props.speed).toBeCloseTo(1.25, 5);
  });

  it('播放头在前/后关键帧之外 → 取端点值', () => {
    const kfs = [
      { time: 0.2, properties: { speed: 0.5 } },
      { time: 0.8, properties: { speed: 2 } },
    ];
    expect(interpolateProperties(kfs, 0).speed).toBeCloseTo(0.5, 5);
    expect(interpolateProperties(kfs, 1).speed).toBeCloseTo(2, 5);
  });

  it('无 speed 关键帧 → 不返回 speed 属性（回退 clip.speed）', () => {
    const props = interpolateProperties([
      { time: 0, properties: { opacity: 0 } },
      { time: 1, properties: { opacity: 1 } },
    ], 0.5);
    expect(props.speed).toBeUndefined();
  });
});
