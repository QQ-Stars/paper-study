import react from '@vitejs/plugin-react';
import { configDefaults, defineConfig } from 'vitest/config';

const backendTarget = 'http://localhost:5173';

export default defineConfig({
  base: '/workspace/',
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': backendTarget,
      '/papers': backendTarget,
      '/pdfbytes': backendTarget,
    },
  },
  test: {
    environment: 'jsdom',
    exclude: [...configDefaults.exclude, 'e2e/**'],
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
