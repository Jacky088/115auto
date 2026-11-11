import axios from 'axios'
import { ElMessage } from 'element-plus'

export const api = axios.create({ timeout: 30000 })

api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('token')
  if (t) cfg.headers.Authorization = 'Bearer ' + t
  return cfg
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && !location.hash.startsWith('#/login')) {
      localStorage.removeItem('token')
      location.hash = '#/login'
    }
    return Promise.reject(err)
  },
)

export const errMsg = (e) => e?.response?.data?.detail || e?.message || '请求失败'

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
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export const statusLabel = {
  queued: '排队中', running: '转存中', submitted: '已提交下载', success: '成功', failed: '失败',
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
  error: (err, fallback = '操作失败') => {
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

const _escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// 115 分享 / 115:// / ed2k / magnet / 普通网页链接
const _LINK_RE = new RegExp(
  [
    '(?:https?://)?(?:www\\.)?(?:115\\.com|115cdn\\.com|anxia\\.com)/s/[0-9A-Za-z]+(?:\\?password=[0-9A-Za-z]+)?',
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
