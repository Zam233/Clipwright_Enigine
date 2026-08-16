import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Extract a top-level selector block's body from CSS text (selector line-anchored). */
function extractBlock(css: string, selector: string): string {
  const m = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{`, 'm').exec(css);
  if (!m) throw new Error(`selector ${selector} not found`);
  const open = css.indexOf('{', m.index);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}') {
      depth--;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error(`unclosed block for ${selector}`);
}

function getVar(block: string, name: string): string {
  const m = block.match(new RegExp(`${name}:\\s*(#[0-9A-Fa-f]{6})`));
  if (!m) throw new Error(`var ${name} not found`);
  return m[1];
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

/** WCAG relative luminance (sRGB). */
function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(hexA: string, hexB: string): number {
  const la = relativeLuminance(hexA);
  const lb = relativeLuminance(hexB);
  const [lighter, darker] = la >= lb ? [la, lb] : [lb, la];
  return (lighter + 0.05) / (darker + 0.05);
}

describe('globals.css light theme contrast (U16b)', () => {
  it('--color-on-surface-variant vs --color-surface meets WCAG AA (>= 4.5:1)', () => {
    const css = readFileSync(resolve(__dirname, 'globals.css'), 'utf-8');
    const light = extractBlock(css, '.light');
    // --color-on-surface-variant aliases --cw-on-surface-variant (single alias layer in :root)
    const onSurfaceVariant = getVar(light, '--cw-on-surface-variant');
    const surface = getVar(light, '--cw-surface');
    expect(contrastRatio(onSurfaceVariant, surface)).toBeGreaterThanOrEqual(4.5);
  });
});
