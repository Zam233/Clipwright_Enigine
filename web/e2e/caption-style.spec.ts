import { test, expect } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

// Timeline engine geometry — mirrors src/features/timeline/engine/types.ts.
const HEADER_W = 152;
const RULER_H = 30;
const TRACK_H = 48;
const DEFAULT_ZOOM = 60;

// demoTimeline (e2e/helpers.ts): caption track is the 3rd track (index 2);
// clip-c1 spans 0–2s, clip-c2 spans 3–5s.
const CAPTION_TRACK_INDEX = 2;
const CLIP_C1 = { start: 0, duration: 2 };
const CLIP_C2 = { start: 3, duration: 2 };

function clipCenterX(startSec: number, durationSec: number): number {
  return HEADER_W + (startSec + durationSec / 2) * DEFAULT_ZOOM;
}

function captionTrackCenterY(): number {
  return RULER_H + CAPTION_TRACK_INDEX * TRACK_H + TRACK_H / 2;
}

test.describe('字幕样式整层级联 (caption style cascade)', () => {
  test('修改一个字幕片段的样式后，同轨另一个字幕片段的样式输入同步（读 UI）', async ({ page }) => {
    // Hermetic: intercept every backend request BEFORE navigation.
    const errors = collectPageErrors(page);
    await mockBackendApi(page);

    // Wide viewport + deterministic editor layout so all 3 tracks and both
    // caption clips are visible and stale localStorage layouts cannot shrink
    // the timeline below the caption track.
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.addInitScript(() => {
      localStorage.setItem(
        'clipwright.layout',
        JSON.stringify({
          panels: { assets: true, properties: true, agent: true },
          panelWidths: { assets: 280, properties: 320, agent: 320 },
          timelineHeight: 300,
        }),
      );
    });

    await page.goto('/editor/proj_e2e_demo');
    await page.waitForSelector('canvas', { timeout: 15_000 });

    // The timeline canvas is the LAST <canvas> in the editor (preview and
    // audio-meter canvases render above it in DOM order). Scope through the
    // panel that hosts the "添加轨道:" bar so we never mistake the preview
    // canvas for it.
    const timelineCanvas = page
      .locator('div', { has: page.getByText('添加轨道:') })
      .locator('canvas')
      .last();
    await expect(timelineCanvas).toBeVisible();

    const box = await timelineCanvas.boundingBox();
    if (!box) throw new Error('timeline canvas has no bounding box');

    // Style-control locators: the Row label span, then its parent Row, then
    // the actual <input>. Stable and scoped — no store reads.
    const styleInput = (label: string, type: 'number' | 'color') =>
      page
        .getByText(label, { exact: true })
        .locator('xpath=..')
        .locator(`input[type="${type}"]`);

    const glowWidth = styleInput('发光宽度', 'number');
    const glowColor = styleInput('发光颜色', 'color');

    // 1) Select clip-c1 by clicking its center on the caption track.
    const c1 = {
      x: box.x + clipCenterX(CLIP_C1.start, CLIP_C1.duration),
      y: box.y + captionTrackCenterY(),
    };
    await page.mouse.click(c1.x, c1.y);

    // Properties panel reflects clip-c1 (id shown in the identity row).
    await expect(page.getByText('字幕样式', { exact: true })).toBeVisible();
    await expect(page.getByText('clip-c1', { exact: true })).toBeVisible();
    await expect(glowWidth).toBeVisible();
    // clip-c1 starts with glow_width=4 / glow_color=#FFFFFF.
    await expect(glowWidth).toHaveValue('4');
    await expect(glowColor).toHaveValue('#ffffff');

    // 2) Edit style controls on clip-c1 — cascades to every clip on the track.
    await glowWidth.fill('12');
    await glowWidth.press('Enter');
    await glowColor.fill('#00FF00');

    // 3) Select the OTHER caption clip (clip-c2) on the same track.
    const c2 = {
      x: box.x + clipCenterX(CLIP_C2.start, CLIP_C2.duration),
      y: box.y + captionTrackCenterY(),
    };
    await page.mouse.click(c2.x, c2.y);

    // Panel now shows clip-c2.
    await expect(page.getByText('clip-c2', { exact: true })).toBeVisible();

    // 4) Style inputs for clip-c2 reflect the values set on clip-c1 — read the
    //    UI, not the store. clip-c2 started at glow_width=0 / #000000, so this
    //    only passes if the layer-wide cascade actually synced it.
    await expect(glowWidth).toHaveValue('12');
    await expect(glowColor).toHaveValue('#00ff00');

    // 5) No uncaught page errors throughout the whole flow.
    expect(errors).toEqual([]);
  });

  test('字幕样式字段齐全，且编辑斜体/字距/阴影模糊后输入同步（读 UI）', async ({ page }) => {
    // Hermetic: intercept every backend request BEFORE navigation.
    const errors = collectPageErrors(page);
    await mockBackendApi(page);

    await page.setViewportSize({ width: 1600, height: 900 });
    await page.addInitScript(() => {
      localStorage.setItem(
        'clipwright.layout',
        JSON.stringify({
          panels: { assets: true, properties: true, agent: true },
          panelWidths: { assets: 280, properties: 320, agent: 320 },
          timelineHeight: 300,
        }),
      );
    });

    await page.goto('/editor/proj_e2e_demo');
    await page.waitForSelector('canvas', { timeout: 15_000 });

    const timelineCanvas = page
      .locator('div', { has: page.getByText('添加轨道:') })
      .locator('canvas')
      .last();
    await expect(timelineCanvas).toBeVisible();
    const box = await timelineCanvas.boundingBox();
    if (!box) throw new Error('timeline canvas has no bounding box');

    // Caption style controls: Row label span -> parent Row -> the control.
    // Scoped to span.text-label with an exact (anchored) regex so option text
    // (e.g. 斜体) and substring label collisions (e.g. 颜色 vs 描边颜色) never match.
    const styleControl = (label: string, type: 'number' | 'color' | 'select') => {
      const row = page
        .locator('span.text-label', { hasText: new RegExp(`^${label}$`) })
        .locator('xpath=..');
      return type === 'select' ? row.locator('select') : row.locator(`input[type="${type}"]`);
    };

    // 1) Select clip-c1 on the caption track.
    const c1 = {
      x: box.x + clipCenterX(CLIP_C1.start, CLIP_C1.duration),
      y: box.y + captionTrackCenterY(),
    };
    await page.mouse.click(c1.x, c1.y);
    await expect(page.getByText('clip-c1', { exact: true })).toBeVisible();
    await expect(page.getByText('字幕样式', { exact: true })).toBeVisible();

    // 2) All caption style fields are present in the panel.
    const fields: Array<{ label: string; type: 'number' | 'color' | 'select' }> = [
      { label: '字体族', type: 'select' },
      { label: '字号', type: 'number' },
      { label: '颜色', type: 'color' },
      { label: '粗细', type: 'select' },
      { label: '斜体', type: 'select' },
      { label: '对齐', type: 'select' },
      { label: '字距', type: 'number' },
      { label: '描边宽度', type: 'number' },
      { label: '描边颜色', type: 'color' },
      { label: '阴影 X', type: 'number' },
      { label: '阴影 Y', type: 'number' },
      { label: '阴影模糊', type: 'number' },
      { label: '阴影颜色', type: 'color' },
      { label: '发光宽度', type: 'number' },
      { label: '发光颜色', type: 'color' },
    ];
    for (const f of fields) {
      await expect(styleControl(f.label, f.type)).toBeVisible();
    }

    // 3) Fixture baseline: italic=normal, letter_spacing=0, shadow_blur=0.
    await expect(styleControl('斜体', 'select')).toHaveValue('normal');
    await expect(styleControl('字距', 'number')).toHaveValue('0');
    await expect(styleControl('阴影模糊', 'number')).toHaveValue('0');

    // 4) Edit italic / letter_spacing / shadow_blur — read the UI inputs after edit.
    await styleControl('斜体', 'select').selectOption('italic');
    const letterSpacing = styleControl('字距', 'number');
    await letterSpacing.fill('6');
    await letterSpacing.press('Enter');
    const shadowBlur = styleControl('阴影模糊', 'number');
    await shadowBlur.fill('8');
    await shadowBlur.press('Enter');

    await expect(styleControl('斜体', 'select')).toHaveValue('italic');
    await expect(styleControl('字距', 'number')).toHaveValue('6');
    await expect(styleControl('阴影模糊', 'number')).toHaveValue('8');

    // 5) No uncaught page errors throughout the whole flow.
    expect(errors).toEqual([]);
  });
});
