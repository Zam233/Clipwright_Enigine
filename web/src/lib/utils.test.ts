import { describe, it, expect } from 'vitest';
import { formatTimecode, formatTimeShort } from './utils';

describe('formatTimecode', () => {
  it('formats positive seconds', () => {
    expect(formatTimecode(3661.5, 30)).toBe('01:01:01:15');
  });

  it('guards NaN / negative / bad fps', () => {
    expect(formatTimecode(NaN)).toBe('00:00:00:00');
    expect(formatTimecode(-1)).toBe('00:00:00:00');
    expect(formatTimecode(10, 0)).toBe('00:00:00:00');
    expect(formatTimecode(10, Infinity)).toBe('00:00:00:00');
  });
});

describe('formatTimeShort', () => {
  it('formats sub-minute values with s suffix', () => {
    expect(formatTimeShort(3.2)).toBe('3.2s');
  });

  it('rounds to 0.1s before splitting minutes', () => {
    expect(formatTimeShort(59.96)).toBe('1:00.0');
    expect(formatTimeShort(59.94)).toBe('59.9s');
  });

  it('formats minutes', () => {
    expect(formatTimeShort(83.4)).toBe('1:23.4');
  });

  it('guards invalid input', () => {
    expect(formatTimeShort(NaN)).toBe('0s');
    expect(formatTimeShort(-5)).toBe('0s');
    expect(formatTimeShort(Infinity)).toBe('0s');
  });
});
