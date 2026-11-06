/* 极简 Service Worker：network-first，同源 GET 静态资源失败时回退缓存。
   不拦截 /api/ 与 /ws 请求（实时数据必须直连），不引入预缓存清单。 */
const CACHE_NAME = 'autosave115-static-v1'

self.addEventListener('install', () => {
  self.skipWaiting()
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
  // 仅处理同源静态资源；API/WebSocket 直连不走缓存
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return

  event.respondWith(
    fetch(req).then((res) => {
      // 仅缓存成功的基础响应，避免把错误页/透明响应写进缓存
      if (res && res.ok && res.type === 'basic') {
        const copy = res.clone()
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy))
      }
      return res
    }).catch(() =>
      caches.match(req).then((cached) => cached || Response.error())
    )
  )
})
