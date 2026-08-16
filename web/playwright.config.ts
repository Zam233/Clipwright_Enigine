import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  // P0-11: 真实后端集成测试需 8080 后端就绪，移出默认套件（npm run test:e2e:integration 单独运行）
  testIgnore: ['**/integration.spec.ts'],
  fullyParallel: true,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --strictPort',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
