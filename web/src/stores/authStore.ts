import { create } from 'zustand';
import { accountApi, type AccountUser } from '@/services/api/account';
import { session } from '@/services/api/session';

interface AuthState {
  accessToken: string | null;
  user: AccountUser | null;
  /** 已尝试恢复会话（防止刷新/登录两个逻辑互相干扰） */
  restoreAttempted: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  /** 用 httpOnly cookie 刷新 access token；成功返回 true，失败清空会话返回 false */
  refresh: () => Promise<boolean>;
  /** 页面加载时尝试恢复会话（cookie 存在且未过期则自动登录） */
  restore: () => Promise<boolean>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  restoreAttempted: false,

  login: async (email, password) => {
    const res = await accountApi.login(email, password);
    const user = await accountApi.me(res.access_token);
    session.setToken(res.access_token);
    set({ accessToken: res.access_token, user });
  },

  register: async (email, password, displayName = '') => {
    const res = await accountApi.register(email, password, displayName);
    const user = await accountApi.me(res.access_token);
    session.setToken(res.access_token);
    set({ accessToken: res.access_token, user });
  },

  logout: async () => {
    try {
      await accountApi.logout();
    } catch {
      /* 网络失败也继续清空本地会话 */
    }
    session.setToken(null);
    set({ accessToken: null, user: null });
  },

  refresh: async () => {
    try {
      const res = await accountApi.refresh();
      if (!res?.access_token) {
        session.setToken(null);
        set({ accessToken: null, user: null });
        return false;
      }
      const user = await accountApi.me(res.access_token);
      session.setToken(res.access_token);
      set({ accessToken: res.access_token, user });
      return true;
    } catch {
      session.setToken(null);
      set({ accessToken: null, user: null });
      return false;
    }
  },

  restore: async () => {
    if (get().restoreAttempted) return !!get().accessToken;
    set({ restoreAttempted: true });
    return get().refresh();
  },
}));
