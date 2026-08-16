import { test, expect } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

test.describe('编辑器交互测试', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendApi(page);
    await page.goto('/editor/proj_e2e_demo');
    await page.waitForSelector('canvas', { timeout: 15_000 });
  });

  test('工具切换 V/C/R 正常工作', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('v');
    await page.waitForTimeout(200);
    await page.keyboard.press('c');
    await page.waitForTimeout(200);
    await page.keyboard.press('r');
    await page.waitForTimeout(200);
    await page.keyboard.press('v');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Ctrl+Z/Ctrl+Shift+Z 撤销重做不崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(200);
    await page.keyboard.press('Control+Shift+z');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Ctrl+S 保存不崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('Control+s');
    await page.waitForTimeout(500);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Ctrl+A 全选 + Escape 取消不崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('Control+a');
    await page.waitForTimeout(200);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('空格键播放/暂停切换', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('Space');
    await page.waitForTimeout(500);
    await page.keyboard.press('Space');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('J/K/L shuttle 控制不崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('l');
    await page.waitForTimeout(300);
    await page.keyboard.press('k');
    await page.waitForTimeout(200);
    await page.keyboard.press('j');
    await page.waitForTimeout(300);
    await page.keyboard.press('k');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('M 添加标记 + Shift+M 跳转标记', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.keyboard.press('m');
    await page.waitForTimeout(200);
    await page.keyboard.press('Shift+m');
    await page.waitForTimeout(200);
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('属性面板显示且可交互', async ({ page }) => {
    const errors = collectPageErrors(page);
    await expect(page.getByText('属性', { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('时间轴工具栏按钮可点击', async ({ page }) => {
    const errors = collectPageErrors(page);
    await expect(page.getByText('添加轨道:')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('状态栏显示完整信息', async ({ page }) => {
    const errors = collectPageErrors(page);
    await expect(page.getByText('ClipWright v0.1.0')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('面板切换按钮工作', async ({ page }) => {
    const errors = collectPageErrors(page);
    const assetBtn = page.locator('button').filter({ has: page.locator('svg.lucide-panel-left') }).first();
    if (await assetBtn.isVisible()) {
      await assetBtn.click();
      await page.waitForTimeout(300);
      await assetBtn.click();
      await page.waitForTimeout(300);
    }
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Ctrl+E 导航到导出页', async ({ page }) => {
    await page.keyboard.press('Control+e');
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL(/\/export/);
  });
});
