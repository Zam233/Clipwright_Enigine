import { getApiClient } from './client';

// ── Types (aligned with backend clipwright/api/subtitle.py + stt.py) ──

/** Timeline caption clip shape (segment → clip) */
export interface SubtitleClip {
  text: string;
  start_sec: number;
  end_sec: number;
  duration_sec?: number;
  [key: string]: unknown;
}

export interface SubtitleImportResponse {
  segments: number;
  clips: SubtitleClip[];
  srt: string;
}

export interface SubtitleExportResponse {
  segments: number;
  srt: string;
}

export interface SubtitleTranscribeResponse {
  success: boolean;
  segments: number;
  language?: string;
  duration_sec?: number;
  model?: string;
  error?: string;
  clips?: SubtitleClip[];
  srt?: string;
}

export const subtitleApi = {
  /** 导入 SRT 字幕内容 → Timeline caption clips */
  async importSrt(srtContent: string): Promise<SubtitleImportResponse> {
    const { data } = await getApiClient().post('/api/subtitle/import', { srt_content: srtContent });
    return data;
  },

  /** 将 Timeline caption clips → SRT 格式文本 */
  async exportSrt(clips: SubtitleClip[]): Promise<SubtitleExportResponse> {
    const { data } = await getApiClient().post('/api/subtitle/export', clips);
    return data;
  },

  /** 从音频自动转录 → 生成带时间戳的字幕 */
  async transcribe(params: {
    audio_path: string;
    language?: string;
    model_size?: string;
    format?: 'timeline' | 'srt';
  }): Promise<SubtitleTranscribeResponse> {
    const { data } = await getApiClient().post('/api/subtitle/transcribe', {
      audio_path: params.audio_path,
      language: params.language ?? '',
      model_size: params.model_size ?? 'base',
      format: params.format ?? 'timeline',
    });
    return data;
  },

  /** 将已有文案与音频对齐 → 生成带时间戳的字幕 */
  async align(params: {
    audio_path: string;
    script_text: string;
    language?: string;
    format?: 'timeline' | 'srt';
  }): Promise<SubtitleTranscribeResponse> {
    const { data } = await getApiClient().post('/api/subtitle/align', {
      audio_path: params.audio_path,
      script_text: params.script_text,
      language: params.language ?? '',
      format: params.format ?? 'timeline',
    });
    return data;
  },
};

// ── STT（独立 /api/stt 端点）──

export interface SttSegment {
  start: number;
  end: number;
  text: string;
  words?: Array<{ word: string; start: number; end: number }>;
}

export interface SttResponse {
  success: boolean;
  segments: SttSegment[];
  language?: string;
  duration_sec?: number;
  model?: string;
  error?: string;
}

export const sttApi = {
  /** 将音频/视频文件转录为带时间戳的文字 */
  async transcribe(params: {
    audio_path: string;
    language?: string;
    model_size?: string;
    word_timestamps?: boolean;
  }): Promise<SttResponse> {
    const { data } = await getApiClient().post('/api/stt/transcribe', {
      audio_path: params.audio_path,
      language: params.language ?? '',
      model_size: params.model_size ?? 'base',
      word_timestamps: params.word_timestamps ?? true,
    });
    return data;
  },

  /** 将已有文案与音频对齐，生成带时间戳的字幕分段 */
  async align(params: {
    audio_path: string;
    transcript_text: string;
    language?: string;
  }): Promise<SttResponse> {
    const { data } = await getApiClient().post('/api/stt/align', {
      audio_path: params.audio_path,
      transcript_text: params.transcript_text,
      language: params.language ?? '',
    });
    return data;
  },
};
