import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],

  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },

  build: {
    // Target modern browsers for smaller output
    target: 'es2020',

    // Source maps only in dev
    sourcemap: mode === 'development',

    // Code splitting: separate vendor chunks for better caching
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
          'router': ['react-router-dom'],
        },
      },
    },

    // Chunk size warning at 500kB
    chunkSizeWarningLimit: 500,

    // CSS code splitting for parallel loading
    cssCodeSplit: true,

    // Minification
    minify: 'esbuild',
  },
}))
