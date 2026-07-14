import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8787' },
    // ponytail: ignore runtime exports/uploads — Windows EBUSY when ffmpeg locks .mp4
    watch: { ignored: ['**/server/data/**'] },
  },
})
