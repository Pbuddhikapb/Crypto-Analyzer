import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8008',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', () => {});
        },
      },
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
