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
import { onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'

export function useUnsavedGuard(dirty) {
  const isDirty = () => (typeof dirty === 'function' ? dirty() : !!dirty.value)
  let confirmed = false  // 已确认离开的标记（本次导航放行）

  onBeforeRouteLeave(async (_to, _from) => {
    if (confirmed || !isDirty()) return true
    try {
      await ElMessageBox.confirm(
        '当前页面有未保存的更改，离开后将丢失这些更改。',
        '未保存的更改',
        { confirmButtonText: '不保存，直接离开', cancelButtonText: '留在本页', type: 'warning' },
      )
      confirmed = true   // 用户确认：放行导航（更改即被丢弃）
      return true
    } catch {
      return false       // 用户取消：留在本页
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
