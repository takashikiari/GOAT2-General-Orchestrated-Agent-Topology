import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-only: lets `npm run dev` reach the real local backend (started
    // separately, e.g. by the live bot process on 127.0.0.1:8765) without
    // CORS setup. Has zero effect on `vite build` / the production bundle.
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
})
