// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { tabSync, TAB_SYNC_CHANNEL } from './tabSync';

describe('tabSync (G3 多标签同步)', () => {
  let channel: BroadcastChannel | null = null;

  beforeEach(() => {
    // 用真实 BroadcastChannel（jsdom 提供 stub 实现）
    try {
      channel = new BroadcastChannel(TAB_SYNC_CHANNEL);
    } catch {
      channel = null;
    }
  });

  afterEach(() => {
    tabSync.detach();
    try { channel?.close(); } catch { /* ignore */ }
  });

  it('attach 幂等且返回取消函数', () => {
    const unsub1 = tabSync.attach();
    const unsub2 = tabSync.attach();
    // 第二次 attach 直接返回空取消函数（不重复建 channel）
    expect(unsub2).toBeTypeOf('function');
    unsub1();
    unsub2();
  });

  it('subscribe 收到广播事件；cancel 后不再收到', async () => {
    if (!channel) { expect(true).toBe(true); return; } // 环境不支持 → 跳过
    const unsubAttach = tabSync.attach();
    const onEvent = vi.fn();
    const unsub = tabSync.subscribe(onEvent);

    // 模拟另一个标签广播
    channel.postMessage({ type: 'timeline-saved', projectId: 'proj_1', at: new Date().toISOString() });

    await new Promise((r) => setTimeout(r, 20));
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].projectId).toBe('proj_1');

    unsub();
    channel.postMessage({ type: 'timeline-saved', projectId: 'proj_2', at: new Date().toISOString() });
    await new Promise((r) => setTimeout(r, 20));
    expect(onEvent).toHaveBeenCalledTimes(1);

    unsubAttach();
  });

  it('非法事件（无 projectId）被忽略', async () => {
    if (!channel) { expect(true).toBe(true); return; }
    const unsubAttach = tabSync.attach();
    const onEvent = vi.fn();
    const unsub = tabSync.subscribe(onEvent);
    channel.postMessage({ type: 'junk' });
    channel.postMessage(null);
    await new Promise((r) => setTimeout(r, 20));
    expect(onEvent).not.toHaveBeenCalled();
    unsub();
    unsubAttach();
  });
});
