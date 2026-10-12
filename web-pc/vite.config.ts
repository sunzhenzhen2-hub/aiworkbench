import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxy = env.VITE_API_PROXY || 'http://127.0.0.1:8100'
  const port = Number(env.VITE_PORT || 5180)
  const host = env.VITE_HOST || '127.0.0.1'
  const base = env.VITE_BASE || '/aiworkbench/'

  return {
    base,
    plugins: [vue()],
    test: { environment: 'jsdom' },
    server: {
      host,
      port,
      strictPort: true,
      proxy: {
        '/aiworkbench/api': { target: apiProxy, changeOrigin: true, rewrite: (p) => p.replace(/^\/aiworkbench\/api/, '/api') },
        '/api': { target: apiProxy, changeOrigin: true },
        '/ws': { target: apiProxy.replace('http', 'ws'), ws: true },
      },
    },
    preview: {
      host,
      port,
      strictPort: true,
      proxy: {
        '/aiworkbench/api': { target: apiProxy, changeOrigin: true, rewrite: (p) => p.replace(/^\/aiworkbench\/api/, '/api') },
      },
    },
  }
})
