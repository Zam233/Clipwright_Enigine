import { useEffect } from 'react';
import { RouterProvider } from '@tanstack/react-router';
import { Providers } from './providers';
import { router } from './router';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/toast';
import { useToastStore } from '@/stores/toastStore';
import { useAuthStore } from '@/stores/authStore';

export function App() {
  // P3-3B: 应用挂载时尝试用 httpOnly cookie 恢复会话（Server 未部署时静默失败，不影响离线/令牌模式）
  useEffect(() => {
    void useAuthStore.getState().restore();
  }, []);

  // P0-10/P3-3B: cw:unauthorized 全局监听——先尝试 refresh 续期，失败提示并跳转登录
  useEffect(() => {
    const onUnauthorized = () => {
      void (async () => {
        const ok = await useAuthStore.getState().refresh();
        if (ok) {
          useToastStore.getState().show('会话已自动续期', 'success');
        } else {
          useToastStore.getState().show('登录已失效：请重新登录（如未部署账号服务，请在设置中配置 API 令牌）', 'error');
          if (window.location.pathname !== '/login') {
            void router.navigate({ to: '/login' });
          }
        }
      })();
    };
    window.addEventListener('cw:unauthorized', onUnauthorized);
    return () => window.removeEventListener('cw:unauthorized', onUnauthorized);
  }, []);

  return (
    <ErrorBoundary>
      <Providers>
        <RouterProvider router={router} />
        <Toaster />
      </Providers>
    </ErrorBoundary>
  );
}
