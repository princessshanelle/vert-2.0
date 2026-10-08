import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// VERT PWA: installable, offline-capable shell so the dashboard keeps
// working on the greenhouse LAN even with no internet uplink at all.
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'VERT — Versatile Eco-farming Robotic Technology',
        short_name: 'VERT',
        description: 'Smart greenhouse monitoring and control',
        theme_color: '#1b5e20',
        background_color: '#ffffff',
        display: 'standalone',
        icons: [
          { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}']
      }
    })
  ],
  server: {
    host: true,
    port: 5173,
    // Mirrors nginx.conf, so `npm run dev` uses the same same-origin API
    // calls as the built app. The automation entries must come first.
    proxy: {
      '/api/v1/rules': { target: 'http://localhost:8081' },
      '/api/v1/alerts': { target: 'http://localhost:8081' },
      '/api': { target: 'http://localhost:8080', ws: true, xfwd: true },
      '/vert-frames': { target: 'http://localhost:9000' }
    }
  }
})
