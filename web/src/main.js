import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
// 深色模式变量（配合 html.dark 切换，切换入口在 Layout 顶栏）
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as Icons from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './style.css'

// 深色模式偏好：挂载前应用，避免闪烁（默认浅色）
if (localStorage.getItem('theme') === 'dark') document.documentElement.classList.add('dark')

const app = createApp(App)
for (const [name, comp] of Object.entries(Icons)) app.component(name, comp)
app.use(router).use(ElementPlus, { locale: zhCn }).mount('#app')

// PWA Service Worker：仅生产环境注册，离线时静态资源走缓存 fallback
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}
