import { ElMessageBox } from 'element-plus'

// 破坏性/危险操作动作词规则：删除、清空、清除、丢弃、废弃、移除、重置、覆盖、停止、中断、抹除、卸载
const DANGER_ACTION_RE = /删除|清空|清除|丢弃|废弃|移除|重置|覆盖|停止|中断|抹除|卸载/

/**
 * 全站统一二次确认弹窗（居中玻璃弹窗 + 标题栏 + 红色圆形关闭按钮 + 磨砂遮罩）。
 *
 * 【弹窗删除/危险操作统一规范】：
 * 1. 凡涉及「删除、清空、丢弃、移除、重置、停止」等破坏性或不可逆操作，确认按钮统一使用红色 danger 样式；
 * 2. 无论调用方是否显式传 danger=true，只要 type='danger' 或按钮/标题文本包含破坏性关键词，系统均自动赋予红色危险确认按钮；
 * 3. 弹窗状态图标统一采用 warning / danger 告警色，确保全站所有模块删除弹窗样式视觉一致。
 */
export async function confirmAction({
  title,
  message = '',
  confirmText = '确认',
  cancelText = '取消',
  type = 'warning',
  danger = undefined,
}) {
  const isDanger = danger !== undefined
    ? Boolean(danger)
    : (type === 'danger' || DANGER_ACTION_RE.test(confirmText) || DANGER_ACTION_RE.test(title))

  const dialogType = isDanger && type !== 'info' ? (type === 'danger' ? 'danger' : 'warning') : type

  try {
    await ElMessageBox.confirm(message || title, message ? title : '确认操作', {
      type: dialogType,
      confirmButtonText: confirmText,
      cancelButtonText: cancelText,
      confirmButtonType: isDanger ? 'danger' : 'primary',
      confirmButtonClass: isDanger ? 'el-button--danger danger-confirm-btn' : '',
    })
    return true
  } catch {
    return false
  }
}
