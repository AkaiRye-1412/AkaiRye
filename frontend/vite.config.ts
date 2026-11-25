import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：/api 与 /ws 代理到 FastAPI（127.0.0.1:8686）
// 生产模式：构建产物由 FastAPI 静态托管（02 文档 3.2 同源部署）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8686', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8686', ws: true },
    },
  },
})
