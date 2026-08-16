/**
 * Headless 全流程测试：首页 → 需求Agent(简报/规划书) → 管线 → 审阅 → 渲染
 * 实时输出 [进度] 标记供汇报
 */

import { chromium } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = 'http://localhost:5173';
const BACKEND = 'http://localhost:8000';
const CONTENT_PATH = path.resolve('文理', 'content.md');
const VOICE_PATH = path.resolve('文理', 'voice.MP3');
const STAGE_TIMEOUT = 900_000; // 单阶段 15 分钟
const PIPELINE_TIMEOUT = 3_600_000; // 管线 60 分钟（animation 阶段 mg_dynamic 每个耗时 1-4 分钟）
const RENDER_TIMEOUT = 1_800_000; // 渲染 30 分钟

function log(msg: string) {
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[${t}] [进度] ${msg}`);
}

async function readContent(): Promise<{ topic: string; script: string }> {
  const text = fs.readFileSync(CONTENT_PATH, 'utf-8');
  const topicMatch = text.match(/\*\*标题[：:]\*\*\s*(.+)/);
  const topic = topicMatch ? topicMatch[1].trim() : '文理之争';
  const scriptMatch = text.match(/\*\*文案[：:]\*\*\s*\n([\s\S]+)/);
  let script = scriptMatch ? scriptMatch[1].trim() : text;
  if (script.length > 10000) script = script.slice(0, 10000);
  return { topic, script };
}

async function waitFor(fn: () => Promise<boolean>, timeout: number, interval = 5000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try { if (await fn()) return true; } catch {}
    await new Promise((r) => setTimeout(r, interval));
  }
  return false;
}

async function fetchJson(page: any, url: string): Promise<any> {
  return page.evaluate(async (u) => {
    const res = await fetch(u);
    return res.ok ? res.json() : null;
  }, url);
}

async function main() {
  const { topic, script } = await readContent();
  log(`测试数据: topic=${topic}, 文案=${script.length}字, 配音=${VOICE_PATH}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, locale: 'zh-CN' });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  let pipelineId = '';
  let renderTaskId = '';
  page.on('response', async (res) => {
    if (res.url().includes('/api/requirements/proceed')) {
      try {
        const body = await res.json();
        if (body.pipeline_id) { pipelineId = body.pipeline_id; log(`proceed 返回 pipeline_id=${body.pipeline_id}`); }
      } catch {}
    }
    if (res.url().includes('/api/render/queue') && res.request().method() === 'POST') {
      try {
        const body = await res.json();
        if (body.task_id) { renderTaskId = body.task_id; log(`渲染任务已提交 task_id=${body.task_id}`); }
      } catch {}
    }
  });

  try {
    // ═══ 1. 首页 ═══
    log('STEP 1/6 首页：加载...');
    await page.goto(FRONTEND, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(2000);
    await page.locator('input[placeholder*="选题"]').fill(topic);
    await page.locator('textarea[placeholder*="口播"]').fill(script);
    try {
      await page.locator('button', { hasText: 'Zam' }).first().click({ timeout: 5000 });
      log('已选择 Persona: Zam');
    } catch { log('WARN Zam 按钮未找到'); }
    await page.locator('input[type="file"]').first().setInputFiles(VOICE_PATH);
    await page.waitForTimeout(6000);
    log('已上传 voice.MP3');
    await page.locator('button', { hasText: '开始创作' }).first().click();
    await page.waitForURL(/\/editor\//, { timeout: 60000 });
    await page.waitForTimeout(3000);
    log('已进入编辑器');

    // ═══ 2. 简报 ═══
    log('STEP 2/6 需求 Agent：等待创意简报...');
    const briefOk = await waitFor(async () => {
      const t = await page.textContent('body').catch(() => '');
      return t.includes('确认简报') || t.includes('创作方案已完成') || t.includes('已为你生成创意简报');
    }, STAGE_TIMEOUT, 3000);
    if (!briefOk) throw new Error('简报生成超时');
    log('简报已生成');

    // 确认简报（按钮优先，回退对话输入框）
    const clicked = await (async () => {
      try {
        await page.waitForSelector('button:has-text("确认简报")', { timeout: 15000 });
        await page.locator('button', { hasText: '确认简报' }).first().click({ timeout: 8000 });
        return true;
      } catch {
        try {
          const ci = page.locator('input[placeholder*="继续与需求"]').first();
          await ci.fill('确认，请生成完整的制作规划书。', { timeout: 5000 });
          await ci.press('Enter');
          return true;
        } catch { return false; }
      }
    })();
    log(clicked ? '已确认简报，生成规划书中...' : 'WARN 确认简报失败');
    if (!clicked) throw new Error('无法确认简报');

    // ═══ 3. 规划书 ═══
    const planOk = await waitFor(async () => {
      const t = await page.textContent('body').catch(() => '');
      return t.includes('确认并启动管线');
    }, STAGE_TIMEOUT, 5000);
    if (!planOk) throw new Error('规划书生成超时');
    log('规划书已生成');

    // ═══ 4. 确认规划书 → 管线 ═══
    log('STEP 4/6 确认规划书，启动管线...');
    await page.locator('button', { hasText: '确认并启动管线' }).first().click({ timeout: 10000 });
    await waitFor(async () => pipelineId !== '', 60000, 2000);
    if (!pipelineId) throw new Error('未获取 pipeline_id');
    log(`管线已启动: ${pipelineId}，等待完成（最长 30 分钟）...`);

    // 轮询管线状态（用 status 接口快速返回，不用 result 的阻塞轮询）
    let lastStatus = '';
    const pipelineDone = await waitFor(async () => {
      const r = await fetchJson(page, `${BACKEND}/api/pipeline/status/${pipelineId}`);
      if (!r) return false;
      const st = r.status ?? '';
      if (st !== lastStatus) { lastStatus = st; log(`  管线状态: ${st}`); }
      return st === 'completed' || st === 'finished' || st === 'failed';
    }, PIPELINE_TIMEOUT, 15000);
    if (!pipelineDone) throw new Error('管线超时');
    if (lastStatus !== 'completed' && lastStatus !== 'finished') throw new Error(`管线失败: ${lastStatus}`);
    log('管线完成 COMPLETED！');

    // 拉取最终结果（此时 _pipeline_results 已有值，快速返回）
    const finalResult = await fetchJson(page, `${BACKEND}/api/pipeline/result/${pipelineId}`);
    if (finalResult) {
      log(`最终结果: status=${finalResult.status}, 场景=${finalResult.scenes?.length ?? 'n/a'}`);
    }

    // ═══ 5. 审阅 ═══
    log('STEP 5/6 审阅时间线：等待 TimelineDiffView...');
    // 记录当前项目 id（编辑器 URL 中的 proj_xxx），供导出页使用
    const editorUrl = page.url();
    const projMatch = editorUrl.match(/\/editor\/([A-Za-z0-9_-]+)/);
    const projectId = projMatch ? projMatch[1] : '';
    log(`项目 id: ${projectId}`);
    // 两种成功路径：出现「全部接受」（有差异）或「无差异」提示（Agent 时间线与当前一致）
    let accepted = false;
    const reviewOk = await waitFor(async () => {
      const t = await page.textContent('body').catch(() => '');
      if (t.includes('全部接受')) return true;
      if (t.includes('无差异')) return true;
      return false;
    }, 180000, 3000);
    if (reviewOk) {
      const t = await page.textContent('body').catch(() => '');
      if (t.includes('全部接受')) {
        await page.locator('button', { hasText: '全部接受' }).first().click({ timeout: 8000 });
        accepted = true;
        log('已接受 Agent 时间线');
      } else {
        await page.locator('button', { hasText: '关闭' }).first().click({ timeout: 8000 }).catch(() => {});
        log('Agent 时间线与当前一致，无需合并');
      }
      // 等待编辑器 5s 防抖自动保存把接受后的时间线写回后端，导出页才能取到
      await page.waitForTimeout(8000);
    } else {
      log('WARN 未出现审阅视图，继续（将直接以当前时间线渲染）');
    }

    // ═══ 6. 渲染 ═══
    log('STEP 6/6 导出渲染：导航到导出页...');
    if (!projectId) throw new Error('未获取 projectId，无法进入导出页');
    await page.goto(`${FRONTEND}/export/${projectId}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
    const renderBtn = page.locator('button', { hasText: '加入渲染队列' });
    const btnVisible = await renderBtn.isVisible({ timeout: 15000 }).catch(() => false);
    if (!btnVisible) throw new Error('渲染按钮不可见（时间线可能为空）');
    await renderBtn.click({ timeout: 10000 });
    log('已提交渲染队列，等待完成（最长 30 分钟）...');

    let lastProgress = '';
    const renderDone = await waitFor(async () => {
      const q = await fetchJson(page, `${BACKEND}/api/render/queue`);
      if (!q || !Array.isArray(q.tasks)) return false;
      const t = renderTaskId ? q.tasks.find((x: any) => x.task_id === renderTaskId) : q.tasks[0];
      if (!t) return false;
      const st = `${t.status} ${t.progress ?? 0}%`;
      if (st !== lastProgress) { lastProgress = st; log(`  渲染进度: ${st}`); }
      return t.status === 'completed' || t.status === 'failed' || t.status === 'error';
    }, RENDER_TIMEOUT, 8000);
    if (!renderDone) throw new Error('渲染超时');
    if (lastProgress.includes('failed') || lastProgress.includes('error')) throw new Error('渲染失败');
    log('渲染完成 COMPLETED！');

    // ═══ 汇总 ═══
    log('══════ 全流程完成 ══════');
    log(`pipeline_id: ${pipelineId}`);
    log(`render task: ${renderTaskId}`);
    log(`页面错误: ${errors.length}`);
    if (errors.length) log(`错误列表: ${JSON.stringify(errors.slice(0, 5))}`);
    await page.screenshot({ path: 'e2e-headless-final.png', fullPage: true });
    log('截图: e2e-headless-final.png');
    process.exit(0);
  } catch (e) {
    log(`FAIL: ${(e as Error).message}`);
    try { await page.screenshot({ path: 'e2e-headless-error.png', fullPage: true }); } catch {}
    log('截图: e2e-headless-error.png');
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
