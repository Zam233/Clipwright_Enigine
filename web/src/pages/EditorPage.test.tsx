// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, cleanup } from '@testing-library/react';
import { decideFlushPayload, FLUSH_PAYLOAD_LIMIT } from './flushPayload';
import { EditorPage } from './EditorPage';
import { createEmptyTimeline, createDefaultClip, type Timeline, type Track } from '@/types/timeline';
import { useProjectStore } from '@/stores/projectStore';
import { useAgentStore } from '@/stores/agentStore';

// EditorLayout 及其面板较重；测试只验证参数捕获/恢复逻辑，stub 掉 UI 层
vi.mock('@/layouts/EditorLayout', () => ({ EditorLayout: () => null }));
vi.mock('@/features/keyboard/useGlobalKeybindings', () => ({
  useGlobalKeybindings: () => ({ cheatSheetOpen: false, setCheatSheetOpen: vi.fn() }),
}));
vi.mock('@/features/keyboard/ShortcutCheatSheet', () => ({ ShortcutCheatSheet: () => null }));
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ projectId: 'proj_test_1' }),
}));
vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: { registerTimeline: vi.fn(), clear: vi.fn() },
}));
vi.mock('@/stores/toastStore', () => ({ toast: vi.fn() }));
vi.mock('@/features/agent/useRequirementsAutoStart', () => ({
  useRequirementsAutoStart: () => {},
}));

const { mocks } = vi.hoisted(() => ({
  mocks: {
    projectLoad: vi.fn(),
    projectSave: vi.fn(),
    getSession: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  projectApi: {
    load: mocks.projectLoad,
    save: mocks.projectSave,
    getThumbnailUrl: vi.fn(),
  },
  requirementsApi: { getSession: mocks.getSession },
  getApiClient: () => ({ defaults: { baseURL: 'http://localhost:8000' } }),
}));

const emptyTimeline = createEmptyTimeline('tl_editor');

beforeEach(() => {
  vi.clearAllMocks();
  mocks.projectLoad.mockResolvedValue({
    id: 'proj_test_1', name: '测试项目', persona_id: null, plugin_id: null,
    timeline: emptyTimeline, agent_state: null,
  });
  mocks.getSession.mockRejectedValue(new Error('offline'));
});

afterEach(() => {
  cleanup();
  useProjectStore.getState().resetProject();
  useAgentStore.getState().resetRequirements();
});

describe('B16: launch params restore after resetProject', () => {
  it('恢复全部启动参数（文稿/音色/配音/模式/Persona）', async () => {
    // 模拟 HomePage.launch() 写入全部字段
    useProjectStore.setState({
      projectId: 'proj_test_1',
      requirementsTopic: '主题T',
      requirementsScript: '长文稿',
      requirementsAudioDuration: 120,
      materialSourceIds: ['src1'],
      scriptText: '长文稿',
      videoMode: 'voiceover',
      splitMode: 'period',
      audioPath: '/tmp/a.mp3',
      audioDurationSec: 118,
      voiceId: 'v1',
      autoDub: false,
      personaId: 'pers1',
      pluginId: 'plug1',
    });

    render(<EditorPage />);
    await waitFor(() => expect(mocks.projectLoad).toHaveBeenCalled());

    const st = useProjectStore.getState();
    // resetProject 被调用后会清空，但 EditorPage 恢复段应全部写回
    expect(st.requirementsTopic).toBe('主题T');
    expect(st.scriptText).toBe('长文稿');
    expect(st.videoMode).toBe('voiceover');
    expect(st.splitMode).toBe('period');
    expect(st.audioPath).toBe('/tmp/a.mp3');
    expect(st.audioDurationSec).toBe(118);
    expect(st.voiceId).toBe('v1');
    expect(st.autoDub).toBe(false);
    expect(st.personaId).toBe('pers1');
    expect(st.pluginId).toBe('plug1');
    expect(st.materialSourceIds).toEqual(['src1']);
  });

  it('非首页启动（无 pendingTopic）不误写，走 agent_state 恢复', async () => {
    useProjectStore.setState({ requirementsTopic: '', requirementsScript: '' });
    mocks.projectLoad.mockResolvedValue({
      id: 'proj_test_1', name: '测试项目', persona_id: 'pers1', plugin_id: 'plug1',
      timeline: emptyTimeline,
      agent_state: {
        requirementsSessionId: 'req_1',
        requirementsStatus: 'plan_ready',
        requirementsMessages: [{ id: 'm1', role: 'assistant', content: '恢复消息', timestamp: new Date().toISOString() }],
        creativeBrief: null, productionPlan: null, logEntries: [],
      },
    });
    mocks.getSession.mockResolvedValue({
      session_id: 'req_1', status: 'plan_ready',
      messages: [{ role: 'assistant', content: '后端权威消息', timestamp: new Date().toISOString() }],
      creative_brief: null, production_plan: null,
    });

    render(<EditorPage />);
    await waitFor(() => expect(mocks.getSession).toHaveBeenCalled());

    const ag = useAgentStore.getState();
    expect(ag.requirementsSessionId).toBe('req_1');
    expect(ag.requirementsStatus).toBe('plan_ready');
    expect(ag.requirementsMessages.some((m) => m.content === '后端权威消息')).toBe(true);
  });

  it('agent_state 恢复但后端离线 → 保留本地草稿', async () => {
    useProjectStore.setState({ requirementsTopic: '', requirementsScript: '' });
    mocks.projectLoad.mockResolvedValue({
      id: 'proj_test_1', name: '测试项目', persona_id: null, plugin_id: null,
      timeline: emptyTimeline,
      agent_state: {
        requirementsSessionId: 'req_2',
        requirementsStatus: 'plan_ready',
        requirementsMessages: [{ id: 'm1', role: 'assistant', content: '本地草稿', timestamp: new Date().toISOString() }],
        creativeBrief: null, productionPlan: null, logEntries: [],
      },
    });
    mocks.getSession.mockRejectedValue(new Error('offline'));

    render(<EditorPage />);
    await waitFor(() => expect(mocks.projectLoad).toHaveBeenCalled());
    await waitFor(() => expect(mocks.getSession).toHaveBeenCalled());

    const ag = useAgentStore.getState();
    expect(ag.requirementsSessionId).toBe('req_2');
    expect(ag.requirementsMessages.some((m) => m.content === '本地草稿')).toBe(true);
  });
});


function makeTimelineWithTextBytes(textBytes: number): Timeline {
  const tl = createEmptyTimeline('tl_test_1');
  const track: Track = {
    id: 'trk_1',
    name: 'V1',
    kind: 'video',
    index: 0,
    locked: false,
    muted: false,
    clips: [],
  };
  const clip = createDefaultClip({
    id: 'clip_1',
    kind: 'video',
    track_id: track.id,
    text: 'x'.repeat(textBytes),
  });
  track.clips.push(clip);
  tl.tracks.push(track);
  return tl;
}

function makeInput(textLen: number) {
  return {
    project_id: 'proj_test_1',
    name: '测试项目',
    timeline: makeTimelineWithTextBytes(textLen),
  };
}

describe('decideFlushPayload (F3 payload-size guard)', () => {
  it('small timeline (<48KB) → kind "full", payload is the full input', () => {
    const input = makeInput(100);
    const d = decideFlushPayload(input);
    expect(d.kind).toBe('full');
    expect(d.payload).toBe(input);
    expect(JSON.stringify(d.payload).length).toBeLessThan(FLUSH_PAYLOAD_LIMIT);
  });

  it('large timeline (>48KB) → kind "metadata", compact payload <48KB with project id and no full tracks', () => {
    const input = makeInput(60 * 1024);
    expect(JSON.stringify(input).length).toBeGreaterThan(FLUSH_PAYLOAD_LIMIT);
    const d = decideFlushPayload(input);
    expect(d.kind).toBe('metadata');

    const payload = d.payload as {
      project_id: string;
      timeline: { track_count: number; clip_count: number; last_edit_at: string };
    };
    expect(payload.project_id).toBe('proj_test_1');
    expect(payload.timeline).toBeDefined();
    expect(payload.timeline.track_count).toBe(1);
    expect(payload.timeline.clip_count).toBe(1);
    expect(typeof payload.timeline.last_edit_at).toBe('string');
    expect((d.payload as { timeline: unknown }).timeline).not.toHaveProperty('tracks');
    expect((d.payload as { timeline: unknown }).timeline).not.toHaveProperty('width');

    // 元数据本身必须远小于阈值，可被 keepalive 可靠发送
    expect(JSON.stringify(d.payload).length).toBeLessThan(FLUSH_PAYLOAD_LIMIT);
  });

  it('exactly at the boundary (==48KB) → kind "full"', () => {
    // 计算使整个序列化负载恰好等于阈值的文本长度
    const base = JSON.stringify(makeInput(0)).length;
    const textLen = FLUSH_PAYLOAD_LIMIT - base;
    expect(textLen).toBeGreaterThan(0);

    const input = makeInput(textLen);
    expect(JSON.stringify(input).length).toBe(FLUSH_PAYLOAD_LIMIT);

    const d = decideFlushPayload(input);
    expect(d.kind).toBe('full');
    expect(d.payload).toBe(input);
  });
});
