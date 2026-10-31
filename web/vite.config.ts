import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({ base: '/', plugins: [react()], build: { outDir: 'dist' }, server: { proxy: { '/api': { target: 'http://127.0.0.1:7420', changeOrigin: true, headers: { origin: 'http://127.0.0.1:7420' } } } }, test: { environment: 'jsdom', setupFiles: ['src/test/setup.ts'] } });
