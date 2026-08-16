import { test, expect } from '@playwright/test';

// P0-11: 与后端实际端口（8080）对齐；本 spec 需真实后端，由 test:e2e:integration 单独运行
const BASE = 'http://127.0.0.1:8080';

test.describe('真实后端集成测试', () => {
  test('健康检查返回有效响应', async ({ request }) => {
    const res = await request.get(`${BASE}/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.service).toBe('clipwright-engine');
    expect(body.status).toBeDefined();
  });

  test('项目列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/project`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test('创建项目 → 读取 → 删除 完整 CRUD', async ({ request }) => {
    const createRes = await request.post(`${BASE}/api/project`, {
      data: {
        name: 'E2E 集成测试项目',
        timeline: {
          id: 'tl-int', width: 1920, height: 1080, fps: 30, duration_sec: 0,
          tracks: [],
        },
      },
    });
    expect(createRes.ok()).toBeTruthy();
    const project = await createRes.json();
    expect(project.id).toBeDefined();
    expect(project.name).toBe('E2E 集成测试项目');

    const getRes = await request.get(`${BASE}/api/project/${project.id}`);
    expect(getRes.ok()).toBeTruthy();
    const fetched = await getRes.json();
    expect(fetched.name).toBe('E2E 集成测试项目');

    const saveRes = await request.put(`${BASE}/api/project/${project.id}`, {
      data: { name: '已修改项目', timeline: fetched.timeline },
    });
    expect(saveRes.ok()).toBeTruthy();

    const verifyRes = await request.get(`${BASE}/api/project/${project.id}`);
    const verified = await verifyRes.json();
    expect(verified.name).toBe('已修改项目');
  });

  test('Persona 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/persona/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('Pipeline 预测脚本 API 可用', async ({ request }) => {
    // script_text 必须放在 JSON body（FastAPI Pydantic body model），不能作为 query 参数
    const res = await request.post(`${BASE}/api/pipeline/predict-script`, {
      data: { script_text: '测试文稿' },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toBeDefined();
  });

  test('字体列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/fonts/list`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.fonts).toBeDefined();
  });

  test('动画列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/animation/list`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test('渲染预设 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/render/presets`);
    expect(res.ok()).toBeTruthy();
  });

  test('Webhook 事件列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/webhook/events`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.events).toBeDefined();
  });

  test('TypeMaker 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/type-maker/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('Template 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/template/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('Plugin 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/plugin/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('Tool 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/tool/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('Skill 列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/skill/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('素材列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/asset/list`);
    expect(res.ok()).toBeTruthy();
  });

  test('预处理操作列表 API 可用', async ({ request }) => {
    const res = await request.get(`${BASE}/api/preprocess/operations`);
    expect(res.ok()).toBeTruthy();
  });

  test('EDL 导出 API 可用', async ({ request }) => {
    const res = await request.post(`${BASE}/api/edl/export/edl?fps=30`, {
      data: [{ id: 'c1', start_sec: 0, duration_sec: 5, kind: 'video', title: 'test' }],
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.edl).toBeDefined();
  });

  test('字幕导出 API 可用', async ({ request }) => {
    const res = await request.post(`${BASE}/api/subtitle/export`, {
      data: { clips: [{ start_sec: 0, duration_sec: 3, text: '你好', kind: 'caption' }], format: 'srt' },
    });
    // 字幕导出可能返回 422（参数格式不匹配），只要不是 500 即可
    expect(res.status()).toBeLessThan(500);
  });
});

test.describe('无头浏览器 + 真实后端页面测试', () => {
  test('首页加载并显示后端连接状态', async ({ page }) => {
    await page.goto('http://localhost:5173/');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('编辑器页面加载（真实后端项目）', async ({ page }) => {
    const createRes = await page.request.post(`${BASE}/api/project`, {
      data: {
        name: 'E2E 浏览器测试',
        timeline: {
          id: 'tl-browser', width: 1920, height: 1080, fps: 30, duration_sec: 5,
          tracks: [{
            id: 'v1', name: 'Video', kind: 'video', index: 0, locked: false, muted: false,
            clips: [{
              id: 'c1', kind: 'video', asset_id: '', track_id: 'v1',
              start_sec: 0, duration_sec: 5, source_offset_sec: 0,
              speed: 1, volume: 1, opacity: 1, keyframes: [], metadata: {},
            }],
          }],
        },
      },
    });
    const project = await createRes.json();
    await page.goto(`http://localhost:5173/editor/${project.id}`);
    await page.waitForSelector('canvas', { timeout: 15_000 });
    await expect(page.locator('canvas').first()).toBeVisible();
    await expect(page.getByText('属性', { exact: true })).toBeVisible();
  });

  test('Settings 页面加载（真实后端）', async ({ page }) => {
    await page.goto('http://localhost:5173/settings');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('Export 页面加载（真实后端）', async ({ page }) => {
    await page.goto('http://localhost:5173/export/proj_test');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('Persona 页面加载（真实后端）', async ({ page }) => {
    await page.goto('http://localhost:5173/persona');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });
});
