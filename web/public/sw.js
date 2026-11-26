/* 115 自动转存助手 · 高性能 PWA Service Worker (v2)
 *
 * 核心策略：
 * 1. 静态哈希资产 (/assets/*)：Cache-First（缓存优先），0 延迟毫秒级秒开；
 * 2. 页面骨架 (/ 及 /index.html)：Network-First，优先拉取最新版本，离线/弱网自动回退本地缓存；
 * 3. 图标与清单：Stale-While-Revalidate，优先返回本地并静默更新；
 * 4. API 接口 (/api/*) 与 WebSocket (/ws)：绝不拦截缓存，保持 100% 实时直连。
 */

const CACHE_NAME = 'autosave115-pwa-v3'
const PRECACHE_ASSETS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-maskable-512.png',
  '/apple-touch-icon.png'
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('[SW] Precache partial fallback:', err)
      })
    }).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return

  const url = new URL(req.url)

  // 1. 跨域请求直接放行
  if (url.origin !== self.location.origin) return

  // 2. API 与 WebSocket 严格放行，绝不缓存
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return

  // 3. 带哈希的静态资源 (/assets/*)：Cache-First
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached
        return fetch(req).then((res) => {
          if (res && res.ok && res.type === 'basic') {
            const copy = res.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copy))
          }
          return res
        })
      })
    )
    return
  }

  // 4. HTML 导航与入口页面：Network-First，离线回退缓存
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copy))
        }
        return res
      }).catch(() => {
        return caches.match(req).then((cached) => {
          if (cached) return cached
          return caches.match('/').then((rootHit) => rootHit || caches.match('/index.html'))
        })
      })
    )
    return
  }

  // 5. 其余同源静态资源（图标、清单等）：Stale-While-Revalidate
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetchPromise = fetch(req).then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copy))
        }
        return res
      }).catch(() => cached)

      return cached || fetchPromise
    })
  )
})
