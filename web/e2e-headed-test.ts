/**
 * 有界面浏览器完整 E2E 测试（headed）
 * 全流程由前端浏览器操作
 */

import { chromium } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = 'http://localhost:5173';
const BACKEND = 'http://localhost:8000';
const CONTENT_PATH = path.resolve('文理', 'content.md');
const VOICE_PATH = path.resolve('文理', 'voice.MP3');

async function readContent(): Promise<{ topic: string; script: string }> {
  const text = fs.readFileSync(CONTENT_PATH, 'utf-8');
  const topicMatch = text.match(/\*\*标题[：:]\*\*\s*(.+)/);
  const topic = topicMatch ? topicMatch[1].trim() : '文理之争';
  const scriptMatch = text.match(/\*\*文案[：:]\*\*\s*\n([\s\S]+)/);
  let script = scriptMatch ? scriptMatch[1].trim() : text;
  if (script.length > 10000) script = script.slice(0, 10000);
  console.log(`[e2e] Topic: ${topic}, Script length: ${script.length}`);
  return { topic, script };
}

async function main() {
  const { topic, script } = await readContent();
  if (!fs.existsSync(VOICE_PATH)) {
    console.error(`[e2e] ERROR: Voice file not found: ${VOICE_PATH}`);
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: false, slowMo: 80 });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(err.message));

  try {
    // ─── Step 1: Navigate to homepage ───
    console.log('[e2e] ===== Step 1: Homepage =====');
    await page.goto(FRONTEND, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);

    // ─── Step 2: Fill in topic ───
    console.log('[e2e] ===== Step 2: Fill form =====');
    // The topic input has placeholder containing "选题"
    const topicInput = page.locator('input[placeholder*="选题"]');
    await topicInput.click();
    await topicInput.fill(topic);
    console.log('[e2e] Topic filled');

    // ─── Step 3: Fill in script ───
    const scriptTextarea = page.locator('textarea[placeholder*="口播"]');
    await scriptTextarea.click();
    await scriptTextarea.fill(script);
    console.log('[e2e] Script filled');

    // ─── Step 4: Select persona Zam (click button containing "Zam") ───
    try {
      const zamBtn = page.locator('button', { hasText: 'Zam' }).first();
      await zamBtn.click({ timeout: 5000 });
      console.log('[e2e] Persona Zam selected');
    } catch (e) {
      console.log('[e2e] WARN: Zam selection skipped');
    }

    // ─── Step 5: Upload voice file ───
    console.log('[e2e] Uploading voice...');
    const fileInput = page.locator('input[type="file"]').first();
    await fileInput.setInputFiles(VOICE_PATH);
    // Wait for upload + audio duration detection
    await page.waitForTimeout(5000);
    console.log('[e2e] Voice uploaded');

    // ─── Step 6: Click "开始创作" ───
    console.log('[e2e] ===== Step 6: Start creation =====');
    const startBtn = page.locator('button', { hasText: '开始创作' }).first();
    await startBtn.click();
    console.log('[e2e] Navigating to editor...');

    // Wait for editor to load (URL contains /editor/)
    await page.waitForURL(/\/editor\//, { timeout: 30000 });
    await page.waitForTimeout(4000);
    console.log('[e2e] Editor loaded');

    // ─── Step 7: Requirements Agent ───
    console.log('[e2e] ===== Step 7: Requirements Agent =====');
    
    // Requirements agent auto-starts. Wait for brief.
    let briefReady = false;
    for (let i = 0; i < 90; i++) {
      await page.waitForTimeout(4000);
      const pageText = await page.textContent('body') || '';
      if (pageText.includes('创作方案已完成') || 
          pageText.includes('已为你生成创意简报') ||
          pageText.includes('brief_ready')) {
        briefReady = true;
        console.log(`[e2e] Brief ready at ${(i+1)*4}s`);
        break;
      }
      if (i % 10 === 0) {
        console.log(`[e2e]   Waiting for brief... (${(i+1)*4}s)`);
      }
    }
    if (!briefReady) console.log('[e2e] WARN: Brief not confirmed ready');

    // ─── Step 8: Confirm brief ───
    console.log('[e2e] ===== Step 8: Confirm brief =====');
    await page.waitForTimeout(2000);
    
    // Find chat input (any textarea or input in the agent panel)
    const chatInput = page.locator('textarea, input:not([type="file"])').last();
    try {
      await chatInput.fill('确认', { timeout: 5000 });
      await page.waitForTimeout(500);
      
      // Click send button or press Enter
      const sendBtn = page.locator('button[type="submit"], button:has-text("发送"), button:has-text("Send")').first();
      const sendVisible = await sendBtn.isVisible({ timeout: 2000 }).catch(() => false);
      if (sendVisible) {
        await sendBtn.click();
      } else {
        await chatInput.press('Enter');
      }
      console.log('[e2e] Brief confirmed');
    } catch (e) {
      console.log('[e2e] Brief confirm error, trying Enter:', e);
      try { await chatInput.press('Enter'); } catch {}
    }

    // ─── Step 9: Wait for plan ───
    console.log('[e2e] ===== Step 9: Wait for plan =====');
    let planReady = false;
    for (let i = 0; i < 90; i++) {
      await page.waitForTimeout(4000);
      const pageText = await page.textContent('body') || '';
      if (pageText.includes('成片规划书已生成') || 
          pageText.includes('plan_ready') ||
          pageText.includes('production_plan')) {
        planReady = true;
        console.log(`[e2e] Plan ready at ${(i+1)*4}s`);
        break;
      }
      if (i % 10 === 0) {
        console.log(`[e2e]   Waiting for plan... (${(i+1)*4}s)`);
      }
    }
    if (!planReady) console.warn('[e2e] WARN: plan not detected in 6 min, continuing anyway');

    // ─── Step 10: Confirm plan ───
    console.log('[e2e] ===== Step 10: Confirm plan =====');
    await page.waitForTimeout(2000);
    try {
      const ci = page.locator('textarea, input:not([type="file"])').last();
      await ci.fill('确认', { timeout: 5000 });
      await page.waitForTimeout(500);
      const sb = page.locator('button[type="submit"], button:has-text("发送")').first();
      const sv = await sb.isVisible({ timeout: 2000 }).catch(() => false);
      if (sv) { await sb.click(); } else { await ci.press('Enter'); }
      console.log('[e2e] Plan confirmed');
    } catch (e) {
      console.log('[e2e] Plan confirm error:', e);
    }

    // ─── Step 11: Pipeline execution ───
    console.log('[e2e] ===== Step 11: Pipeline =====');
    await page.waitForTimeout(5000);

    let pipelineDone = false;
    const pipelineStart = Date.now();
    for (let i = 0; i < 240; i++) { // up to 20 min
      await page.waitForTimeout(5000);
      
      try {
        const pageText = await page.textContent('body') || '';
        if (pageText.includes('COMPLETED') || pageText.includes('pipeline_done') ||
            (pageText.includes('completed') && pageText.includes('pipeline')) ||
            pageText.includes('管线完成') || pageText.includes('生成完成')) {
          pipelineDone = true;
          console.log(`[e2e] Pipeline done via UI at ${(i+1)*5}s`);
          break;
        }
      } catch {}

        // Check health endpoint for queue status
        try {
          const resp = await fetch(`${BACKEND}/health`);
          if (resp.ok) {
            const data = await resp.json() as any;
            if (data?.queue_running === '0' && data?.queue_pending === '0' && i > 20) {
              console.log(`[e2e] No active pipelines, checking timeline...`);
              break;
            }
          }
        } catch {}

      if (i % 12 === 0) {
        const elapsed = Math.round((Date.now() - pipelineStart) / 1000);
        console.log(`[e2e]   Pipeline running... (${elapsed}s)`);
      }
    }

    const totalElapsed = Math.round((Date.now() - pipelineStart) / 1000);
    console.log(`[e2e] Pipeline phase: ${totalElapsed}s`);

    // ─── Step 12: Verify timeline ───
    console.log('[e2e] ===== Step 12: Verify =====');
    await page.waitForTimeout(3000);
    
    // Take final screenshot
    await page.screenshot({ path: 'e2e-result.png', fullPage: true });
    console.log('[e2e] Screenshot: e2e-result.png');

    // Check page for timeline content
    const finalText = await page.textContent('body') || '';
    const hasTimeline = /timeline|时间轴/i.test(finalText);
    const hasDuration = /duration|\d{3,}s|时长/i.test(finalText);
    const hasClips = /clip|track|片段|轨道/i.test(finalText);
    
    console.log(`[e2e] Results: timeline=${hasTimeline}, duration=${hasDuration}, clips=${hasClips}`);
    console.log(`[e2e] Pipeline: ${pipelineDone ? 'DONE' : 'TIMEOUT'}`);
    console.log(`[e2e] Errors: ${errors.length}`);
    if (errors.length) console.log('[e2e] Page errors:', errors.slice(0, 5));

    const success = pipelineDone && errors.length === 0;
    console.log(`[e2e] ===== ${success ? 'SUCCESS' : 'PARTIAL'} =====`);

    // Keep browser open
    console.log('[e2e] Browser closes in 15s...');
    await page.waitForTimeout(15000);
    process.exit(success ? 0 : 1);

  } catch (e) {
    console.error('[e2e] FATAL:', e);
    try { await page.screenshot({ path: 'e2e-error.png', fullPage: true }); } catch {}
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
