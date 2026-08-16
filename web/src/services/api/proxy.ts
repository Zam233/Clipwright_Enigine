import { getApiClient } from './client';

export const proxyApi = {
  /** Generate a low-res proxy file for a high-res source. */
  async generate(inputPath: string, proxyHeight = 720): Promise<Record<string, unknown>> {
    const { data } = await getApiClient().post('/api/proxy/generate', {
      input_path: inputPath,
      proxy_height: proxyHeight,
    });
    return data;
  },

  /** Rewrite the timeline so asset paths point back to full-res originals. */
  async switchToFull(timeline: Record<string, unknown>): Promise<Record<string, unknown>> {
    const { data } = await getApiClient().post('/api/proxy/switch', {
      timeline,
      proxy_suffix: '',
    });
    return data;
  },

  /** Rewrite the timeline so asset paths point to low-res proxy files. */
  async switchToProxy(
    timeline: Record<string, unknown>,
    proxySuffix = '_proxy_720p',
  ): Promise<Record<string, unknown>> {
    const { data } = await getApiClient().post('/api/proxy/switch', {
      timeline,
      proxy_suffix: proxySuffix,
    });
    return data;
  },
};
