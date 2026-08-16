// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getApiClient } from '@/services/api/client';
import { versionApi } from './project';

vi.mock('@/services/api/client', () => ({
  getApiClient: vi.fn(),
}));

const mockGetApiClient = vi.mocked(getApiClient);

describe('versionApi (G1 版本历史接线)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('list GET /api/project/{id}/versions', async () => {
    const versions = [{ version_id: 'v_1', time: '2026-01-01T00:00:00', label: '初版', position: 0, is_current: true }];
    const get = vi.fn().mockResolvedValue({ data: versions });
    mockGetApiClient.mockReturnValue({ get } as unknown as ReturnType<typeof getApiClient>);

    const result = await versionApi.list('proj_1');
    expect(get).toHaveBeenCalledWith('/api/project/proj_1/versions');
    expect(result).toEqual(versions);
  });

  it('snapshot POST with label', async () => {
    const post = vi.fn().mockResolvedValue({ data: { version_id: 'v_2', count: 2 } });
    mockGetApiClient.mockReturnValue({ post } as unknown as ReturnType<typeof getApiClient>);

    const result = await versionApi.snapshot('proj_1', '手动快照');
    expect(post).toHaveBeenCalledWith('/api/project/proj_1/versions', { label: '手动快照' });
    expect(result.count).toBe(2);
  });

  it('restore POST /versions/{position}/restore', async () => {
    const timeline = { id: 'proj_1', width: 1920, height: 1080, fps: 30, duration_sec: 5, tracks: [], markers: [] };
    const post = vi.fn().mockResolvedValue({ data: { version_id: 'v_0', timeline } });
    mockGetApiClient.mockReturnValue({ post } as unknown as ReturnType<typeof getApiClient>);

    const result = await versionApi.restore('proj_1', 0);
    expect(post).toHaveBeenCalledWith('/api/project/proj_1/versions/0/restore');
    expect(result.timeline.duration_sec).toBe(5);
  });

  it('clear DELETE /versions', async () => {
    const del = vi.fn().mockResolvedValue({ data: { deleted: true } });
    mockGetApiClient.mockReturnValue({ delete: del } as unknown as ReturnType<typeof getApiClient>);

    const result = await versionApi.clear('proj_1');
    expect(del).toHaveBeenCalledWith('/api/project/proj_1/versions');
    expect(result.deleted).toBe(true);
  });
});
