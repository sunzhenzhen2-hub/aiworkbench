import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxy = env.VITE_API_PROXY || 'http://127.0.0.1:8100'
  const port = Number(env.VITE_PORT || 5181)
  const host = env.VITE_HOST || '127.0.0.1'
  const base = env.VITE_BASE || '/aiworkbench/m/'

  return {
    base,
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host,
      port,
      strictPort: true,
      proxy: {
        '/aiworkbench/api': {
          target: apiProxy,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/aiworkbench\/api/, '/api'),
        },
      },
    },
  }
})
