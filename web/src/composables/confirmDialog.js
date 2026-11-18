import { ElMessageBox } from 'element-plus'

/**
 * 全站统一二次确认弹窗（Aurora Glass 居中玻璃弹窗 + 标题栏 + 红色圆形关闭按钮 + 磨砂遮罩）。
 * 所有删除 / 撤销 / 重置 / 清理 / 放行 / 重试等二次确认统一走此入口，保持视觉与交互完全一致。
 */
export async function confirmAction({
  title,
  message = '',
  confirmText = '确认',
  cancelText = '取消',
  type = 'warning',
  danger = false,
}) {
  try {
    await ElMessageBox.confirm(message || title, message ? title : '确认操作', {
      type,
      confirmButtonText: confirmText,
      cancelButtonText: cancelText,
      ...(danger ? { confirmButtonClass: 'danger-confirm-btn' } : {}),
    })
    return true
  } catch {
    return false
  }
}
