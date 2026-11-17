import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理目标来自 VITE_API_TARGET（默认 profile 的 dashboard 稳定端口 8580）。
// 不写死旧固定端口：端口由 launcher 按 profile 哈希派生，生产走同源托管。
// VITE_API_MOCK=1 时前端完全离线，代理用不上，前后端可各自独立验证。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_')
  const target = env.VITE_API_TARGET || 'http://127.0.0.1:8580'
  const proxy = Object.fromEntries(
    ['/api', '/health', '/auth', '/v1'].map((prefix) => [
      prefix,
      { target, changeOrigin: true },
    ]),
  )

  return {
    plugins: [vue()],
    server: {
      port: 5173,
      proxy,
    },
    build: {
      outDir: 'dist',
    },
  }
})
