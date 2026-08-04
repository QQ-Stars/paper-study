import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { configDefaults, defineConfig } from 'vitest/config';

import { cleanRoomGuard } from './build/cleanRoomGuard.ts';

const backendTarget = 'http://localhost:5173';
const repositoryRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  base: '/workspace/',
  build: {
    manifest: true,
  },
  plugins: [cleanRoomGuard({ repositoryRoot }), react()],
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
