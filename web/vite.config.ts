/// <reference types="vitest" />
import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

// The API runs on its own port in development (8780; 8770 is never used).
const API = process.env.SKY_API ?? 'http://127.0.0.1:8780';
// The baked tile layers (/tiles: the star map, SPHEREx, the surveys) are static files of the deployed site, not of the API. A local
// run reads them from a site that has them: SKY_TILES=https://www.spherex.space (read-only GETs of public files). Unset, /tiles is
// not proxied and the globe shows only its live layers.
const TILES = process.env.SKY_TILES;

export default defineConfig({
  plugins: [preact()],
  server: {
    port: 5173,
    // '^/s/': the share pages (server pages with their Open Graph tags, /s/<kind>/<ref>) come from the API as well. A RegExp key:
    // the plain prefix '/s' would also send /src/… (the app's own modules) to the API
    proxy: { '/api': API, '^/s/': API, ...(TILES ? { '/tiles': { target: TILES, changeOrigin: true } } : {}) },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: { include: ['tests/unit/**/*.test.ts'], environment: 'node' },
});
