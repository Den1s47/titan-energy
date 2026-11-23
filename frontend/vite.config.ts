import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Конфигурация Vite (специф §3: React + TypeScript + Vite).
// proxy: в dev-режиме запросы /api идут на FastAPI (порт 8000),
// чтобы не мучиться с CORS; в проде статика отдаётся самим FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
