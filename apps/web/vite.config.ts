import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Приложение живёт на `/`: FastAPI отдаёт `dist/index.html` на `/` и `/лента`,
// ассеты — на `/assets`, старая лента переехала на `/старая` (REFACTOR.md,
// срез 1c, после приёмки 2026-09-08).
export default defineConfig({
  plugins: [react()],
  base: '/',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    // В разработке страницу отдаёт Vite, а API — uvicorn на 8765. Один origin
    // для браузера, поэтому CORS не нужен ни там, ни там.
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/вложение': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
