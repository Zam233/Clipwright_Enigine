import { useEffect } from 'react';
import { RouterProvider } from '@tanstack/react-router';
import { Providers } from './providers';
import { router } from './router';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/toast';
import { useToastStore } from '@/stores/toastStore';

export function App() {
  // P0-10: cw:unauthorized 全局监听——登录失效时给用户可见提示（此前事件派发后无人消费）
  useEffect(() => {
    const onUnauthorized = () => {
      useToastStore.getState().show('登录已失效：请在设置中重新配置 API 令牌', 'error');
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
