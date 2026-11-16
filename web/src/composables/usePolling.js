import { onUnmounted } from 'vue'

/**
 * 后台轮询定时器：页面不可见（切换标签/最小化）时自动跳过本轮请求，重新可见立即补一次。
 *
 * 之前各页面直接用 setInterval：浏览器标签页在后台仍按 3~10 秒频率打接口，
 * NAS 上多标签/多设备时是明显的无谓负载。这里统一成「可见才轮询」，
 * 并在上一轮还没返回时跳过，避免慢接口把请求堆起来。
 *
 * 返回 { start, stop, tick }：start/stop 由调用方在 mounted/unmounted 或状态变化时控制；
 * 组件卸载时自动 stop，不会残留定时器。
 */
export function usePolling(task, intervalMs) {
  let timer = null
  let running = false

  const tick = async () => {
    if (document.hidden || running) return
    running = true
    try {
      await task()
    } catch (e) {
      // 各调用方内部已处理业务错误；这里只兜住未捕获异常，避免 Promise 拒绝冒泡
      console.debug('[polling]', e)
    } finally {
      running = false
    }
  }

  const onVisible = () => {
    if (!document.hidden) tick()
  }

  const start = () => {
    if (timer) return
    timer = setInterval(tick, intervalMs)
    document.addEventListener('visibilitychange', onVisible)
  }

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    document.removeEventListener('visibilitychange', onVisible)
  }

  onUnmounted(stop)
  return { start, stop, tick }
}
