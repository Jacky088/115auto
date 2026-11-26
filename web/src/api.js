import axios from 'axios'
import { ElMessage } from 'element-plus'

export const api = axios.create({ timeout: 30000, withCredentials: true })

/**
 * 长耗时只读扫描（自动整理预览 / 目录整理 Dry-Run）的统一超时。
 * 这类请求要逐目录遍历 115（每目录至少间隔 0.25s），30 秒必然超时；
 * 15 分钟可覆盖约 3000+ 个目录，日常待整理目录远小于该规模。
 * 所有扫描预览都应复用此常量，改一处即全局生效。
 */
export const LONG_SCAN_TIMEOUT = 900000

// 登录态走 HttpOnly Cookie（sa_auth），token 不落 localStorage（脚本不可读）。
// withCredentials 保证同源请求携带 Cookie；401 时清理本地残留并跳登录页。
api.interceptors.request.use((cfg) => cfg)

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && !location.hash.startsWith('#/login')) {
      clearLoggedIn()
      location.hash = '#/login'
    }
    return Promise.reject(err)
  },
)

/** 设置接口的密钥掩码值：GET 返回此哨兵表示「已配置」，PUT 收到即保持原值不变 */
export const SECRET_MASK = '__SAVED__'

/**
 * 登录会话标记：仅记录「已登录过」的非敏感布尔，用于路由守卫快速拦截；
 * 真正的凭证是 HttpOnly Cookie（JS 读不到）。标记过期时由 401 拦截器兜底跳登录页。
 */
export const markLoggedIn = () => localStorage.setItem('logged_in', '1')
export const clearLoggedIn = () => localStorage.removeItem('logged_in')
export const isLoggedInFlag = () => !!localStorage.getItem('logged_in')

/** HTTP 状态码 → 中文提示（后端未返回 detail 时兜底，避免把 axios 英文抛给用户） */
const HTTP_ERR_HINT = {
  400: '请求参数有误',
  401: '登录已失效，请重新登录',
  403: '没有权限执行该操作',
  404: '请求的资源不存在',
  409: '数据冲突，请刷新后重试',
  429: '操作太频繁，请稍后重试',
  500: '服务内部异常，请查看运行日志',
  502: '服务网关异常，请稍后重试',
  503: '服务暂不可用，请稍后重试',
  504: '服务响应超时，请稍后重试',
}
const _hasCn = (s) => typeof s === 'string' && /[\u4e00-\u9fa5]/.test(s)

export const errMsg = (e, fallback = '请求失败') => {
  if (typeof e === 'string') return e
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) return '请求参数校验失败，请检查填写内容'
  if (e?.code === 'ECONNABORTED' || e?.code === 'ETIMEDOUT') return '请求超时，请检查网络后重试'
  if (!e?.response) return '无法连接服务，请确认服务已启动且网络正常'
  const hint = HTTP_ERR_HINT[e.response.status]
  if (hint) return hint
  // 自定义异常通常已是中文提示，保留；axios 英文原文不再直接展示
  return _hasCn(e?.message) ? e.message : fallback
}

export const fmtSize = (b) => {
  if (!b && b !== 0) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = Number(b)
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 2) + ' ' + units[i]
}

export const fmtTime = (ts) => {
  if (!ts) return '-'
  let d
  if (typeof ts === 'number') {
    d = new Date(ts > 1e11 ? ts : ts * 1000)
  } else if (typeof ts === 'string') {
    const num = Number(ts)
    if (!isNaN(num) && num > 0) {
      d = new Date(num > 1e11 ? num : num * 1000)
    } else {
      d = new Date(ts)
    }
  }
  if (!d || isNaN(d.getTime()) || d.getFullYear() < 2000) return '-'
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 仅展示时分秒，适合实时测试/刷新状态的时间戳证明（如 15:27:32） */
export const fmtClock = (ts) => {
  if (!ts) return ''
  let d
  if (typeof ts === 'number') {
    d = new Date(ts > 1e11 ? ts : ts * 1000)
  } else if (typeof ts === 'string') {
    const num = Number(ts)
    d = !isNaN(num) && num > 0 ? new Date(num > 1e11 ? num : num * 1000) : new Date(ts)
  }
  if (!d || isNaN(d.getTime()) || d.getFullYear() < 2000) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export const statusLabel = {
  queued: '排队中', running: '转存中', submitted: '已提交云下载', success: '成功', failed: '失败',
  blocked: '黑名单', duplicate: '重复', skipped: '已跳过',
}

export const statusTagType = (s) => ({ success: 'success', failed: 'danger', blocked: 'warning',
  queued: 'info', running: 'primary', submitted: 'warning', duplicate: 'info', skipped: 'info' }[s] || 'info')

/** 保存/生效成功统一用顶部消息（多条自动向下顺延堆叠，不合并角标） */
export const saveOk = (title = '保存成功', message = '') =>
  ElMessage({ type: 'success', message: message ? `${title}，${message}` : title,
              duration: 2500, grouping: false })

// 最近提示去重防抖（防止网络抖动或连续轮询时同一错误瞬间刷屏）
const _recentToasts = new Map()
const _canToast = (key, cooldownMs = 2500) => {
  const now = Date.now()
  const last = _recentToasts.get(key) || 0
  if (now - last < cooldownMs) return false
  _recentToasts.set(key, now)
  return true
}

/** 全站统一操作反馈门面（自动防抖、统一持续时间） */
export const toast = {
  success: (title, message = '') => saveOk(title, message),
  error: (err, fallback = '操作失败，请稍后重试') => {
    const msg = typeof err === 'string' ? err : errMsg(err, fallback)
    if (_canToast(`err:${msg}`, 2000)) {
      ElMessage({ type: 'error', message: msg, duration: 3500, grouping: false })
    }
  },
  warn: (msg) => {
    if (_canToast(`warn:${msg}`, 2000)) {
      ElMessage({ type: 'warning', message: msg, duration: 3000, grouping: false })
    }
  },
  info: (msg) => {
    ElMessage({ type: 'info', message: msg, duration: 2500, grouping: false })
  }
}

// 转义 & < > 与引号：当前调用点均为元素文本上下文，补引号转义作为纵深防御，
// 防止该函数的输出未来被用于 HTML 属性上下文（title="..." 等）时被闭合注入
const _escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

// 115 分享 / 115:// / ed2k / magnet / 普通网页链接
// 与后端 extractor 同口径的左边界：防止 xx115.com 这类无关域名从中间截取高亮
const _LINK_RE = new RegExp(
  [
    '(?<![0-9A-Za-z])(?:https?://)?(?:www\\.)?(?:115\\.com|115cdn\\.com|anxia\\.com)/s/[0-9A-Za-z]+(?:\\?password=[0-9A-Za-z]+)?',
    '115://[0-9A-Za-z]+(?:\\?password=[0-9A-Za-z]+)?',
    'ed2k://\\|file\\|[^|\\s]+\\|\\d+\\|[0-9A-Fa-f]{32}\\|/?',
    'magnet:\\?xt=urn:btih:[0-9A-Za-z]{32,40}[^\\s]*',
    'https?://[^\\s]+',
  ].join('|'),
  'gi',
)

/** 消息文本 → HTML：转义后给资源链接加高亮标记（配合 .hl-link 样式） */
export const highlightLinks = (text) => {
  if (!text) return ''
  return _escape(text).replace(_LINK_RE, (m) => `<span class="hl-link">${m}</span>`)
}
