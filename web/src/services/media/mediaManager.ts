/**
 * MediaManager — real media playback, thumbnails and waveform extraction.
 *
 * Bridges uploaded Files (object URLs) and backend-hosted assets (proxy URLs)
 * into HTMLVideoElement / HTMLAudioElement / WebAudio analysis. Falls back
 * gracefully when no real media is available (demo assets).
 */

import type { Timeline, Clip } from '@/types/timeline';

/** Resolve a clip's real-media URL for preview playback. */
export function resolveMediaUrl(clip: Pick<Clip, 'metadata' | 'asset_id'>): string | undefined {
  const meta = clip.metadata ?? {};
  const rawUrl = typeof meta.url === 'string' ? meta.url : '';
  // 优先使用 HTTP URL（Agent 时间线可直接预览）；否则把本地路径/asset_id 包装成 by-path 代理
  if (/^https?:\/\//i.test(rawUrl)) return rawUrl;
  const localPath = typeof meta.local_path === 'string' ? meta.local_path : '';
  const path = localPath || clip.asset_id;
  if (!path) return undefined;
  return `/api/asset/by-path?path=${encodeURIComponent(path)}`;
}

interface MediaEntry {
  url: string;
  kind: 'video' | 'audio' | 'image';
  videoEl?: HTMLVideoElement;
  audioEl?: HTMLAudioElement;
  img?: HTMLImageElement;
  durationSec: number;
  waveform?: number[];
  thumbnails: Map<number, string>;
  /** true when url is a blob: object URL that must be revoked on unregister */
  isObjectUrl?: boolean;
  /** true when the media element fired an error event (404 / network) — keeps url for retry */
  error?: boolean;
}

class MediaManager {
  private entries = new Map<string, MediaEntry>();
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private analyserSource: MediaElementAudioSourceNode | null = null;
  private sourceNodes = new WeakMap<HTMLMediaElement, MediaElementAudioSourceNode>();

  /** Register an uploaded File so its real media becomes available. */
  registerFile(assetId: string, file: File): void {
    const url = URL.createObjectURL(file);
    const kind: MediaEntry['kind'] = file.type.startsWith('video')
      ? 'video'
      : file.type.startsWith('audio')
        ? 'audio'
        : 'image';
    const entry: MediaEntry = { url, kind, durationSec: 0, thumbnails: new Map(), isObjectUrl: true };

    if (kind === 'video') {
      const v = document.createElement('video');
      v.src = url;
      v.preload = 'auto';
      v.muted = true;
      v.playsInline = true;
      v.addEventListener('loadedmetadata', () => {
        entry.durationSec = v.duration;
        this.notify(assetId);
      });
      v.addEventListener('error', () => {
        entry.error = true;
        this.notify(assetId);
      });
      entry.videoEl = v;
    } else if (kind === 'audio') {
      const a = new Audio();
      a.src = url;
      a.preload = 'auto';
      a.addEventListener('loadedmetadata', () => {
        entry.durationSec = a.duration;
        this.notify(assetId);
      });
      a.addEventListener('error', () => {
        entry.error = true;
        this.notify(assetId);
      });
      entry.audioEl = a;
    } else {
      // image: probe dimensions via an Image
      const img = new Image();
      img.crossOrigin = 'anonymous'; // no-op for same-origin blob URLs, but required for canvas reads later
      img.src = url;
      img.onload = () => this.notify(assetId);
      img.onerror = () => {
        entry.error = true;
        this.notify(assetId);
      };
      entry.img = img;
    }

    this.entries.set(assetId, entry);
  }

  /** Register a backend-hosted asset by proxy URL. */
  registerUrl(assetId: string, url: string, kind: MediaEntry['kind']): void {
    if (this.entries.has(assetId)) return;
    const entry: MediaEntry = { url, kind, durationSec: 0, thumbnails: new Map() };
    if (kind === 'video') {
      const v = document.createElement('video');
      v.src = url;
      v.preload = 'metadata';
      v.muted = true;
      v.crossOrigin = 'anonymous';
      v.addEventListener('loadedmetadata', () => { entry.durationSec = v.duration; this.notify(assetId); });
      v.addEventListener('error', () => {
        entry.error = true;
        this.notify(assetId);
      });
      entry.videoEl = v;
    } else if (kind === 'audio') {
      const a = new Audio();
      a.src = url;
      a.preload = 'metadata';
      a.crossOrigin = 'anonymous';
      a.addEventListener('loadedmetadata', () => { entry.durationSec = a.duration; this.notify(assetId); });
      a.addEventListener('error', () => {
        entry.error = true;
        this.notify(assetId);
      });
      entry.audioEl = a;
    } else if (kind === 'image') {
      const img = new Image();
      img.crossOrigin = 'anonymous'; // must precede src assignment so the fetch uses CORS mode
      img.src = url;
      img.onload = () => this.notify(assetId);
      img.onerror = () => {
        entry.error = true;
        this.notify(assetId);
      };
      entry.img = img;
    }
    this.entries.set(assetId, entry);
  }

  /**
   * registerTimeline — 把时间线上所有媒体片段（video/audio/image）注册为真实媒体。
   *
   * 幂等：已注册的 asset_id 跳过；无法解析 URL 的片段静默跳过（预览回退占位块）。
   */
  registerTimeline(timeline: Timeline): void {
    for (const track of timeline.tracks) {
      for (const clip of track.clips) {
        if (clip.kind !== 'video' && clip.kind !== 'audio' && clip.kind !== 'image') continue;
        if (this.entries.has(clip.asset_id)) continue;
        const url = resolveMediaUrl(clip);
        if (!url) continue;
        this.registerUrl(clip.asset_id, url, clip.kind);
      }
    }
  }

  get(assetId: string): MediaEntry | undefined {
    return this.entries.get(assetId);
  }

  /** Unregister an asset and release its resources (object URL, media elements, caches). */
  unregister(assetId: string): void {
    const e = this.entries.get(assetId);
    if (!e) return;
    if (e.videoEl) {
      e.videoEl.pause();
      e.videoEl.removeAttribute('src');
      e.videoEl.load();
    }
    if (e.audioEl) {
      e.audioEl.pause();
      e.audioEl.removeAttribute('src');
      e.audioEl.load();
    }
    e.thumbnails.clear();
    if (e.isObjectUrl) URL.revokeObjectURL(e.url);
    this.entries.delete(assetId);
    this.notify(assetId);
  }

  /** Release all registered assets (e.g. on project switch / editor unmount). */
  clear(): void {
    for (const id of [...this.entries.keys()]) this.unregister(id);
  }

  /** Pause all playing media elements without releasing them. */
  pauseAll(): void {
    for (const e of this.entries.values()) {
      if (e.videoEl && !e.videoEl.paused) e.videoEl.pause();
      if (e.audioEl && !e.audioEl.paused) e.audioEl.pause();
    }
  }

  hasRealMedia(assetId: string): boolean {
    const e = this.entries.get(assetId);
    return !!e && (e.kind !== 'image' ? !!e.videoEl || !!e.audioEl : true);
  }

  getDuration(assetId: string): number {
    return this.entries.get(assetId)?.durationSec ?? 0;
  }

  getMediaUrl(assetId: string): string | undefined {
    return this.entries.get(assetId)?.url;
  }

  /** Seek a video asset to a time and return the element (for canvas drawing). */
  seekVideo(assetId: string, timeSec: number): HTMLVideoElement | undefined {
    const e = this.entries.get(assetId);
    if (!e?.videoEl) return undefined;
    const v = e.videoEl;
    const target = Math.max(0, Math.min(timeSec, (e.durationSec || timeSec) - 0.01));
    if (Math.abs(v.currentTime - target) > 0.03) {
      try { v.currentTime = target; } catch { /* not ready */ }
    }
    return v;
  }

  /** Capture a thumbnail frame as a dataURL (cached per time bucket). */
  async captureThumbnail(assetId: string, timeSec = 0.1, width = 160): Promise<string | null> {
    const e = this.entries.get(assetId);
    if (!e) return null;

    if (e.kind === 'image') return e.url;

    const bucket = Math.round(timeSec * 2) / 2;
    const cached = e.thumbnails.get(bucket);
    if (cached) return cached;

    if (e.kind === 'video' && e.videoEl) {
      return new Promise((resolve) => {
        const v = e.videoEl!;
        const grab = () => {
          try {
            const c = document.createElement('canvas');
            const scale = width / (v.videoWidth || width);
            c.width = width;
            c.height = Math.round((v.videoHeight || 90) * scale);
            const ctx = c.getContext('2d')!;
            ctx.drawImage(v, 0, 0, c.width, c.height);
            const dataUrl = c.toDataURL('image/jpeg', 0.7);
            e.thumbnails.set(bucket, dataUrl);
            resolve(dataUrl);
          } catch {
            resolve(null);
          }
        };
        if (v.readyState >= 2) {
          v.currentTime = Math.min(bucket, (e.durationSec || bucket) - 0.05);
          v.addEventListener('seeked', grab, { once: true });
        } else {
          v.addEventListener('loadeddata', () => {
            v.currentTime = Math.min(bucket, (e.durationSec || bucket) - 0.05);
            v.addEventListener('seeked', grab, { once: true });
          }, { once: true });
        }
      });
    }
    return null;
  }

  /** Extract waveform peaks (0-1) via WebAudio decodeAudioData. Cached. */
  async getWaveform(assetId: string, buckets = 120): Promise<number[] | null> {
    const e = this.entries.get(assetId);
    if (!e) return null;
    if (e.waveform && e.waveform.length === buckets) return e.waveform;

    const srcEl = e.audioEl ?? e.videoEl;
    if (!srcEl) return null;

    try {
      if (!this.audioCtx) {
        this.audioCtx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      }
      const resp = await fetch(e.url);
      const buf = await resp.arrayBuffer();
      const audioBuf = await this.audioCtx.decodeAudioData(buf);
      const data = audioBuf.getChannelData(0);
      const block = Math.floor(data.length / buckets) || 1;
      const peaks: number[] = [];
      for (let i = 0; i < buckets; i++) {
        let max = 0;
        const start = i * block;
        for (let j = start; j < start + block && j < data.length; j += 8) {
          const v = Math.abs(data[j]);
          if (v > max) max = v;
        }
        peaks.push(Math.min(1, max));
      }
      e.waveform = peaks;
      return peaks;
    } catch {
      return null;
    }
  }

  /** Synchronous read of cached waveform peaks (null if not yet decoded). */
  getCachedWaveform(assetId: string): number[] | null {
    return this.entries.get(assetId)?.waveform ?? null;
  }

  /** Synchronous read of a cached thumbnail at a time bucket (dataURL or null). */
  getCachedThumbnailAt(assetId: string, timeSec: number): string | null {
    const e = this.entries.get(assetId);
    if (!e) return null;
    if (e.kind === 'image') return e.url;
    const bucket = Math.round(timeSec * 2) / 2;
    return e.thumbnails.get(bucket) ?? null;
  }

  /** Fire-and-forget: ensure a thumbnail capture has been kicked off. */
  ensureThumbnail(assetId: string, timeSec: number): void {
    const e = this.entries.get(assetId);
    if (!e || e.kind !== 'video') return;
    const bucket = Math.round(timeSec * 2) / 2;
    if (e.thumbnails.has(bucket)) return;
    e.thumbnails.set(bucket, ''); // in-flight marker
    this.captureThumbnail(assetId, bucket).then((url) => {
      if (!url) e.thumbnails.delete(bucket);
      this.notify(assetId);
    });
  }

  /** Fire-and-forget: ensure waveform decoding has been kicked off. */
  ensureWaveform(assetId: string, buckets = 120): void {
    const e = this.entries.get(assetId);
    if (!e || e.waveform) return;
    if (e.kind === 'audio' || e.kind === 'video') {
      // mark as in-flight by storing an empty array to avoid duplicate kicks
      e.waveform = [];
      this.getWaveform(assetId, buckets).then((peaks) => {
        if (!peaks) e.waveform = undefined;
        this.notify(assetId);
      });
    }
  }

  // ── simple change notification ───────────────────────
  private listeners = new Set<(assetId: string) => void>();
  onChange(cb: (assetId: string) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
  private notify(assetId: string) {
    this.listeners.forEach((cb) => cb(assetId));
  }

  // ── audio level metering ─────────────────────────────
  private ensureAudioCtx(): AudioContext {
    if (!this.audioCtx) {
      this.audioCtx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    }
    return this.audioCtx;
  }

  /** Attach analyser to an audio-capable media entry for level metering. */
  attachAnalyser(assetId: string): AnalyserNode | null {
    const e = this.entries.get(assetId);
    if (!e || (e.kind !== 'audio' && e.kind !== 'video')) return null;
    const el = e.audioEl ?? e.videoEl;
    if (!el) return null;
    const ctx = this.ensureAudioCtx();
    // 浏览器可能以 suspended 状态创建 AudioContext（非用户手势路径），静默无声；
    // 播放已由用户点击触发，此时尝试恢复
    if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
    if (!this.analyser) {
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.8;
      this.analyser.connect(ctx.destination);
    }
    let src = this.sourceNodes.get(el);
    if (!src) {
      src = ctx.createMediaElementSource(el);
      this.sourceNodes.set(el, src);
    }
    if (this.analyserSource && this.analyserSource !== src) {
      try { this.analyserSource.disconnect(); } catch { /* ignore */ }
    }
    this.analyserSource = src;
    src.connect(this.analyser);
    return this.analyser;
  }

  /** Get current audio levels (0-1) — simplified peak detection. */
  getAudioLevels(): [number, number] {
    if (!this.analyser) return [0, 0];
    const data = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(data);
    let leftMax = 0;
    let rightMax = 0;
    for (let i = 0; i < data.length; i += 2) {
      const v = Math.abs(data[i] / 128 - 1);
      if (v > leftMax) leftMax = v;
    }
    for (let i = 1; i < data.length; i += 2) {
      const v = Math.abs(data[i] / 128 - 1);
      if (v > rightMax) rightMax = v;
    }
    return [Math.min(1, leftMax), Math.min(1, rightMax)];
  }
}

export const mediaManager = new MediaManager();
