import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useAuthStore } from '@/stores/authStore';
import { StandardLayout } from '@/layouts/StandardLayout';

/**
 * LoginPage — P3-3B 账号登录页。
 * 登录/注册走 ClipWright Server（/srv 代理）；refresh token 存 httpOnly cookie，
 * access token 仅存内存。登录成功跳转首页。
 */
export function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!email.trim() || password.length < 8) {
      setError('请输入有效邮箱；密码至少 8 位');
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (mode === 'login') {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, displayName.trim());
      }
      navigate({ to: '/' });
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : mode === 'login' ? '登录失败：请检查邮箱与密码' : '注册失败：请检查输入');
    } finally {
      setBusy(false);
    }
  };

  return (
    <StandardLayout title="登录">
      <div className="max-w-sm mx-auto mt-10 space-y-4">
        <div className="flex rounded-cw-md overflow-hidden border border-outline-variant/40">
          {(['login', 'register'] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(''); }}
              className={`flex-1 py-2 text-body-sm font-medium transition-colors cursor-pointer ${
                mode === m ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {m === 'login' ? '登录' : '注册'}
            </button>
          ))}
        </div>

        {mode === 'register' && (
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="昵称（可选）"
            className="w-full px-3 py-2 rounded-cw-md bg-surface-container text-body-sm"
            autoComplete="nickname"
          />
        )}
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="邮箱"
          type="email"
          autoComplete="email"
          className="w-full px-3 py-2 rounded-cw-md bg-surface-container text-body-sm"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); }}
          placeholder="密码（至少 8 位）"
          type="password"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          className="w-full px-3 py-2 rounded-cw-md bg-surface-container text-body-sm"
        />

        {error && <p className="text-caption text-error">{error}</p>}

        <button
          onClick={() => void submit()}
          disabled={busy}
          className="w-full py-2.5 rounded-cw-md bg-primary text-on-primary font-medium disabled:opacity-60 cursor-pointer"
        >
          {busy ? '请稍候…' : mode === 'login' ? '登录' : '创建账号'}
        </button>

        <p className="text-caption text-on-surface-variant text-center">
          账号服务由 ClipWright Server（:8090）提供；未部署 Server 时本页不可用。
        </p>
      </div>
    </StandardLayout>
  );
}
