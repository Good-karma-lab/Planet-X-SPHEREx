import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
// QA only (untracked): /api/v1/searched does not exist yet, so a few fields stand in for it (harness data, not shipped)
const FIELDS = [
  { key: 'a', ra: 269.45, dec: 4.69, fov: 20, state: 'done', kinds: ['passes', 'film'], found: 2, best: { kind: 'track', label: '2 tracks' }, searched_at: '2026-11-14T10:00:00Z', open: { ra: 269.45, dec: 4.69, fov: 20 } },
  { key: 'b', ra: 133.76, dec: -7.24, fov: 5, state: 'done', kinds: ['passes'], found: 0, nothing: true, open: { ra: 133.76, dec: -7.24, fov: 5 } },
  { key: 'c', ra: 27.378, dec: -0.12, fov: 18, state: 'running', kinds: ['distant'], found: 0, open: { ra: 27.378, dec: -0.12, fov: 18 } },
];
export default defineConfig({
  plugins: [preact(), { name: 'mock-searched', configureServer(s) {
    s.middlewares.use('/api/v1/searched', (_req, res) => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify({ count: FIELDS.length, fields: FIELDS })); });
    s.middlewares.use('/api/v1/health', async (_req, res) => { const h = await (await fetch('https://spherex.space/api/v1/health')).json(); h.product_versions.searched = 'x'; res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(h)); });
  } }],
  server: { port: 5197, proxy: { '/api': { target: 'https://spherex.space', changeOrigin: true, secure: true } } },
});
