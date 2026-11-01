import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, searchForWorkspaceRoot } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const mock = loadEnv(mode, process.cwd(), 'VITE_').VITE_MOCK === '1'
  return {
    plugins: [react()],
    // A production build drops the mocks and the galleries, the app's only dynamic imports
    // (#220), and ships one chunk. The mock build (VITE_MOCK=1, the visual tests) inlines them,
    // so that it ships one chunk too, with the production stylesheet in its order: split, the
    // galleries' shared modules would leave for a chunk of their own, and their CSS with them.
    build: { rolldownOptions: { output: { codeSplitting: !mock } } },
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
  }
})
