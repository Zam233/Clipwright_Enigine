import { describe, it, expect } from 'vitest';
import { sectionsForKind } from './sectionsForKind';
import type { ClipKind } from '@/types/timeline';

const ALL_KINDS: ClipKind[] = ['video', 'audio', 'text', 'image', 'caption', 'shape', 'waveform', 'animation'];

const COMMON = ['timing', 'notes', 'playback', 'transitions', 'animation', 'keyframes'];

describe('sectionsForKind', () => {
  it('covers all 8 ClipKinds', () => {
    for (const kind of ALL_KINDS) {
      expect(sectionsForKind(kind).length).toBeGreaterThan(0);
    }
  });

  it('video -> includes transform + fx', () => {
    const s = sectionsForKind('video');
    expect(s).toContain('transform');
    expect(s).toContain('fx');
    expect(s).not.toContain('image');
    expect(s).not.toContain('shape');
    expect(s).not.toContain('waveform');
    expect(s).not.toContain('text');
  });

  it('image -> includes image (fit) + transform + fx', () => {
    const s = sectionsForKind('image');
    expect(s).toContain('image');
    expect(s).toContain('transform');
    expect(s).toContain('fx');
    expect(s).not.toContain('shape');
  });

  it('text/caption -> includes text + captionStyle', () => {
    expect(sectionsForKind('text')).toEqual(expect.arrayContaining(['text', 'captionStyle']));
    expect(sectionsForKind('caption')).toEqual(expect.arrayContaining(['text', 'captionStyle']));
  });

  it('shape -> includes shape section only (no image/waveform/text)', () => {
    const s = sectionsForKind('shape');
    expect(s).toContain('shape');
    expect(s).not.toContain('image');
    expect(s).not.toContain('waveform');
    expect(s).not.toContain('text');
  });

  it('waveform -> includes waveform section only', () => {
    const s = sectionsForKind('waveform');
    expect(s).toContain('waveform');
    expect(s).not.toContain('shape');
    expect(s).not.toContain('image');
  });

  it('audio/animation -> only common sections', () => {
    expect(sectionsForKind('audio')).toEqual(COMMON);
    expect(sectionsForKind('animation')).toEqual(COMMON);
  });

  it('every kind keeps the common base sections', () => {
    for (const kind of ALL_KINDS) {
      for (const base of COMMON) {
        expect(sectionsForKind(kind)).toContain(base);
      }
    }
  });
});
