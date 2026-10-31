import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The management mock reads the demo fixture the seed also loads (#131).
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), '../tests/fixtures/demo'] },
    // The platform API serves /chat and /events; /api avoids CORS in dev.
    proxy: {
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // Forward a stream cut by the API to the browser instead of hanging.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, _req, res) => {
            proxyRes.on('close', () => {
              if (!proxyRes.complete) res.destroy()
            })
          })
        },
      },
    },
  },
})
