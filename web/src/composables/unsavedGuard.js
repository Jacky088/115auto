/** 未保存更改的离开确认：路由切换弹窗 + 浏览器刷新/关闭原生确认。
 *
 * 用法（任意页面组件内）：
 *   const dirty = ref(false)
 *   useUnsavedGuard(dirty)          // 传入 dirty ref，或 getter 函数
 *
 * 弹窗交互：
 *   - 确定：不保存直接离开（回退该页未保存的更改——离开即丢弃）
 *   - 取消：留在当前页面
 */
import { nextTick, onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { confirmAction } from './confirmDialog'

export function useUnsavedGuard(dirty, onDiscard = null) {
  const isDirty = () => (typeof dirty === 'function' ? dirty() : !!dirty.value)
  let confirmed = false  // 已确认离开的标记（本次导航放行）

  onBeforeRouteLeave(async (_to, _from) => {
    if (confirmed || !isDirty()) return true
    try {
      const ok = await confirmAction({
        title: '离开当前页面？',
        message: '未保存的修改将丢失。',
        confirmText: '离开并丢弃',
        cancelText: '留在本页',
        type: 'warning',
        danger: true,
      })
      if (!ok) return false
      confirmed = true   // 用户确认：放行导航（更改即被丢弃）
      if (typeof dirty === 'object' && dirty !== null && 'value' in dirty) {
        dirty.value = false
      }
      if (typeof onDiscard === 'function') {
        try { onDiscard() } catch { /* ignore */ }
      }
      return true
    } finally {
      // 放行标记一次性消费：若全局守卫随后取消了本次导航（如向导重查重定向 /setup），
      // 组件仍挂载且 dirty 未变，此时必须重新询问，避免此后所有离开被静默放行
      nextTick(() => { confirmed = false })
    }
  })

  // 浏览器刷新 / 关闭标签页：原生确认框
  const beforeunload = (e) => {
    if (!isDirty()) return
    e.preventDefault()
    e.returnValue = ''
  }
  onMounted(() => window.addEventListener('beforeunload', beforeunload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeunload))
}
