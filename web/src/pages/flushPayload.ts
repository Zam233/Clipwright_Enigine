import type { Timeline } from '@/types/timeline';

/**
 * F3: pagehide/beforeunload 冲刷保存的负载大小上限（48KB）。
 * pagehide 中使用 keepalive 同步发送超大负载可能被浏览器静默丢弃，
 * 超过该阈值时退化为仅保存紧凑元数据，避免大项目数据静默丢失。
 */
export const FLUSH_PAYLOAD_LIMIT = 48 * 1024;

/** 卸载冲刷负载的输入：项目关键字段 + 完整时间线。 */
export interface FlushPayloadInput {
  project_id?: string;
  name?: string;
  timeline: Timeline;
  persona_id?: string;
  plugin_id?: string;
}

/**
 * 决策卸载冲刷负载：
 * - 序列化 ≤48KB → kind 'full'，发送完整负载（原路径不变）；
 * - 序列化 >48KB → kind 'metadata'，仅保存紧凑摘要
 *   （项目 id + 轨道/片段计数 + 最后编辑时间），保证可被 keepalive 可靠发送。
 */
export function decideFlushPayload(
  input: FlushPayloadInput,
): { kind: 'full' | 'metadata'; payload: unknown } {
  if (JSON.stringify(input).length <= FLUSH_PAYLOAD_LIMIT) {
    return { kind: 'full', payload: input };
  }
  const timeline = input.timeline;
  return {
    kind: 'metadata',
    payload: {
      project_id: input.project_id,
      name: input.name,
      timeline: {
        id: timeline.id,
        duration_sec: timeline.duration_sec,
        track_count: timeline.tracks.length,
        clip_count: timeline.tracks.reduce((n, t) => n + t.clips.length, 0),
        last_edit_at: new Date().toISOString(),
      },
      persona_id: input.persona_id,
      plugin_id: input.plugin_id,
    },
  };
}
