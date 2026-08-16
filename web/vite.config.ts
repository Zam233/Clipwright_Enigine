import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 后端实际端口：J:\Clipwright\.env 中 CLIPWRIGHT_PORT=8080
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      // P3-3B: 账号/市场服务（ClipWright Server，K:\Clipwright Server :8090）
      '/srv': {
        target: 'http://localhost:8090',
        changeOrigin: true,
      },
    },
  },
  // W18: 包体优化 — vendor 拆分 + 大依赖独立 chunk（长缓存 + 首屏并行加载）
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          'tanstack': ['@tanstack/react-router', '@tanstack/react-virtual', '@tanstack/react-query'],
          'lucide': ['lucide-react'],
          'zustand': ['zustand'],
          'radix': ['@radix-ui/react-tooltip', '@radix-ui/react-slider', '@radix-ui/react-select',
            '@radix-ui/react-dialog', '@radix-ui/react-dropdown-menu', '@radix-ui/react-popover'],
        },
      },
    },
  },
});
