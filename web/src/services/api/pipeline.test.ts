// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getApiClient } from '@/services/api/client';
import { pipelineApi } from './pipeline';

vi.mock('@/services/api/client', () => ({
  getApiClient: vi.fn(),
}));

const mockGetApiClient = vi.mocked(getApiClient);

describe('pipelineApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('cancel POSTs to /api/pipeline/cancel/{id} and returns data', async () => {
    const post = vi.fn().mockResolvedValue({ data: { status: 'cancelled' } });
    mockGetApiClient.mockReturnValue({ post } as unknown as ReturnType<typeof getApiClient>);

    const result = await pipelineApi.cancel('proj_pipeline_123');

    expect(mockGetApiClient).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/pipeline/cancel/proj_pipeline_123');
    expect(result).toEqual({ status: 'cancelled' });
  });
});
