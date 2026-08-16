// @vitest-environment jsdom

import { describe, it, expect } from 'vitest';
import { splitScriptToCaptions } from './HomePage';

describe('splitScriptToCaptions · punctuation 标点模式', () => {
  it('标点模式：按全角冒号（U+FF1A）切分并丢弃冒号', () => {
    expect(splitScriptToCaptions('第一。第二：第三！第四？第五，第六；第七', 'punctuation')).toEqual([
      '第一',
      '第二',
      '第三！',
      '第四？',
      '第五',
      '第六',
      '第七',
    ]);
  });

  it('连用标点不产生空段（丢弃类标点连续出现时）', () => {
    expect(splitScriptToCaptions('第一，，第二', 'punctuation')).toEqual(['第一', '第二']);
    expect(splitScriptToCaptions('第一。。第二', 'punctuation')).toEqual(['第一', '第二']);
    // 保留类标点（！）连用：每个都被保留（既有行为，不改动）
    expect(splitScriptToCaptions('第一！！第二', 'punctuation')).toEqual(['第一！', '！', '第二']);
  });

  it('空串 / 纯空白返回空数组', () => {
    expect(splitScriptToCaptions('', 'punctuation')).toEqual([]);
    expect(splitScriptToCaptions('   ', 'punctuation')).toEqual([]);
  });

  it('纯丢弃类标点字符串返回空数组', () => {
    expect(splitScriptToCaptions('，。；：', 'punctuation')).toEqual([]);
  });

  it('纯保留类标点字符串每个字符独立成段', () => {
    expect(splitScriptToCaptions('！！！', 'punctuation')).toEqual(['！', '！', '！']);
  });

  it('仅 ！？ 保留在段尾，其余标点（，。；：）从段文本中丢弃', () => {
    expect(splitScriptToCaptions('甲，乙。丙；丁：戊！己？', 'punctuation')).toEqual([
      '甲',
      '乙',
      '丙',
      '丁',
      '戊！',
      '己？',
    ]);
  });

  it('无标点文本原样透传', () => {
    expect(splitScriptToCaptions('无标点文本', 'punctuation')).toEqual(['无标点文本']);
    expect(splitScriptToCaptions('hello world', 'punctuation')).toEqual(['hello world']);
  });

  it('period 句号模式行为保持：按 。！？ 切分并丢弃标点，无匹配时回退整段', () => {
    expect(splitScriptToCaptions('第一。第二！第三？', 'period')).toEqual(['第一', '第二', '第三']);
    expect(splitScriptToCaptions('第一：第二', 'period')).toEqual(['第一：第二']);
    expect(splitScriptToCaptions('', 'period')).toEqual([]);
  });
});
