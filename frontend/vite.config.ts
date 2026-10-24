import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { execSync } from 'child_process';

let buildCommit = 'unknown';
try {
  buildCommit = execSync('git rev-parse --short HEAD').toString().trim() || 'unknown';
} catch {
  buildCommit = process.env.VITE_GIT_COMMIT || 'unknown';
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __BUILD_COMMIT__: JSON.stringify(buildCommit),
  },
  server: {
    port: 5174,
    host: '127.0.0.1',
    proxy: {
      // REST API calls go through the local cache proxy (port 8009)
      // which serves cached responses and only hits the VM on cache miss.
      '/api': {
        target: 'http://127.0.0.1:8009',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', () => {});
        },
      },
      // WebSocket bypasses the cache and connects directly via SSH tunnel (port 8008).
      // Live tick data cannot be cached.
      '/ws': {
        target: 'ws://127.0.0.1:8008',
        ws: true,
        configure: (proxy) => {
          proxy.on('error', () => {});
        },
      },
    },
  },
});
