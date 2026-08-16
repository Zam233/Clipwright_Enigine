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
    },
  },
});
