import { test, expect, type Page } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

// Timeline engine geometry — mirrors src/features/timeline/engine/types.ts.
const HEADER_W = 152;
const RULER_H = 30;
const DEFAULT_ZOOM = 60;

// 1×1 pure-red PNG (R=255,G=0,B=0)
const RED_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADklEQVR4nGP4z8DwnwEABv8B/wcrjvYAAAAASUVORK5CYII=';

const SCREENSHOT_DIR = 'test-results/preview-fixes';

/** Hermetic editor setup: mock backend, wide viewport, deterministic layout. */
async function setupEditor(page: Page, projectId: string): Promise<string[]> {
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
  await page.goto(`/editor/${projectId}`);
  await page.waitForSelector('canvas', { timeout: 15_000 });
  return errors;
}

/** PreviewPanel root: header's parent. The preview canvas is its first <canvas>. */
function previewRoot(page: Page) {
  return page
    .getByText('节目监视器', { exact: true })
    .locator('xpath=..')
    .locator('xpath=..');
}

/** Seek the playhead by clicking the timeline ruler at time t (seconds). */
async function seekViaRuler(page: Page, t: number): Promise<void> {
  const timelineCanvas = page
    .locator('div', { has: page.getByText('添加轨道:') })
    .locator('canvas')
    .last();
  await expect(timelineCanvas).toBeVisible();
  const box = await timelineCanvas.boundingBox();
  if (!box) throw new Error('timeline canvas has no bounding box');
  await page.mouse.click(box.x + HEADER_W + t * DEFAULT_ZOOM, box.y + RULER_H / 2);
}

/** Count near-white pixels (R,G,B all > 180) in the BOTTOM 50% band of the preview canvas. */
function countWhiteBottomHalf(page: Page): Promise<number> {
  return page.evaluate(() => {
    const canvas = document.querySelectorAll('canvas')[0] as HTMLCanvasElement | undefined;
    if (!canvas) return -1;
    const ctx = canvas.getContext('2d');
    if (!ctx) return -1;
    const y0 = Math.floor(canvas.height / 2);
    const data = ctx.getImageData(0, y0, canvas.width, canvas.height - y0).data;
    let n = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] > 180 && data[i + 1] > 180 && data[i + 2] > 180) n++;
    }
    return n;
  });
}

test.describe('preview-fixes 修复回归', () => {
  test('D1: 字幕在视频帧之上可见（轨道堆叠顺序）', async ({ page }) => {
    const errors = await setupEditor(page, 'proj_e2e_demo');

    const previewCanvas = previewRoot(page).locator('canvas').first();
    await expect(previewCanvas).toBeVisible();

    // t=0.7s: caption clip-c1 (0–2s, '第一句字幕', white) is active.
    await seekViaRuler(page, 0.7);
    await page.waitForTimeout(600); // let the RAF draw settle
    const count0_7 = await countWhiteBottomHalf(page);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/d1-caption-visible.png` });

    // t=2.5s: between captions (c1 ended at 2s, c2 starts at 3s) — video placeholder only.
    await seekViaRuler(page, 2.5);
    await page.waitForTimeout(600);
    const count2_5 = await countWhiteBottomHalf(page);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/d1-caption-gap.png` });

    expect(count0_7).toBeGreaterThanOrEqual(0);
    expect(count2_5).toBeGreaterThanOrEqual(0);
    expect(count0_7 - count2_5).toBeGreaterThan(100);
    expect(errors).toEqual([]);
  });

  test('D2: 图片素材加载后无需移动播放头即显示', async ({ page }) => {
    // The app sets img.crossOrigin='anonymous' — ACAO header is required or the canvas taints.
    await page.route('**/red.png', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'image/png',
        headers: { 'Access-Control-Allow-Origin': '*' },
        body: Buffer.from(RED_PNG_B64, 'base64'),
      }),
    );
    const errors = await setupEditor(page, 'proj_img_demo');

    // Do NOT move the playhead (stays at 0). The 1×1 red image is scaled to
    // fill the frame, so once it loads the center pixel must turn red without
    // any playhead movement.
    await page.waitForFunction(
      () => {
        const canvas = document.querySelectorAll('canvas')[0] as HTMLCanvasElement | undefined;
        if (!canvas || canvas.width === 0) return false;
        try {
          const ctx = canvas.getContext('2d');
          if (!ctx) return false;
          const data = ctx.getImageData(
            Math.floor(canvas.width / 2),
            Math.floor(canvas.height / 2),
            1,
            1,
          ).data;
          return data[0] > 200 && data[1] < 80 && data[2] < 80;
        } catch {
          return false; // canvas tainted or not ready yet
        }
      },
      undefined,
      { timeout: 5000, polling: 200 },
    );
    await page.screenshot({ path: `${SCREENSHOT_DIR}/d2-image-no-seek.png` });
    expect(errors).toEqual([]);
  });

  test('UX1: 预览头部音量滑块可聚焦并响应键盘', async ({ page }) => {
    const errors = await setupEditor(page, 'proj_e2e_demo');

    // The preview header contains the 节目监视器 label and the volume slider.
    const header = page.getByText('节目监视器', { exact: true }).locator('xpath=..');
    const slider = header.locator('input[type=range]');
    await expect(slider).toBeVisible();

    const initial = Number(await slider.inputValue());
    await slider.focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowLeft');
    const after = Number(await slider.inputValue());
    expect(after).toBeLessThan(initial);

    await page.screenshot({ path: `${SCREENSHOT_DIR}/ux1-volume-slider.png` });
    expect(errors).toEqual([]);
  });

  test('UX2: 半透明播放按钮悬停时完全可见', async ({ page }) => {
    const errors = await setupEditor(page, 'proj_e2e_demo');

    // Paused initially → overlay shows the play affordance at 35% opacity.
    const button = page.locator('button[aria-label="播放"]');
    await expect(button).toBeVisible();
    expect(await button.evaluate((el) => getComputedStyle(el).opacity)).toBe('0.35');

    // Hover the preview panel (mouse over the canvas center) → fully opaque.
    const previewCanvas = previewRoot(page).locator('canvas').first();
    const box = await previewCanvas.boundingBox();
    if (!box) throw new Error('preview canvas has no bounding box');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await expect
      .poll(async () => button.evaluate((el) => getComputedStyle(el).opacity))
      .toBe('1');

    await page.screenshot({ path: `${SCREENSHOT_DIR}/ux2-overlay-hover.png` });
    expect(errors).toEqual([]);
  });

  test('UX3: 拖拽刷洗移动播放头且不触发播放；单击切换播放/暂停', async ({ page }) => {
    const errors = await setupEditor(page, 'proj_e2e_demo');

    const previewCanvas = previewRoot(page).locator('canvas').first();
    await expect(previewCanvas).toBeVisible();
    const box = await previewCanvas.boundingBox();
    if (!box) throw new Error('preview canvas has no bounding box');
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // Info-bar timecode (scoped to the preview panel; the only HH:MM:SS:FF span there).
    const timecode = previewRoot(page)
      .locator('span')
      .filter({ hasText: /^\d{2}:\d{2}:\d{2}:\d{2}$/ });
    await expect(timecode).toHaveText('00:00:00:00');

    // Drag: press left of center, sweep right in 3 steps, release.
    await page.mouse.move(cx - 100, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 40, cy, { steps: 3 });
    await page.waitForTimeout(60);
    await page.mouse.move(cx + 20, cy, { steps: 3 });
    await page.waitForTimeout(60);
    await page.mouse.move(cx + 80, cy, { steps: 3 });
    await page.mouse.up();

    // Scrub moved the playhead…
    await expect(timecode).not.toHaveText('00:00:00:00');
    // …but did NOT start playback (still paused → play affordance shown).
    await expect(page.locator('button[aria-label="播放"]')).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOT_DIR}/ux3-drag-scrub.png` });

    // Pure click while paused → plays.
    await page.mouse.click(cx, cy);
    await expect(page.locator('button[aria-label="暂停"]')).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOT_DIR}/ux3-click-play.png` });

    // Click again → pauses.
    await page.mouse.click(cx, cy);
    await expect(page.locator('button[aria-label="播放"]')).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOT_DIR}/ux3-click-pause.png` });

    expect(errors).toEqual([]);
  });
});
