import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, searchForWorkspaceRoot } from 'vite'

// Vite's own limit for a production chunk, and the mock build's one chunk (about 650 kB).
const DEFAULT_CHUNK_KB = 500;
const MOCK_CHUNK_KB = 800;

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const mock = loadEnv(mode, process.cwd(), 'VITE_').VITE_MOCK === '1'
  return {
    plugins: [react()],
    // A production build drops the mocks and the galleries (#220), and ships two chunks: Studio,
    // and the management site it loads on its first visit (src/management/LazyManagePage.tsx),
    // each with its stylesheet. The mock build (VITE_MOCK=1, the visual tests) inlines every
    // dynamic import, so that it ships one chunk and one stylesheet, in the order the
    // screenshots were taken with: split, the galleries' shared modules would leave for a chunk
    // of their own, and their CSS with them. That stylesheet holds the same rules as the two
    // production ones; only Studio's (`.studio`, `.st-*`) come after the management site's
    // (`.mg`) instead of before, and they style different elements. One chunk is over Vite's
    // 500 kB warning there, on purpose.
    build: {
      rolldownOptions: { output: { codeSplitting: !mock } },
      chunkSizeWarningLimit: mock ? MOCK_CHUNK_KB : DEFAULT_CHUNK_KB,
    },
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
