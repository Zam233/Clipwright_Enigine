export interface VoiceRecord {
  id: string;
  provider: string;
  voice_id: string;
  voice_name: string;
  target_model: string;
  created_at: string;
}

export interface VoiceUploadResponse {
  filename: string;
  saved_as: string;
  size: number;
  data_uri: string;
  mime: string;
}

export interface VoiceCloneRequest {
  voice_name: string;
  data_uri?: string;
  audio_url?: string;
  audio_path?: string;
  provider?: string;
  target_model?: string;
  audition_text?: string;
}

export interface VoiceSynthesizeRequest {
  voice_id: string;
  text: string;
  provider?: string;
  target_model?: string;
  instructions?: string;
}

export interface VoiceSynthesizeResponse {
  audio_path: string;
  audio_url: string;
  duration_sec: number;
  voice_id: string;
  provider: string;
  text: string;
}

export interface VoiceDubRequest {
  voice_id: string;
  text: string;
  split_mode?: 'sentence' | 'paragraph';
  provider?: string;
  target_model?: string;
  instructions?: string;
}

export interface VoiceDubSegment {
  index: number;
  text: string;
  audio_path?: string;
  audio_url?: string;
  duration_sec?: number;
  error?: string;
}

export interface VoiceDubResponse {
  segments: VoiceDubSegment[];
  total: number;
  total_duration_sec: number;
}
