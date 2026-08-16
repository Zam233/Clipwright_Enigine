import { useEffect, useState } from 'react';
import { useProjectStore } from '@/stores/projectStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { useHistoryStore } from '@/stores/historyStore';
import { voiceApi } from '@/services/api';
import { mediaManager } from '@/services/media/mediaManager';
import { AudioPlayer } from '@/components/shared/AudioPlayer';
import { Button } from '@/components/ui';
import { uid } from '@/lib/utils';
import { VoicePage } from '@/pages/VoicePage';
import type { VoiceRecord, VoiceDubSegment } from '@/types/voice';
import { Mic, Plus, Loader2, Check, X } from 'lucide-react';

export function DubView() {
  const voiceId = useProjectStore((s) => s.voiceId);
  const scriptText = useProjectStore((s) => s.scriptText);
  const setVoiceId = useProjectStore((s) => s.setVoiceId);
  const setScriptText = useProjectStore((s) => s.setScriptText);

  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dubbing, setDubbing] = useState(false);
  const [dubError, setDubError] = useState<string | null>(null);
  const [dubSegments, setDubSegments] = useState<VoiceDubSegment[]>([]);
  const [dubAudioUrl, setDubAudioUrl] = useState<string | null>(null);
  const [dubTotal, setDubTotal] = useState(0);
  const [added, setAdded] = useState(false);

  const refreshVoices = () => { voiceApi.list().then(setVoices).catch(() => {}); };
  useEffect(() => { voiceApi.list().then(setVoices).catch(() => {}); }, []);

  const handleConfirm = async () => {
    if (!voiceId) { setDubError('请先选择音色'); return; }
    if (!scriptText.trim()) { setDubError('请输入配音文案'); return; }
    setDubbing(true);
    setDubError(null);
    setDubSegments([]);
    setDubAudioUrl(null);
    setAdded(false);
    try {
      const res = await voiceApi.dub({ voice_id: voiceId, text: scriptText });
      setDubSegments(res.segments);
      setDubTotal(res.total_duration_sec);
      const first = res.segments.find((s) => s.audio_url);
      if (first?.audio_url) setDubAudioUrl(voiceApi.getAudioUrl(first.audio_url));
      else setDubError('配音未返回音频');
      // B21: 配音段写入 projectStore（供需求/管线 dub_segments 对齐场景时间）。
      // 后端返回的段无 start/end，按累计时长计算时间轴区间。
      let cursor = 0;
      const mapped = res.segments.map((s) => {
        const dur = s.duration_sec && s.duration_sec > 0 ? s.duration_sec : 3;
        const seg = { start: cursor, end: cursor + dur, text: s.text };
        cursor += dur;
        return seg;
      });
      useProjectStore.getState().setDubSegments(mapped);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (e instanceof Error ? e.message : '配音失败');
      setDubError(detail);
    } finally {
      setDubbing(false);
    }
  };

  const handleAddToTimeline = () => {
    const store = useTimelineStore.getState();
    // 先快照历史（addTrack 之前），避免撤销后残留空轨道
    useHistoryStore.getState().pushState(store.timeline, 'add-dub');
    let track = store.timeline.tracks.find((t) => t.kind === 'audio');
    if (!track) {
      const tid = store.addTrack('audio');
      // addTrack 后必须重读 store 状态（旧快照不含新轨道）
      track = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
    }
    if (!track) return;
    let atSec = track.clips.reduce((m, c) => Math.max(m, c.start_sec + c.duration_sec), 0);
    const segs = dubSegments.filter((s) => s.audio_url);
    const fallbackDur = segs.length ? dubTotal / segs.length : 3;
    for (const seg of segs) {
      const assetId = uid('dub');
      const url = voiceApi.getAudioUrl(seg.audio_url!);
      mediaManager.registerUrl(assetId, url, 'audio');
      const dur = seg.duration_sec && seg.duration_sec > 0 ? seg.duration_sec : fallbackDur;
      store.addClip(track.id, {
        kind: 'audio' as const,
        asset_id: assetId,
        duration_sec: dur,
        start_sec: atSec,
        metadata: { title: seg.text?.slice(0, 20) || '配音片段' },
      });
      atSec += dur;
    }
    setAdded(true);
  };

  return (
    <div className="p-3 space-y-4">
      <div>
        <label className="flex items-center gap-1.5 text-label font-medium text-on-surface-variant mb-2">
          <Mic className="w-3.5 h-3.5 text-track-audio" /> 音色
          <span className="text-error">*</span>
        </label>
        <div className="flex gap-1.5">
          <select
            value={voiceId ?? ''}
            onChange={(e) => setVoiceId(e.target.value || null)}
            className="flex-1 bg-surface-container rounded-cw-xs px-2.5 py-1.5 text-body-sm text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary cursor-pointer"
          >
            <option value="">（请选择音色）</option>
            {voices.map((v) => (
              <option key={v.id} value={v.id}>{v.voice_name} ({v.provider})</option>
            ))}
          </select>
          <button
            onClick={() => setPickerOpen(true)}
            title="克隆 / 管理音色"
            className="shrink-0 w-9 flex items-center justify-center bg-surface-container rounded-cw-xs
              border border-outline-variant/30 text-on-surface-variant hover:text-primary hover:border-primary
              transition-colors cursor-pointer"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div>
        <label className="text-label font-medium text-on-surface-variant block mb-1.5">配音文案</label>
        <textarea
          value={scriptText}
          onChange={(e) => setScriptText(e.target.value)}
          rows={5}
          placeholder="输入要配音的文案…"
          className="w-full bg-surface-container rounded-cw-xs px-2.5 py-1.5 text-label-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary resize-none placeholder:text-on-surface-variant/50"
        />
      </div>

      <Button onClick={handleConfirm} disabled={dubbing || !voiceId} className="w-full">
        {dubbing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
        {dubbing ? '配音中…' : '确认配音'}
      </Button>

      {dubError && <p className="text-caption text-error">{dubError}</p>}

      {dubAudioUrl && (
        <div className="space-y-2 border-t border-outline-variant/20 pt-3">
          <p className="text-caption text-on-surface-variant">
            共 {dubSegments.filter((s) => s.audio_url).length} 段 · 总时长 {dubTotal.toFixed(0)}s
          </p>
          <AudioPlayer src={dubAudioUrl} />
          <Button size="sm" variant="outline" onClick={handleAddToTimeline} disabled={added} className="w-full">
            {added ? '已加入时间轴' : '加入时间轴'}
          </Button>
        </div>
      )}

      {pickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setPickerOpen(false)}>
          <div
            className="relative w-full max-w-3xl h-[80vh] bg-surface rounded-cw-lg border border-outline-variant/40 shadow-xl overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setPickerOpen(false)}
              className="absolute top-3 right-3 z-10 p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
            <VoicePage
              embedded
              onSelect={(v) => { setVoiceId(v.id); setPickerOpen(false); refreshVoices(); }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
