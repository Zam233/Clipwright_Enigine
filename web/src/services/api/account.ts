import axios from 'axios';

/**
 * 账号 API 客户端（P3-3B）—— 指向 ClipWright Server 端点。
 * 同源 /srv：开发由 vite proxy 转发 8090，生产由反向代理路由 /srv。
 * withCredentials: refresh token 走 httpOnly cookie（Server 端 Set-Cookie）。
 */

const srv = axios.create({ baseURL: '/srv', withCredentials: true, timeout: 15_000 });

export interface AccountUser {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  quotas: Record<string, number>;
  usage: Record<string, number>;
}

export const accountApi = {
  async login(email: string, password: string) {
    const { data } = await srv.post('/api/auth/login', { email, password });
    return data as { access_token: string; refresh_token: string };
  },

  async register(email: string, password: string, displayName = '') {
    const { data } = await srv.post('/api/auth/register', {
      email,
      password,
      display_name: displayName,
    });
    return data as { access_token: string; refresh_token: string };
  },

  /** 用 httpOnly cookie 中的 refresh token 换取新 access token。 */
  async refresh() {
    const { data } = await srv.post('/api/auth/refresh', { refresh_token: '' });
    return data as { access_token: string };
  },

  async logout() {
    await srv.post('/api/auth/logout');
  },

  async me(accessToken?: string) {
    const { data } = await srv.get('/api/auth/me', {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
    return data as AccountUser;
  },
};
