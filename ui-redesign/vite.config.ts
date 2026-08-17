import react from '@vitejs/plugin-react';
import type { ProxyOptions } from 'vite';
import { defineConfig } from 'vite';

const backendTarget = 'http://127.0.0.1:5173';

/* 后端 LocalAccessMiddleware 要求 Host 为自身绑定点，且 POST 等写请求的
 * Origin 与 Host 一致；原型跑在 5180，需在代理层把两者都重写为后端地址。 */
const proxyEntry: ProxyOptions = {
  target: backendTarget,
  configure: (proxy) => {
    proxy.on('proxyReq', (proxyReq) => {
      proxyReq.setHeader('host', '127.0.0.1:5173');
      if (proxyReq.getHeader('origin')) {
        proxyReq.setHeader('origin', backendTarget);
      }
    });
  },
};

export default defineConfig(({ command }) => ({
  /* 生产构建托管在后端 /workspace/ 路由下（frontend_assets 静态适配器），
   * 资源引用需以 /workspace/ 为基址；dev 模式（5180）仍用根路径。 */
  base: command === 'build' ? '/workspace/' : '/',
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      '/api': proxyEntry,
      '/pdfbytes': proxyEntry,
      '/papers': proxyEntry,
      '/health': proxyEntry,
    },
  },
  optimizeDeps: {
    force: true,
  },
}));
