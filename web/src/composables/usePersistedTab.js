import { ref, watch } from 'vue'

/**
 * 持久化当前页面的激活 Tab 到 localStorage，刷新后自动恢复。
 * 若路由带有合法的 ?tab=xxx 查询参数，则优先直达该 Tab。
 */
export function usePersistedTab(storageKey, allowedTabs, defaultTab, route = null) {
  const fullKey = `tab:${storageKey}`
  const isAllowed = (v) => typeof v === 'string' && allowedTabs.includes(v)

  let initial = defaultTab
  try {
    const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(fullKey) : null
    if (isAllowed(saved)) initial = saved
  } catch {
    // 隐私模式或禁用存储时静默回退到默认 Tab
  }

  const queryTab = String(route?.query?.tab || '').toLowerCase()
  if (isAllowed(queryTab)) {
    initial = queryTab
  }

  const activeTab = ref(initial)

  watch(
    activeTab,
    (val) => {
      if (!isAllowed(val)) return
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(fullKey, val)
        }
      } catch {
        // 忽略存储写入异常
      }
    },
    { immediate: true },
  )

  if (route) {
    watch(
      () => route.query?.tab,
      (q) => {
        const next = String(q || '').toLowerCase()
        if (isAllowed(next)) {
          activeTab.value = next
        }
      },
    )
  }

  return activeTab
}
