// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { requirementsApi } from './requirements';

vi.mock('@/services/api/sse', () => ({
  apiBase: () => 'http://localhost:8000',
  fetchSseToken: async () => '',
}));

describe('requirementsApi.streamChat (W1 SSE 流式消费)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function makeStream(chunks: string[]) {
    const encoder = new TextEncoder();
    return {
      ok: true,
      body: {
        getReader: () => {
          let i = 0;
          return {
            read: async () => {
              if (i >= chunks.length) return { done: true, value: undefined };
              return { done: false, value: encoder.encode(chunks[i++]) };
            },
          };
        },
      },
    } as unknown as Response;
  }

  it('解析 SSE 事件并回调 chunk，返回 result payload', async () => {
    const stream = makeStream([
      'data: {"type":"status","data":"typing"}\n\n',
      'data: {"type":"result","data":{"reply":"你好","status":"brief_ready"}}\n\n',
      'data: [DONE]\n\n',
    ]);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(stream);

    const onChunk = vi.fn();
    const result = await requirementsApi.streamChat('sess_1', '帮我', onChunk);

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/requirements/chat/stream/sess_1',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(onChunk).toHaveBeenCalledWith({ type: 'status', data: 'typing' });
    expect(result.reply).toBe('你好');
    expect(result.status).toBe('brief_ready');
  });

  it('HTTP 失败抛错（调用方回退一次性 chat）', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false, status: 500 });
    await expect(requirementsApi.streamChat('sess_2', 'hi')).rejects.toThrow('chat stream failed: 500');
  });
});
