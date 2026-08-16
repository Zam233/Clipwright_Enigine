import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

// Deterministic drag offset — assets panel starts at 280 (DEFAULT_PANEL_WIDTHS
// in workspaceStore) and setPanelWidth clamps to [200, 500], so 280 + 60 = 340
// is safe and exact.
const DRAG_DX = 60;

/**
 * Read the assets panel width from its inline style. The panel is the div
 * immediately preceding the first `.panel-divider` in DOM order (see
 * EditorLayout). Width is rendered as `style="width: Npx"`.
 */
async function assetsPanelWidth(page: Page): Promise<number> {
  const panel = page
    .locator('.panel-divider')
    .first()
    .locator('xpath=preceding-sibling::div[1]');
  const style = (await panel.getAttribute('style')) ?? '';
  const m = style.match(/width:\s*([\d.]+)px/);
  if (!m) throw new Error(`assets panel has no explicit width in style: "${style}"`);
  return Number(m[1]);
}

test.describe('面板分割条拖拽 (panel divider drag)', () => {
  test.beforeEach(async ({ page }) => {
    // Hermetic: intercept every backend request BEFORE navigation.
    await mockBackendApi(page);
    await page.setViewportSize({ width: 1600, height: 900 });
    // Deterministic layout — a persisted localStorage layout must not shrink
    // the assets panel or hide it.
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
  });

  test('Pointer 事件拖拽分割条可调整面板宽度', async ({ page }) => {
    // Defect F4: devices/browsers that fire Pointer Events (touch/pen) did not
    // trigger the mousemove/mouseup-only drag, so resizing silently failed.
    // This test dispatches ONLY Pointer events to prove the pointer path works.
    const errors = collectPageErrors(page);
    const divider = page.locator('.panel-divider').first();
    await expect(divider).toBeVisible();

    const before = await assetsPanelWidth(page);
    const startX = 600;
    const startY = 200;

    await divider.dispatchEvent('pointerdown', {
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      buttons: 1,
      clientX: startX,
      clientY: startY,
    });
    await divider.dispatchEvent('pointermove', {
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      buttons: 1,
      clientX: startX + DRAG_DX,
      clientY: startY,
    });
    await divider.dispatchEvent('pointerup', {
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      buttons: 0,
      clientX: startX + DRAG_DX,
      clientY: startY,
    });

    await expect.poll(() => assetsPanelWidth(page)).toBe(before + DRAG_DX);
    expect(errors).toEqual([]);
  });

  test('Mouse 事件拖拽分割条回归（原有行为不变）', async ({ page }) => {
    // Regression: the existing mousedown/mousemove/mouseup path must keep
    // working after the pointer-events fallback is added.
    const errors = collectPageErrors(page);
    const divider = page.locator('.panel-divider').first();
    await expect(divider).toBeVisible();

    const before = await assetsPanelWidth(page);
    const startX = 600;
    const startY = 200;

    await divider.dispatchEvent('mousedown', {
      button: 0,
      buttons: 1,
      clientX: startX,
      clientY: startY,
    });
    await divider.dispatchEvent('mousemove', {
      button: 0,
      buttons: 1,
      clientX: startX + DRAG_DX,
      clientY: startY,
    });
    await divider.dispatchEvent('mouseup', {
      button: 0,
      buttons: 0,
      clientX: startX + DRAG_DX,
      clientY: startY,
    });

    await expect.poll(() => assetsPanelWidth(page)).toBe(before + DRAG_DX);
    expect(errors).toEqual([]);
  });

  test('B18: Agent 面板分割条可拖拽调整宽度', async ({ page }) => {
    // Agent panel is the rightmost panel; its divider is the LAST .panel-divider
    // in DOM order, and the agent panel div is its following sibling.
    const errors = collectPageErrors(page);
    const dividers = page.locator('.panel-divider');
    const divider = dividers.last();
    await expect(divider).toBeVisible();

    const panel = divider.locator('xpath=following-sibling::div[1]');
    const readAgentWidth = async (): Promise<number> => {
      const style = (await panel.getAttribute('style')) ?? '';
      const m = style.match(/width:\s*([\d.]+)px/);
      if (!m) throw new Error(`agent panel has no explicit width in style: "${style}"`);
      return Number(m[1]);
    };

    const before = await readAgentWidth();
    const startX = 1400;
    const startY = 200;

    await divider.dispatchEvent('pointerdown', {
      pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 1,
      clientX: startX, clientY: startY,
    });
    await divider.dispatchEvent('pointermove', {
      pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 1,
      clientX: startX + DRAG_DX, clientY: startY,
    });
    await divider.dispatchEvent('pointerup', {
      pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 0,
      clientX: startX + DRAG_DX, clientY: startY,
    });

    await expect.poll(() => readAgentWidth()).toBe(before + DRAG_DX);
    expect(errors).toEqual([]);
  });
});
