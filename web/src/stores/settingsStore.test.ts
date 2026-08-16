// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/**
 * settingsStore 连接配置持久化测试。
 * 通过 vi.resetModules() + 动态 import 模拟「关闭页面后重新加载」，
 * 断言 set 写入 localStorage 后新模块实例读取到一致值。
 */

const CONN_KEY = 'clipwright.connectionPrefs';
// 默认值与 settingsStore 同源（本地 .env 可能覆盖，不写死端口）
const DEFAULT_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const DEFAULT_WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws';

async function importFreshStore() {
  vi.resetModules();
  return await import('./settingsStore');
}

describe('settingsStore 连接配置持久化', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.resetModules();
  });

  it('setApiBaseUrl/setWsUrl/setAuthToken 后重新加载模块读取到一致值', async () => {
    const { useSettingsStore } = await importFreshStore();
    useSettingsStore.getState().setApiBaseUrl('http://192.168.1.10:8000');
    useSettingsStore.getState().setWsUrl('ws://192.168.1.10:8000/ws');
    useSettingsStore.getState().setAuthToken('test-token-123');

    // localStorage 中应已写入
    const raw = localStorage.getItem(CONN_KEY);
    expect(raw).not.toBeNull();
    const saved = JSON.parse(raw!) as Record<string, unknown>;
    expect(saved.apiBaseUrl).toBe('http://192.168.1.10:8000');
    expect(saved.wsUrl).toBe('ws://192.168.1.10:8000/ws');
    expect(saved.authToken).toBe('test-token-123');

    // 模拟刷新页面：重置模块缓存后重新加载
    const fresh = await importFreshStore();
    expect(fresh.useSettingsStore.getState().apiBaseUrl).toBe('http://192.168.1.10:8000');
    expect(fresh.useSettingsStore.getState().wsUrl).toBe('ws://192.168.1.10:8000/ws');
    expect(fresh.useSettingsStore.getState().authToken).toBe('test-token-123');
  });

  it('authToken 置空(null)后持久化，重新加载读取为 null', async () => {
    const { useSettingsStore } = await importFreshStore();
    useSettingsStore.getState().setAuthToken('abc');
    useSettingsStore.getState().setAuthToken(null);

    const fresh = await importFreshStore();
    expect(fresh.useSettingsStore.getState().authToken).toBeNull();
  });

  it('localStorage 无持久化数据时回退到环境变量默认值', async () => {
    const { useSettingsStore } = await importFreshStore();
    expect(useSettingsStore.getState().apiBaseUrl).toBe(DEFAULT_API_BASE_URL);
    expect(useSettingsStore.getState().wsUrl).toBe(DEFAULT_WS_URL);
    expect(useSettingsStore.getState().authToken).toBeNull();
  });

  it('localStorage 数据损坏时回退默认值且不抛异常', async () => {
    localStorage.setItem(CONN_KEY, '{broken json');
    const { useSettingsStore } = await importFreshStore();
    expect(useSettingsStore.getState().apiBaseUrl).toBe(DEFAULT_API_BASE_URL);
    expect(useSettingsStore.getState().authToken).toBeNull();
  });

  it('字段类型非法（如数字）时回退默认值', async () => {
    localStorage.setItem(
      CONN_KEY,
      JSON.stringify({ apiBaseUrl: 42, wsUrl: null, authToken: 7 }),
    );
    const { useSettingsStore } = await importFreshStore();
    expect(useSettingsStore.getState().apiBaseUrl).toBe(DEFAULT_API_BASE_URL);
    expect(useSettingsStore.getState().wsUrl).toBe(DEFAULT_WS_URL);
    expect(useSettingsStore.getState().authToken).toBeNull();
  });
});
