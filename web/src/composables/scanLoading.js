import { ElLoading } from 'element-plus'

// 扫描冻结遮罩（全局单例）：转存分流与整理刮削共用，保证一致的文案、配色与可读性。
// 深底白字 + 白色 spinner，避免主题紫色文字压在浅色毛玻璃上不可读。
// 遮罩期间锁定页面交互：焦点移出输入框、捕获阶段吞掉除 Esc 外的按键、禁止滚轮滚动
// （Esc 放行，供预览任务的「按 Esc 取消扫描」使用）。
let inst = null
let guard = null

export const SCAN_LOADING_TEXT = '正在扫描文件，请勿进行其他操作……（按 Esc 可取消扫描）'

function installGuard() {
  if (guard) return
  const keys = (e) => {
    if (e.key === 'Escape') return
    e.preventDefault()
    e.stopPropagation()
  }
  const scroll = (e) => {
    e.preventDefault()
  }
  guard = { keys, scroll }
  // 捕获阶段拦截，先于业务组件的冒泡监听执行，避免扫描期间误触其他操作
  window.addEventListener('keydown', keys, true)
  window.addEventListener('wheel', scroll, { capture: true, passive: false })
  window.addEventListener('touchmove', scroll, { capture: true, passive: false })
}

function removeGuard() {
  if (!guard) return
  window.removeEventListener('keydown', guard.keys, true)
  window.removeEventListener('wheel', guard.scroll, true)
  window.removeEventListener('touchmove', guard.scroll, true)
  guard = null
}

export function showScanLoading(text = SCAN_LOADING_TEXT) {
  closeScanLoading()
  try {
    document.activeElement?.blur?.()
  } catch {
    // 忽略：极端情况下 blur 不可用不影响遮罩
  }
  inst = ElLoading.service({
    lock: true,
    text,
    background: 'rgba(15, 18, 34, 0.72)',
    customClass: 'scan-loading-mask',
  })
  installGuard()
  return inst
}

export function closeScanLoading() {
  removeGuard()
  if (inst) {
    inst.close()
    inst = null
  }
}
