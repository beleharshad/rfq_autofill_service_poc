/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    // treat CSS imports as no-ops in tests
    css: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 0,         // No proxy-level timeout — let backend decide
        configure: (proxy) => {
          // Disable response buffering so SSE (llm-stream) chunks flow immediately
          proxy.on('proxyRes', (_proxyRes, _req, res) => {
            res.setHeader('x-accel-buffering', 'no');
          });
          proxy.on('error', (err) => console.error('[API proxy]', err));
        },
      },
    },
  },
})








