import { describe, it, expect } from 'vitest';
import { applyMasterVolume } from './volume';

describe('applyMasterVolume', () => {
  it('multiplies clip and master volume', () => {
    expect(applyMasterVolume(1, 0.5)).toBe(0.5);
  });
  it('master 0 silences output', () => {
    expect(applyMasterVolume(1, 0)).toBe(0);
  });
  it('clamps clip volume above 1', () => {
    expect(applyMasterVolume(2, 1)).toBe(1);
  });
  it('clip 0 stays silent', () => {
    expect(applyMasterVolume(0, 1)).toBe(0);
  });
  it('clamps master above 1', () => {
    expect(applyMasterVolume(1, 2)).toBe(1);
  });
  it('clamps negative values to 0', () => {
    expect(applyMasterVolume(-1, -1)).toBe(0);
  });
});
