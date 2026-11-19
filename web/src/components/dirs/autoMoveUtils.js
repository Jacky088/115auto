/**
 * 定时文件整理（按目录映射移动中文文件）公用工具函数。
 */

export const normPath = (p) => {
  const t = String(p || '').trim()
  return t ? '/' + t.replace(/^\/+|\/+$/g, '') : ''
}

export const extractKeyword = (path) => {
  if (!path) return ''
  const parts = path.trim().replace(/^\/+|\/+$/g, '').split('/').filter(p => p.trim())
  if (!parts.length) return ''
  let idx = parts.length - 1
  const seasonRe = /^(?:season\s*\d+|s\d{1,2}|第\s*[一二三四五六七八九十\d]+\s*季|特别篇|sp|specials?)$/i
  if (idx > 0 && seasonRe.test(parts[idx])) {
    idx -= 1
  }
  const candidate = parts[idx]
  const cleaned = candidate.replace(/[([{（【]?\s*(?:19\d{2}|20\d{2})\s*[)\]}）】]?/g, '').trim()
  return cleaned || candidate
}

export const formatTime = (ts) => {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
}
