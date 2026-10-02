import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // 0.0.0.0 so the container's port mapping reaches it.
    host: true,
    port: 5173,
    strictPort: true,
  },
  build: { outDir: 'dist', sourcemap: true },
});
