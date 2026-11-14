/// <reference types="vitest" />
import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

// The API runs on its own port in development (never the research service's 8770).
const API = process.env.SKY_API ?? 'http://127.0.0.1:8780';

export default defineConfig({
  plugins: [preact()],
  server: { port: 5173, proxy: { '/api': API } },
  build: { outDir: 'dist', sourcemap: true },
  test: { include: ['tests/unit/**/*.test.ts'], environment: 'node' },
});
