import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

function manualVendorChunk(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined

  if (
    id.includes('/node_modules/react/')
    || id.includes('/node_modules/react-dom/')
  ) {
    return 'react-vendor'
  }

  if (id.includes('/node_modules/@xyflow/react/')) {
    return 'xyflow-vendor'
  }

  if (id.includes('/node_modules/elkjs/')) {
    return 'elk-vendor'
  }

  if (id.includes('/node_modules/zustand/')) {
    return 'zustand-vendor'
  }

  return 'vendor'
}

// https://vite.dev/config/
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        manualChunks: manualVendorChunk,
      },
    },
  },
  resolve: {
    alias: {
      // elkjs/lib/main.js has a try/catch require('web-worker') for Node.js
      // environments. Stub it out so Rolldown does not fail to resolve it in
      // the browser build; the try block catches the stub gracefully.
      'web-worker': path.resolve(__dirname, 'src/lib/empty.ts'),
    },
  },
})
