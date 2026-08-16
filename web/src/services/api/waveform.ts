import { getApiClient } from './client';

export interface WaveformData {
  samples: number[];
  duration_sec: number;
}

export const waveformApi = {
  async generate(audioPath: string, samples?: number): Promise<WaveformData> {
    const { data } = await getApiClient().post('/api/waveform/generate', {
      audio_path: audioPath,
      samples: samples ?? 200,
    });
    return data;
  },
};
