// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import {
  extractCopyableAttributes, filterFieldsForKind,
  useClipAttributeClipboard, COPYABLE_FIELDS,
} from './clipAttributeClipboard';
import { createDefaultClip } from '@/types/timeline';

describe('clipAttributeClipboard (M3 跨项目复制/粘贴属性)', () => {
  beforeEach(() => {
    localStorage.clear();
    useClipAttributeClipboard.setState({ fields: null, sourceKind: null, sourceTime: '' });
  });

  it('extractCopyableAttributes 只取可粘贴字段的非空值', () => {
    const clip = createDefaultClip({
      id: 'c1', kind: 'video', track_id: 't1',
      volume: 0.8, opacity: 0.5, fx_brightness: 1.2, fx_blur: 3,
      text: '不应出现', font_size: null,
    });
    const attrs = extractCopyableAttributes(clip);
    expect(attrs.volume).toBe(0.8);
    expect(attrs.opacity).toBe(0.5);
    expect(attrs.fx_brightness).toBe(1.2);
    expect(attrs.fx_blur).toBe(3);
    expect(attrs.text).toBeUndefined(); // 非可粘贴字段
    expect(attrs.font_size).toBeUndefined(); // null 被跳过
    // 全部字段都来自 COPYABLE_FIELDS
    expect(Object.keys(attrs).every((k) => (COPYABLE_FIELDS as readonly string[]).includes(k))).toBe(true);
  });

  it('filterFieldsForKind 按目标类型过滤字段', () => {
    const fields = { volume: 0.8, fx_brightness: 1.2, font_size: 48, font_color: '#fff' };
    const video = filterFieldsForKind(fields, 'video');
    expect(video.volume).toBe(0.8);
    expect(video.fx_brightness).toBe(1.2);
    expect(video.font_size).toBeUndefined(); // 文字字段不贴到视频
    const text = filterFieldsForKind(fields, 'text');
    expect(text.volume).toBe(0.8); // 通用字段保留
    expect(text.font_size).toBe(48);
    expect(text.fx_brightness).toBeUndefined(); // 视频效果不贴到文字
    // 无限制字段（如 volume）对所有类型可用
    expect(filterFieldsForKind(fields, 'audio').volume).toBe(0.8);
  });

  it('set/clear/get 持久化到 localStorage', () => {
    useClipAttributeClipboard.getState().set({ volume: 1 }, 'video');
    const s = useClipAttributeClipboard.getState();
    expect(s.fields).toEqual({ volume: 1 });
    expect(s.sourceKind).toBe('video');
    const raw = JSON.parse(localStorage.getItem('clipwright.clipAttributeClipboard')!);
    expect(raw.fields.volume).toBe(1);

    useClipAttributeClipboard.getState().clear();
    expect(useClipAttributeClipboard.getState().fields).toBeNull();
  });
});
