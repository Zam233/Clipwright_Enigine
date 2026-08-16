import { test, expect } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

test.describe('编辑器冒烟测试', () => {
  test('编辑器加载项目并渲染四面板', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBackendApi(page);
    await page.goto('/editor/proj_e2e_demo');

    // 预览面板
    await expect(page.getByText('节目监视器')).toBeVisible({ timeout: 15_000 });
    // 预览 Canvas 与时间轴 Canvas 均应存在
    await expect(page.locator('canvas').first()).toBeVisible();
    expect(await page.locator('canvas').count()).toBeGreaterThanOrEqual(2);
    expect(errors).toEqual([]);
  });

  test('项目加载失败时回退到本地空项目（演示模式）', async ({ page }) => {
    await page.route(/https?:\/\/[^/]+\/health(\?|$)/, (route) =>
      route.fulfill({ json: { status: 'ok', service: 'clipwright-engine' } }),
    );
    // Playwright 后注册的路由优先匹配：先注册通用兜底，再注册具体 404
    await page.route(/https?:\/\/[^/]+\/api\//, (route) => route.fulfill({ json: [] }));
    await page.route(/https?:\/\/[^/]+\/api\/project\/proj_bad_id/, (route) =>
      route.fulfill({ status: 404, json: { detail: 'not found' } }),
    );

    await page.goto('/editor/proj_bad_id');
    // 当前设计：加载失败不跳转首页，而是留在编辑器打开本地空项目（演示模式）
    await expect(page).toHaveURL(/\/editor\/proj_bad_id$/, { timeout: 15_000 });
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 });
    // 时间轴仍在，且未崩溃
    await expect(page.locator('canvas').count()).resolves.toBeGreaterThanOrEqual(2);
  });
});
