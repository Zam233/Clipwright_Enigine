/**
 * P3-3B: 内存会话令牌单例。
 * 避免 stores ↔ services 循环依赖：access token 只存内存（不落 localStorage），
 * 由 authStore 写入、axios 拦截器/裸 fetch 读取。
 */

let _token: string | null = null;

export const session = {
  get token(): string | null {
    return _token;
  },
  setToken(token: string | null): void {
    _token = token;
  },
};
