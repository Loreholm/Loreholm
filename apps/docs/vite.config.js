import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  base: '/docs/',
  plugins: [react()],
  server: {
    fs: {allow: [path.resolve(import.meta.dirname, '../..')]},
  },
  build: {
    outDir: '../../web/docs',
    emptyOutDir: true,
    sourcemap: true,
  },
});
