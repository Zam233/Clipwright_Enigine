import type { Page } from '@playwright/test';

export const demoTimeline = {
  id: 'tl-e2e',
  width: 1920,
  height: 1080,
  fps: 30,
  duration_sec: 8,
  tracks: [
    {
      id: 'track-v1',
      name: 'VIDEO 1',
      kind: 'video',
      index: 0,
      locked: false,
      muted: false,
      clips: [
        {
          id: 'clip-v1',
          kind: 'video',
          asset_id: 'demo-asset',
          track_id: 'track-v1',
          start_sec: 0,
          duration_sec: 5,
          source_offset_sec: 0,
          speed: 1,
          volume: 1,
          opacity: 1,
          image_fit: null,
          image_rect: null,
          text: null,
          font: null,
          font_size: null,
          font_color: null,
          text_align: null,
          transition_in: null,
          transition_out: null,
          transition_duration_sec: null,
          shape: null,
          fill: null,
          bar_count: null,
          bar_width: null,
          keyframes: [],
          metadata: { title: 'E2E 演示片段' },
        },
      ],
    },
    {
      id: 'track-t1',
      name: 'TEXT 1',
      kind: 'text',
      index: 1,
      locked: false,
      muted: false,
      clips: [],
    },
    {
      id: 'track-c1',
      name: 'CAPTION 1',
      kind: 'caption',
      index: 2,
      locked: false,
      muted: false,
      clips: [
        {
          id: 'clip-c1',
          kind: 'caption',
          asset_id: 'caption-c1',
          track_id: 'track-c1',
          start_sec: 0,
          duration_sec: 2,
          source_offset_sec: 0,
          speed: 1,
          volume: 1,
          opacity: 1,
          image_fit: null,
          image_rect: null,
          text: '第一句字幕',
          font: 'Inter',
          font_size: 48,
          font_color: '#FFFFFF',
          text_align: 'center',
          font_weight: 'normal',
          font_italic: false,
          letter_spacing: 0,
          stroke_width: 0,
          stroke_color: '#000000',
          shadow_x: 0,
          shadow_y: 0,
          shadow_blur: 0,
          shadow_color: '#000000',
          glow_width: 4,
          glow_color: '#FFFFFF',
          transition_in: null,
          transition_out: null,
          transition_duration_sec: null,
          shape: null,
          fill: null,
          bar_count: null,
          bar_width: null,
          keyframes: [],
          metadata: {},
        },
        {
          id: 'clip-c2',
          kind: 'caption',
          asset_id: 'caption-c2',
          track_id: 'track-c1',
          start_sec: 3,
          duration_sec: 2,
          source_offset_sec: 0,
          speed: 1,
          volume: 1,
          opacity: 1,
          image_fit: null,
          image_rect: null,
          text: '第二句字幕',
          font: 'Inter',
          font_size: 48,
          font_color: '#FFFFFF',
          text_align: 'center',
          font_weight: 'normal',
          font_italic: false,
          letter_spacing: 0,
          stroke_width: 0,
          stroke_color: '#000000',
          shadow_x: 0,
          shadow_y: 0,
          shadow_blur: 0,
          shadow_color: '#000000',
          glow_width: 0,
          glow_color: '#000000',
          transition_in: null,
          transition_out: null,
          transition_duration_sec: null,
          shape: null,
          fill: null,
          bar_count: null,
          bar_width: null,
          keyframes: [],
          metadata: {},
        },
      ],
    },
  ],
};

export const demoImageTimeline = {
  id: 'tl-img-demo',
  width: 1920,
  height: 1080,
  fps: 30,
  duration_sec: 5,
  tracks: [
    {
      id: 'track-img1',
      name: 'IMAGE 1',
      kind: 'image',
      index: 0,
      locked: false,
      muted: false,
      clips: [
        {
          id: 'clip-img1',
          kind: 'image',
          asset_id: 'asset-img',
          track_id: 'track-img1',
          start_sec: 0,
          duration_sec: 5,
          source_offset_sec: 0,
          speed: 1,
          volume: 1,
          opacity: 1,
          image_fit: null,
          image_rect: null,
          text: null,
          font: null,
          font_size: null,
          font_color: null,
          text_align: null,
          transition_in: null,
          transition_out: null,
          transition_duration_sec: null,
          shape: null,
          fill: null,
          bar_count: null,
          bar_width: null,
          keyframes: [],
          metadata: { title: '红色测试图', url: 'http://mock.local/red.png' },
        },
      ],
    },
  ],
};

export const demoImageProject = {
  id: 'proj_img_demo',
  name: 'E2E 图片项目',
  timeline: demoImageTimeline,
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
  persona_id: 'default',
  plugin_id: 'knowledge_longform',
  folder: '',
  tags: [],
};

export const demoProject = {
  id: 'proj_e2e_demo',
  name: 'E2E 演示项目',
  timeline: demoTimeline,
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
  persona_id: 'default',
  plugin_id: 'knowledge_longform',
  folder: '',
  tags: [],
};

export const demoSummary = {
  id: 'proj_e2e_demo',
  name: 'E2E 演示项目',
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
  persona_id: 'default',
  plugin_id: 'knowledge_longform',
  folder: '',
  tags: [],
  track_count: 3,
  duration_sec: 8,
  has_thumbnail: false,
};

/**
 * 拦截全部后端请求，使 E2E 不依赖真实后端（hermetic）。
 * 注意：用正则限定「路径以 /api/ 或 /health 开头」，避免误拦截 Vite 的
 * 模块请求（如 /src/services/api/client.ts）。
 */
export async function mockBackendApi(page: Page): Promise<void> {
  await page.route(/https?:\/\/[^/]+\/health(\?|$)/, (route) =>
    route.fulfill({ json: { status: 'ok', service: 'clipwright-engine' } }),
  );

  await page.route(/https?:\/\/[^/]+\/api\//, (route) => {
    const url = route.request().url();

    if (/\/api\/project\/proj_img_demo(\/|$|\?)/.test(url)) {
      return route.fulfill({ json: demoImageProject });
    }
    if (/\/api\/project\/proj_e2e_demo(\/|$|\?)/.test(url)) {
      return route.fulfill({ json: demoProject });
    }
    if (/\/api\/project(\/?$|\?)/.test(url)) {
      return route.fulfill({ json: [demoSummary] });
    }
    if (url.includes('/api/plugin/list')) {
      return route.fulfill({ json: [] });
    }
    if (url.includes('/api/persona')) {
      return route.fulfill({ json: [] });
    }
    if (route.request().method() === 'POST' && /\/api\/requirements\/init(\?|$)/.test(url)) {
      // 需求会话初始化：返回固定 session_id，供后续 chat/edit 使用
      return route.fulfill({ json: { session_id: 'sess-e2e', status: 'gathering' } });
    }
    if (route.request().method() === 'POST' && /\/api\/requirements\/chat(\?|$)/.test(url)) {
      return route.fulfill({ json: { reply: '（E2E mock）已收到你的需求。', status: 'gathering' } });
    }
    if (route.request().method() === 'POST' && /\/api\/requirements\/edit(\?|$)/.test(url)) {
      // C6 时间线编辑：返回假 proposed_timeline，触发 TimelineDiffView 审阅
      return route.fulfill({
        json: {
          status: 'gathering',
          reply: '已根据指令调整时间线（E2E mock）。',
          action: 'adjust',
          proposed_timeline: {
            ...demoTimeline,
            duration_sec: 9,
            tracks: demoTimeline.tracks.map((t) => (t.id === 'track-v1'
              ? { ...t, clips: [{ ...t.clips[0], duration_sec: 6, metadata: { ...t.clips[0].metadata, url: 'http://mock/v.mp4' } }] }
              : t)),
          },
        },
      });
    }
    if (route.request().method() === 'GET' && url.includes('/api/asset/by-path')) {
      // by-path 媒体代理：返回 1x1 PNG 占位
      return route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
          'base64',
        ),
      });
    }
    return route.fulfill({ json: {} });
  });
}

/** 收集页面 JS 错误，测试结束时断言为空。 */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(err.message));
  return errors;
}
