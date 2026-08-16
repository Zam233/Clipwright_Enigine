/**
 * 需求 Agent 验证测试（有界面浏览器）
 * 验证简报 + 规划书生成，捕获并输出真实生成内容
 */

import { chromium } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = 'http://localhost:5173';
const CONTENT_PATH = path.resolve('文理', 'content.md');

interface BriefData { [k: string]: any }
interface PlanData { [k: string]: any }

async function readContent(): Promise<{ topic: string; script: string }> {
  const text = fs.readFileSync(CONTENT_PATH, 'utf-8');
  const topicMatch = text.match(/\*\*标题[：:]\*\*\s*(.+)/);
  const topic = topicMatch ? topicMatch[1].trim() : '文理之争';
  const scriptMatch = text.match(/\*\*文案[：:]\*\*\s*\n([\s\S]+)/);
  let script = scriptMatch ? scriptMatch[1].trim() : text;
  if (script.length > 10000) script = script.slice(0, 10000);
  return { topic, script };
}

async function main() {
  const { topic, script } = await readContent();
  console.log(`[test] Topic: ${topic}, Script: ${script.length} chars`);

  const browser = await chromium.launch({ headless: false, slowMo: 60 });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, locale: 'zh-CN' });
  const page = await context.newPage();

  // ── 拦截 /api/requirements/chat 响应，捕获简报与规划书 ──
  let briefData: BriefData | null = null;
  let planData: PlanData | null = null;
  let lastReply = '';
  page.on('response', async (res) => {
    if (!res.url().includes('/api/requirements/chat')) return;
    try {
      const body = await res.json();
      if (body.creative_brief) {
        briefData = body.creative_brief;
        console.log('[test] [capture] creative_brief received, title=', briefData.title);
      }
      if (body.production_plan) {
        planData = body.production_plan;
        console.log('[test] [capture] production_plan received, scenes=', planData.scene_count);
      }
      if (body.reply) lastReply = body.reply;
    } catch {}
  });

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  try {
    // ── 1. 首页 ──
    console.log('[test] ===== Step 1: Homepage =====');
    await page.goto(FRONTEND, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);

    const topicInput = page.locator('input[placeholder*="选题"]');
    await topicInput.fill(topic);
    const scriptTextarea = page.locator('textarea[placeholder*="口播"]');
    await scriptTextarea.fill(script);
    try {
      const zamBtn = page.locator('button', { hasText: 'Zam' }).first();
      await zamBtn.click({ timeout: 5000 });
      console.log('[test] Persona Zam selected');
    } catch { console.log('[test] Zam not found'); }

    await page.locator('button', { hasText: '开始创作' }).first().click();
    await page.waitForURL(/\/editor\//, { timeout: 30000 });
    await page.waitForTimeout(3000);
    console.log('[test] Editor loaded');

    // ── 2. 等待简报 ──
    console.log('[test] ===== Step 2: Wait for creative brief =====');
    let briefReady = false;
    for (let i = 0; i < 90; i++) {
      await page.waitForTimeout(3000);
      if (briefData) { briefReady = true; break; }
      const text = await page.textContent('body').catch(() => '');
      if (text.includes('创作方案已完成') || text.includes('已为你生成创意简报')) {
        briefReady = true;
        break;
      }
      if (i % 8 === 0) console.log(`[test]   waiting brief... (${(i+1)*3}s)`);
    }
    console.log(`[test] Brief ready: ${briefReady}, captured: ${!!briefData}`);
    if (!briefData) {
      console.log('[test] FAIL: brief data not captured');
      process.exit(1);
    }

    // 输出简报
    console.log('\n========== 简报内容 ==========');
    console.log(JSON.stringify(briefData, null, 2));
    fs.writeFileSync('e2e-brief.json', JSON.stringify(briefData, null, 2), 'utf-8');

    // ── 3. 点击"确认简报"按钮 → 生成规划书 ──
    console.log('\n[test] ===== Step 3: Click 确认简报, wait for plan =====');
    try {
      await page.waitForSelector('button:has-text("确认简报")', { timeout: 30000 });
      const confirmBriefBtn = page.locator('button', { hasText: '确认简报' }).first();
      await confirmBriefBtn.scrollIntoViewIfNeeded().catch(() => {});
      await confirmBriefBtn.click({ timeout: 8000 });
      console.log('[test] 确认简报 clicked');
    } catch (e) {
      console.log('[test] confirm brief button error:', e);
      // 诊断：dump 页面状态
      try {
        const bodyText = await page.textContent('body').catch(() => '');
        console.log('[test] [diag] body 含"需求 Agent":', bodyText.includes('需求 Agent'));
        console.log('[test] [diag] body 含"创意简报":', bodyText.includes('创意简报'));
        console.log('[test] [diag] body 含"确认简报":', bodyText.includes('确认简报'));
        console.log('[test] [diag] body 含"继续与需求":', bodyText.includes('继续与需求'));
        await page.screenshot({ path: 'e2e-requirements-diag.png', fullPage: true });
        console.log('[test] [diag] 截图已保存 e2e-requirements-diag.png');
      } catch {}
      // 回退：用对话输入框发送确认
      try {
        const chatInput = page.locator('input[placeholder*="继续与需求"]').first();
        await chatInput.fill('确认，请生成完整的制作规划书。', { timeout: 5000 });
        await page.waitForTimeout(500);
        await chatInput.press('Enter');
        console.log('[test] 回退：对话输入框发送确认');
      } catch (e2) {
        console.log('[test] fallback chat input error:', e2);
      }
    }

    let planReady = false;
    for (let i = 0; i < 200; i++) { // up to 10 min
      await page.waitForTimeout(3000);
      if (planData) { planReady = true; break; }
      const text = await page.textContent('body').catch(() => '');
      if (text.includes('成片规划书已生成')) { planReady = true; break; }
      if (i % 10 === 0) console.log(`[test]   waiting plan... (${(i+1)*3}s)`);
    }
    console.log(`[test] Plan ready: ${planReady}, captured: ${!!planData}`);
    if (!planData) {
      console.log('[test] FAIL: plan data not captured');
      process.exit(1);
    }

    // 输出规划书
    console.log('\n========== 规划书内容 ==========');
    const md = planData.markdown_content || '(无 markdown)';
    console.log(md);
    fs.writeFileSync('e2e-plan.json', JSON.stringify(planData, null, 2), 'utf-8');
    fs.writeFileSync('e2e-plan.md', md, 'utf-8');

    // ── 4. 汇总 ──
    console.log('\n========== 汇总 ==========');
    console.log('简报标题:', briefData.title);
    console.log('规划书场景数:', planData.scene_count, '总时长:', planData.total_duration_sec);
    console.log('页面错误:', errors.length);
    console.log('文件已保存: e2e-brief.json / e2e-plan.json / e2e-plan.md');
    console.log('[test] 浏览器 20 秒后关闭');
    await page.waitForTimeout(20000);
    process.exit(0);
  } catch (e) {
    console.error('[test] FATAL:', e);
    try { await page.screenshot({ path: 'e2e-requirements-error.png', fullPage: true }); } catch {}
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
