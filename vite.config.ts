import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': resolve(__dirname, 'src') } },
  server: {
    // Windows: mặc định đôi khi chỉ ::1 → 127.0.0.1 fail → @vite/client hủy env.mjs
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8787' },
    // ponytail: đừng watch data/venv/backup — Windows handle phình → Vite listen nhưng treo HTML
    watch: {
      ignored: [
        '**/server/data/**',
        '**/server/.venv/**',
        '**/server/.venv-*/**',
        '**/.git/**',
        '**/*.bk',
        '**/*.bk[0-9]',
        '**/*.mp4',
        '**/*.wav',
      ],
    },
  },
})
