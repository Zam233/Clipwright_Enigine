import { test, expect } from '@playwright/test';
import { collectPageErrors } from './helpers';

async function mockSettingsApis(page: import('@playwright/test').Page) {
  await page.route(/https?:\/\/[^/]+\/health(\?|$)/, (route) =>
    route.fulfill({ json: { status: 'ok', service: 'clipwright-engine' } }),
  );
  await page.route(/https?:\/\/[^/]+\/api\//, (route) => {
    const url = route.request().url();
    if (url.includes('/api/fonts/list')) return route.fulfill({ json: { fonts: [{ family: 'Inter', style: 'Regular' }], count: 1 } });
    if (url.includes('/api/webhook/events')) return route.fulfill({ json: { events: ['pipeline.completed', 'render.completed'] } });
    if (url.includes('/api/webhook/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/type-maker/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/template/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/plugin/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/plugin/discover')) return route.fulfill({ json: [] });
    if (url.includes('/api/test/config')) return route.fulfill({ json: { llm: 'test', embed: 'test' } });
    if (url.includes('/api/render/presets')) return route.fulfill({ json: [] });
    if (url.includes('/api/render/queue')) return route.fulfill({ json: { tasks: [] } });
    if (url.includes('/api/tool/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/skill/list')) return route.fulfill({ json: [] });
    if (url.includes('/api/pipeline/runs')) return route.fulfill({ json: [] });
    if (url.includes('/api/persona')) return route.fulfill({ json: [] });
    if (url.includes('/api/asset')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
}

test.describe('Settings 页面冒烟测试', () => {
  test('SettingsPage 加载无崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('ExportPage 加载且预设可选', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/export/proj_test');
    await page.waitForTimeout(1000);
    await expect(page.getByText('导出').first()).toBeVisible({ timeout: 10_000 });
    expect(errors).toEqual([]);
  });

  test('FontsPage 加载字体列表', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/fonts');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('WebhooksPage 加载且表单可见', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/webhooks');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('TypeMakerPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/type-maker');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('TemplatesPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/templates');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('ModelsPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/models');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('PluginsPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/settings/plugins');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('PersonaPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/persona');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('PipelineAdminPage 加载', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockSettingsApis(page);
    await page.goto('/pipeline-admin');
    await page.waitForTimeout(1000);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });
});
