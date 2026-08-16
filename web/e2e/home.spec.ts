import { test, expect } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

test.describe('首页冒烟测试', () => {
  test('首页加载并显示主标题', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBackendApi(page);
    await page.goto('/');

    await expect(page.locator('h1')).toContainText('把你的选题');
    expect(errors).toEqual([]);
  });

  test('后端健康检查显示已连接', async ({ page }) => {
    await mockBackendApi(page);
    await page.goto('/');

    await expect(page.getByText(/已连接/)).toBeVisible({ timeout: 10_000 });
  });

  test('项目列表页渲染演示项目卡片', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBackendApi(page);
    await page.goto('/projects');

    await expect(page.getByText('E2E 演示项目').first()).toBeVisible({ timeout: 10_000 });
    expect(errors).toEqual([]);
  });
});
