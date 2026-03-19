import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  resolve: {
    alias: {
      // elkjs/lib/main.js has a try/catch require('web-worker') for Node.js
      // environments. Stub it out so Rolldown does not fail to resolve it in
      // the browser build; the try block catches the stub gracefully.
      'web-worker': path.resolve(__dirname, 'src/lib/empty.ts'),
    },
  },
})
