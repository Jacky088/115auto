import { ElMessage } from 'element-plus'
import { api } from '../api'
import { confirmAction } from './confirmDialog'
import { showScanLoading, closeScanLoading } from './scanLoading'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const fmtElapsed = (s) => {
  const v = Math.max(0, Math.round(Number(s) || 0))
  return v >= 60 ? `${Math.floor(v / 60)}分${v % 60}秒` : `${v}秒`
}

async function startJob(base, body) {
  const { data } = await api.post(`${base}/start`, body, { timeout: 30000 })
  return data || {}
}

/**
 * 异步扫描预览：POST {base}/start → 轮询 {base}/status → 返回最终 result。
 * - 扫描在后台 job 中执行，HTTP 不会因大目录扫描超时；
 * - 若历史索引预估目录数过大：>2 万先弹确认（分批扫描），>10 万直接拒绝；
 * - 遮罩实时显示进度，按 Esc 可请求取消；
 * - 轮询失败自动重试 3 次；前端等待超时只停止等待，不取消后台任务。
 * @param {string} base 预览接口基地址，如 /api/dedupe/preview
 * @param {object} body start 请求体（rule_ids 等）
 * @param {{text?: string, timeout?: number}} opts 遮罩初始文案 / 前端等待上限
 */
export async function runPreviewJob(base, body = {}, opts = {}) {
  const { text = '正在扫描文件，请勿进行其他操作……（按 Esc 可取消扫描）', timeout = 900000 } = opts
  let started = await startJob(base, body)
  if (started.need_confirm) {
    const ok = await confirmAction({
      title: '扫描范围较大',
      message: started.msg,
      confirmText: '分批继续扫描',
      cancelText: '缩小目录范围',
      type: 'warning',
    })
    if (!ok) {
      const err = new Error('已取消扫描，请缩小目录范围后再试')
      err.cancelled = true
      throw err
    }
    started = await startJob(base, { ...(body || {}), confirm_large: true })
  }
  if (started.reject) throw new Error(started.msg || '扫描范围超过上限，已拒绝')
  if (!started.ok || !started.job_id) throw new Error(started.msg || '扫描任务发起失败')

  const jobId = started.job_id
  let cancelRequested = false
  const mask = showScanLoading(text)
  const onKey = (e) => {
    if (e.key === 'Escape' && !cancelRequested) {
      cancelRequested = true
      api.post(`${base}/cancel`, { job_id: jobId }).catch(() => {})
    }
  }
  window.addEventListener('keydown', onKey)
  const deadline = Date.now() + timeout
  let pollFailures = 0
  try {
    for (;;) {
      await sleep(1500)
      let st = null
      try {
        const resp = await api.get(`${base}/status`, {
          params: { job_id: jobId }, timeout: 30000,
        })
        st = resp.data
        pollFailures = 0
      } catch (e) {
        // 网络抖动/代理瞬时中断：重试 3 次，避免一次失败就把长时间扫描判死
        pollFailures += 1
        if (pollFailures >= 3) {
          throw new Error('扫描进度查询连续失败（网络或服务异常），请稍后重新点击预览查看结果', { cause: e })
        }
        if (mask && typeof mask.setText === 'function') {
          mask.setText(`进度查询失败，正在重试（${pollFailures}/3）……`)
        }
        continue
      }
      if (!st?.ok) throw new Error(st?.msg || '扫描状态查询失败')
      const label = cancelRequested
        ? '正在取消扫描，请稍候……'
        : `正在扫描：${st.label || ''}（${st.dirs || 0} 个目录 / ${st.requests || 0} 次请求，已用 ${fmtElapsed(st.elapsed)}）请勿进行其他操作`
      if (mask && typeof mask.setText === 'function') mask.setText(label)
      if (st.state !== 'running') {
        const result = st.result || {}
        if (st.state === 'cancelled') {
          const err = new Error(result.msg || '扫描已取消')
          err.cancelled = true
          throw err
        }
        if (st.state === 'failed' || result.ok === false) {
          throw new Error(result.msg || st.error || '扫描失败')
        }
        return result
      }
      if (Date.now() > deadline) {
        // 不再取消后台任务：扫描会继续按预算推进，索引也会落库，稍后重开预览即可看结果
        ElMessage.warning('扫描仍在后台继续，已停止等待；请稍后重新点击预览查看结果')
        const err = new Error('扫描仍在后台继续，请稍后重新点击预览')
        err.silent = true
        throw err
      }
    }
  } finally {
    window.removeEventListener('keydown', onKey)
    closeScanLoading()
  }
}
