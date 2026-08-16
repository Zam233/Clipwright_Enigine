import { useEffect, useState } from 'react';
import { healthApi } from '@/services/api';
import { useSettingsStore } from '@/stores/settingsStore';

export type BackendStatus = 'checking' | 'online' | 'offline';

// G5: 心跳轮询间隔（毫秒）——后端中途挂掉时前端能及时反映离线
const POLL_INTERVAL_MS = 30_000;

/**
 * 探测后端连接状态：挂载时立即检查一次，随后每 30s 轮询一次（G5），
 * 让运行中后端离线也能被及时反映；apiBaseUrl 变化时重新探测。
 * 卸载时清除定时器（停止轮询）。
 */
export function useBackendHealth(): BackendStatus {
  const [backend, setBackend] = useState<BackendStatus>('checking');
  const apiBaseUrl = useSettingsStore((s) => s.apiBaseUrl);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    const check = () => {
      healthApi.check()
        .then(() => alive && setBackend('online'))
        .catch(() => alive && setBackend('offline'));
    };

    check(); // 立即检查一次
    timer = setInterval(check, POLL_INTERVAL_MS);

    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [apiBaseUrl]);

  return backend;
}
