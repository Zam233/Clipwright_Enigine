// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { useAgentStore, MAX_LOG_ENTRIES } from './agentStore';

beforeEach(() => {
  useAgentStore.setState({ logEntries: [] });
});

describe('G2: cancelling state', () => {
  it('cancelling 默认值为 false', () => {
    expect(useAgentStore.getState().cancelling).toBe(false);
  });

  it('setCancelling(true) 置为 true，setCancelling(false) 置回 false', () => {
    useAgentStore.getState().setCancelling(true);
    expect(useAgentStore.getState().cancelling).toBe(true);
    useAgentStore.getState().setCancelling(false);
    expect(useAgentStore.getState().cancelling).toBe(false);
  });

  it('先置 true 后 resetPipeline() 会重置为 false', () => {
    useAgentStore.getState().setCancelling(true);
    expect(useAgentStore.getState().cancelling).toBe(true);
    useAgentStore.getState().resetPipeline();
    expect(useAgentStore.getState().cancelling).toBe(false);
  });
});

describe('E7: log entry cap', () => {
  it('601 条 addLogEntry 后仅保留最近 500 条', () => {
    const s = useAgentStore.getState();
    for (let i = 0; i < 601; i++) {
      s.addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'info', summary: `e-${i}` });
    }
    const entries = useAgentStore.getState().logEntries;
    expect(entries.length).toBe(MAX_LOG_ENTRIES);
    expect(entries.length).toBe(500);
    expect(entries[0].summary).toBe('e-101'); // 最旧 101 条被丢弃
    expect(entries[entries.length - 1].summary).toBe('e-600');
  });

  it('addLogEntries 批量超限也被裁剪', () => {
    const entries = Array.from({ length: 600 }, (_, i) => ({
      timestamp: Date.now(), agent: 'system', type: 'info' as const, summary: `b-${i}`,
    }));
    useAgentStore.getState().addLogEntries(entries);
    const list = useAgentStore.getState().logEntries;
    expect(list.length).toBe(500);
    expect(list[0].summary).toBe('b-100');
  });

  it('低于上限不裁剪', () => {
    const s = useAgentStore.getState();
    for (let i = 0; i < 10; i++) {
      s.addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'info', summary: `s-${i}` });
    }
    expect(useAgentStore.getState().logEntries.length).toBe(10);
  });

  it('clearLogs 清空', () => {
    const s = useAgentStore.getState();
    s.addLogEntry({ timestamp: Date.now(), agent: 'system', type: 'info', summary: 'x' });
    s.clearLogs();
    expect(useAgentStore.getState().logEntries.length).toBe(0);
  });
});
