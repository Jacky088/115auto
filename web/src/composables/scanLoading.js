import { ElLoading } from 'element-plus'

// 扫描冻结遮罩（全局单例）：转存分流与整理刮削共用，保证一致的文案、配色与可读性。
// 深底白字 + 白色 spinner，避免主题紫色文字压在浅色毛玻璃上不可读。
let inst = null

export function showScanLoading(text = '正在扫描文件，请稍后……') {
  closeScanLoading()
  inst = ElLoading.service({
    lock: true,
    text,
    background: 'rgba(15, 18, 34, 0.72)',
    customClass: 'scan-loading-mask',
  })
  return inst
}

export function closeScanLoading() {
  if (inst) {
    inst.close()
    inst = null
  }
}
