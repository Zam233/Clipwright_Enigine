import { test, expect, type Page, type Locator } from '@playwright/test';
import { mockBackendApi, collectPageErrors } from './helpers';

// Timeline engine geometry — mirrors src/features/timeline/engine/types.ts.
const HEADER_W = 152;
const RULER_H = 30;
const TRACK_H = 48;
const DEFAULT_ZOOM = 60;

// demoTimeline (e2e/helpers.ts): video track is the 1st track (index 0);
// clip-v1 spans 0–5s. The mocked requirements/edit response proposes duration 6s.
const VIDEO_TRACK_INDEX = 0;
const CLIP_V1 = { start: 0, duration: 5 };

const EDIT_INSTRUCTION = '把这段视频延长到 6 秒';

function clipCenterX(startSec: number, durationSec: number): number {
  return HEADER_W + (startSec + durationSec / 2) * DEFAULT_ZOOM;
}

function videoTrackCenterY(): number {
  return RULER_H + VIDEO_TRACK_INDEX * TRACK_H + TRACK_H / 2;
}

/** 属性面板「时长 (s)」数值输入框（Row 标签 span → 父 Row → input）。 */
function durationInput(page: Page): Locator {
  return page
    .getByText('时长 (s)', { exact: true })
    .locator('xpath=..')
    .locator('input[type="number"]');
}

/** TimelineDiffView 全屏覆盖层（AgentPanel 中 fixed inset-0 z-[60]）。 */
function diffOverlay(page: Page): Locator {
  return page.locator('div.fixed.inset-0', { hasText: 'Agent 建议的修改' });
}

/** 时间轴画布（DOM 中最后一个 canvas，经由「添加轨道:」面板作用域限定）。 */
function timelineCanvas(page: Page): Locator {
  return page
    .locator('div', { has: page.getByText('添加轨道:') })
    .locator('canvas')
    .last();
}

/**
 * 驱动到 diff 覆盖层出现为止：
 * 开启需求会话 → 画布上点击选中 clip-v1 → 输入编辑指令并回车。
 */
async function driveEditToOverlay(page: Page): Promise<void> {
  // 1) 开启需求会话（init/chat 由 helpers.ts mock）
  await page.getByPlaceholder(/输入视频选题/).fill('E2E 编辑测试选题');
  await page.getByRole('button', { name: /开始需求分析/ }).click();
  // assistant 回复出现 ⇒ session_id 已写入 store
  await expect(page.getByText('（E2E mock）已收到你的需求。')).toBeVisible();

  // 2) 在时间轴画布上点击 clip-v1 中心（track 0, 0–5s）
  const canvas = timelineCanvas(page);
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  if (!box) throw new Error('timeline canvas has no bounding box');
  await page.mouse.click(
    box.x + clipCenterX(CLIP_V1.start, CLIP_V1.duration),
    box.y + videoTrackCenterY(),
  );

  // 选中成功：属性面板显示 clip-v1 时长 5s；Agent 面板出现「选中素材」标签，
  // 输入框切换为编辑模式（Enter 将走 sendEdit → /api/requirements/edit）。
  await expect(durationInput(page)).toHaveValue('5');
  await expect(page.getByText('选中素材', { exact: true })).toBeVisible();
  const editInput = page.getByPlaceholder(/描述对选中素材的修改/);
  await expect(editInput).toBeVisible();

  // 3) 发送自然语言编辑指令
  await editInput.fill(EDIT_INSTRUCTION);
  await editInput.press('Enter');

  // 4) mocked proposed_timeline 触发 TimelineDiffView 全屏覆盖层
  await expect(diffOverlay(page)).toBeVisible();
}

test.describe('C6 自然语言时间线编辑 (agent edit)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendApi(page);
    // 兜底：接受后 registerTimeline 会用 proposed clip 的 metadata.url 建 <video>，
    // 拦截 mock 媒体地址，保持完全 hermetic。
    await page.route(/https?:\/\/mock\//, (route) => route.fulfill({ status: 204 }));

    // 宽视口 + 确定性布局（与 caption-style.spec.ts 一致），保证轨道与面板可见。
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
  });

  test('选中片段 → 发送编辑指令 → diff 覆盖层出现（请求体形状断言）→ 全部接受应用时间线', async ({ page }) => {
    const errors = collectPageErrors(page);

    // 捕获 POST /api/requirements/edit 请求体（helpers mock 仍负责 fulfill）
    let editBody: Record<string, unknown> | null = null;
    page.on('request', (req) => {
      if (req.method() === 'POST' && /\/api\/requirements\/edit(\?|$)/.test(req.url())) {
        try {
          editBody = req.postDataJSON() as Record<string, unknown>;
        } catch {
          editBody = null;
        }
      }
    });

    await driveEditToOverlay(page);
    const overlay = diffOverlay(page);

    // (b) 覆盖层可见：标题 + 修改徽标(~1) + 全部接受按钮
    await expect(overlay.getByText('Agent 建议的修改')).toBeVisible();
    await expect(overlay.getByRole('button', { name: /全部接受/ })).toBeVisible();
    await page.screenshot({ path: 'e2e/artifacts/agent-edit-01-diff-overlay.png' });

    // (a) 请求体形状：message + 非空 selected_clip_ids + 当前时间线
    expect(editBody).toBeTruthy();
    expect(editBody!.session_id).toBe('sess-e2e');
    expect(editBody!.message).toBe(EDIT_INSTRUCTION);
    expect(editBody!.selected_clip_ids).toEqual(['clip-v1']);
    expect((editBody!.timeline as { tracks?: unknown[] })?.tracks?.length).toBe(3);

    // (c) 点击「全部接受」→ 覆盖层关闭，时间线被应用（clip-v1 时长 5 → 6）
    await overlay.getByRole('button', { name: /全部接受/ }).click();
    await expect(overlay).not.toBeVisible();
    // 接受会清空选中；状态栏时长 8.0s → 6.0s（按片段末尾重算）
    await expect(page.getByText('3 轨 · 6.0s · 30fps', { exact: true })).toBeVisible();
    // 重新点击 clip-v1（现跨度 0–6s，原中心点仍在片段内），属性面板确认时长已应用
    const box2 = await timelineCanvas(page).boundingBox();
    if (!box2) throw new Error('timeline canvas has no bounding box');
    await page.mouse.click(
      box2.x + clipCenterX(CLIP_V1.start, CLIP_V1.duration),
      box2.y + videoTrackCenterY(),
    );
    await expect(durationInput(page)).toHaveValue('6');
    await page.screenshot({ path: 'e2e/artifacts/agent-edit-02-accepted.png' });

    expect(errors).toEqual([]);
  });

  test('diff 覆盖层可通过关闭按钮忽略，时间线保持不变', async ({ page }) => {
    const errors = collectPageErrors(page);

    await driveEditToOverlay(page);
    const overlay = diffOverlay(page);
    await page.screenshot({ path: 'e2e/artifacts/agent-edit-03-overlay-visible.png' });

    // 关闭按钮仅含 X 图标（无文本），覆盖层内唯一带 lucide-x 的按钮
    await overlay.locator('button:has(svg.lucide-x)').click();
    await expect(overlay).not.toBeVisible();

    // 忽略后时间线未被修改：clip-v1 时长仍为 5s
    await expect(durationInput(page)).toHaveValue('5');

    expect(errors).toEqual([]);
  });
});
