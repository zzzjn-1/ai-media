import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// M4 前端构建配置
// - dev：Vite 开发服务器（5173），将 /api 反向代理到后端 8000（无需依赖 CORS）
// - build：产物输出 dist/，可由 Nginx 静态托管，并反向代理 /api 到后端
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // antd-vendor 为多页面共用组件的并集（AntD 全量引入的固有体积，约 838KB），
    // 属刻意设计的共享 chunk：入口 chunk 已降至 29KB，首屏不再承受该体积。
    // 故将默认 500KB 警告阈值上调至 900KB，避免每次构建刷无关噪音。
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // T5.2 vendor 拆包：react/router 与 antd/icons 各自独立 chunk。
        // 路由级懒加载已把页面拆开，但 antd 被多个页面共用、体积大，
        // 单独成 chunk 可避免它随业务代码频繁失效，也进一步压低入口 chunk。
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'antd-vendor': ['antd', '@ant-design/icons'],
        },
      },
    },
  },
})
