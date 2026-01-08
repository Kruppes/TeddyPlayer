import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8754',
      '/library': 'http://localhost:8754',
      '/streams': 'http://localhost:8754',
      '/devices': 'http://localhost:8754',
      '/playback': 'http://localhost:8754',
      '/readers': 'http://localhost:8754',
      '/settings': 'http://localhost:8754',
      '/preferences': 'http://localhost:8754',
      '/health': 'http://localhost:8754',
      '/cache': 'http://localhost:8754',
      '/uploads': 'http://localhost:8754',
      '/proxy': 'http://localhost:8754',
      '/transcode': 'http://localhost:8754',
      '/tracks': 'http://localhost:8754',
      '/version': 'http://localhost:8754',
    },
  },
})
