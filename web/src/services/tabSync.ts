/**
 * tabSync — 多标签同步（G3）。同一项目在多个标签页打开时，
 * 任一标签保存后广播事件，其他标签收到后重新拉取项目时间线。
 *
 * 使用 BroadcastChannel（同源多标签页通信）；不支持时静默降级为 no-op。
 * 仅广播「已保存」事件，不传递时间线内容本身（避免大对象序列化开销，
 * 接收端通过 GET /api/project/{id} 拉取权威状态）。
 */

export const TAB_SYNC_CHANNEL = 'clipwright-tab-sync';

export interface TabSyncEvent {
  type: 'timeline-saved' | 'ping';
  projectId: string;
  at: string;
}

class TabSync {
  private channel: BroadcastChannel | null = null;
  private listeners = new Set<(ev: TabSyncEvent) => void>();

  /** 初始化 channel 并返回取消订阅函数。幂等。 */
  attach(): () => void {
    if (this.channel) return () => {};
    try {
      this.channel = new BroadcastChannel(TAB_SYNC_CHANNEL);
      this.channel.onmessage = (e: MessageEvent<TabSyncEvent>) => {
        const ev = e.data;
        if (!ev || typeof ev !== 'object' || !ev.projectId) return;
        this.listeners.forEach((fn) => fn(ev));
      };
    } catch {
      this.channel = null; // 环境不支持 → no-op
    }
    return () => this.detach();
  }

  detach(): void {
    try { this.channel?.close(); } catch { /* ignore */ }
    this.channel = null;
  }

  /** 广播「时间线已保存」事件。 */
  broadcastSaved(projectId: string): void {
    if (!this.channel || !projectId) return;
    const ev: TabSyncEvent = { type: 'timeline-saved', projectId, at: new Date().toISOString() };
    try {
      this.channel.postMessage(ev);
    } catch { /* ignore */ }
  }

  /** 订阅事件；返回取消订阅函数。 */
  subscribe(fn: (ev: TabSyncEvent) => void): () => void {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }
}

/** 单例。 */
export const tabSync = new TabSync();
