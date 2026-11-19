import { createApp } from 'vue'
import 'element-plus/dist/index.css'
// 深色模式变量（配合 html.dark 切换，切换入口在 Layout 顶栏）
import 'element-plus/theme-chalk/dark/css-vars.css'
import ElementPlus from './element'
import icons from './icons'
import App from './App.vue'
import router from './router'
import './style.css'

// 深色模式偏好：挂载前应用，避免闪烁（默认浅色）
if (localStorage.getItem('theme') === 'dark') {
  document.documentElement.classList.add('dark')
  try { window.AndroidBridge?.setDarkMode(true) } catch { /* ignore */ }
} else {
  try { window.AndroidBridge?.setDarkMode(false) } catch { /* ignore */ }
}

const app = createApp(App)
for (const [name, comp] of Object.entries(icons)) app.component(name, comp)
app.use(router).use(ElementPlus).mount('#app')

// PWA Service Worker：仅生产环境注册，离线时静态资源走缓存 fallback，支持版本更新
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      reg.addEventListener('updatefound', () => {
        const newWorker = reg.installing
        if (newWorker) {
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              console.log('[PWA] 新版本资源已准备就绪')
            }
          })
        }
      })
    }).catch(() => {})
  })
}
