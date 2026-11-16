import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    // 体积告警阈值：Element Plus / ECharts 单独成块后主包已明显变小
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // 稳定第三方分包：升级业务代码只换业务 chunk，浏览器可长期缓存框架产物
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
          if (id.includes('/vue') || id.includes('@vue') || id.includes('vue-router')) return 'vue'
          return 'vendor'
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
