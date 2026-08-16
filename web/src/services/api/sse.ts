import { getApiClient } from './client';

/**
 * P0-9/P0-10: EventSource 鉴权工具。
 * EventSource 无法携带 Authorization 头；令牌模式下改用后端签发的
 * 短期一次性 query token（POST /api/auth/sse-token，需 Bearer）。
 */

/** 获取 SSE 一次性 token；开放模式或失败时返回空串。 */
export async function fetchSseToken(): Promise<string> {
  try {
    const { data } = await getApiClient().post<{ token: string; expires_in: number }>(
      '/api/auth/sse-token',
    );
    return data?.token ?? '';
  } catch {
    return '';
  }
}

/** 为 EventSource URL 拼接一次性 token（token 为空时原样返回）。 */
export function withSseToken(url: string, token: string): string {
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

/** API base 归一：配置了绝对地址用它；否则同源（生产部署/后端挂载静态）或浏览器 origin（EventSource 需要绝对 URL）。 */
export function apiBase(): string {
  const base = getApiClient().defaults.baseURL;
  if (base) return base;
  if (typeof window !== 'undefined') return window.location.origin;
  return '';
}
