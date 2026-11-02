<template>
  <div>
    <div class="stat-grid page-card">
      <el-card shadow="never" v-for="c in cards" :key="c.label" class="stat-card">
        <div class="stat-top">
          <div class="stat-icon" :style="{ background: c.chip, color: c.color }">
            <el-icon :size="18"><component :is="c.icon" /></el-icon>
          </div>
          <span class="stat-label">{{ c.label }}</span>
        </div>
        <div class="stat-value" :style="{ color: c.color }">{{ c.value }}</div>
      </el-card>
      <!-- 115 空间柱状图卡片 -->
      <el-card shadow="never" class="stat-card space-card">
        <div class="stat-top">
          <div class="stat-icon" :style="{ background: 'rgba(47,107,255,.12)', color: '#2f6bff' }">
            <el-icon :size="18"><Coin /></el-icon>
          </div>
          <span class="stat-label">115 空间</span>
        </div>
        <template v-if="space.total">
          <div class="space-bar">
            <div class="space-used" :style="{ width: space.ratio + '%', background: spaceColor }"></div>
          </div>
          <div class="space-text">
            已用 {{ fmtSize(space.use) }} / {{ fmtSize(space.total) }}
            <span class="space-ratio" :style="{ color: spaceColor }">{{ space.ratio }}%</span>
          </div>
        </template>
        <div v-else class="space-text">{{ space.total === 0 ? '-' : '未登录或读取失败' }}</div>
      </el-card>
    </div>

    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>最近 20 条转存的分类占比</span>
        </div>
      </template>
      <div ref="pieRef" class="pie-chart"></div>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>最近转存</template>
      <el-table :data="recent" size="small" empty-text="暂无转存记录">
        <el-table-column prop="created_at" label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="90" />
        <el-table-column prop="target_dir" label="目标目录" min-width="150" show-overflow-tooltip />
        <el-table-column prop="size" label="大小" width="90">
          <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>实时日志</span>
          <el-button size="small" @click="autoScroll = !autoScroll">{{ autoScroll ? '暂停滚动' : '自动滚动' }}</el-button>
          <el-tooltip content="仅清空当前显示内容，不影响已保存的历史日志（可在「日志查询」页面查看）">
            <el-button size="small" @click="lines = []">清屏</el-button>
          </el-tooltip>
          <el-tag size="small" :type="wsOk ? 'success' : 'danger'">{{ wsOk ? '已连接' : '未连接' }}</el-tag>
          <el-button size="small" text type="primary" style="margin-left: auto" @click="$router.push('/history')">
            历史日志 →
          </el-button>
        </div>
      </template>
      <div class="log-window" ref="logBox" @touchstart.passive="autoScroll = false">
        <div v-for="(l, i) in lines" :key="i" class="log-line">
          <span style="color: #666">{{ ts(l.ts) }}</span>
          <span :class="'lv-' + l.level">[{{ l.level }}]</span>
          <span class="mod">{{ l.module }}</span>
          <span>{{ l.message }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, fmtSize, fmtTime, statusLabel } from '../api'

const stats = ref({})
const space = ref({ total: 0, use: 0, free: 0, ratio: 0 })
const recent = ref([])
const lines = ref([])
const wsOk = ref(false)
const autoScroll = ref(true)
const logBox = ref(null)
const pieRef = ref(null)
let pieChart = null

// 空间颜色：<70% 蓝、70~90% 橙、>90% 红
const spaceColor = computed(() => {
  const r = space.value.ratio || 0
  return r >= 90 ? '#ef4444' : r >= 70 ? '#f59e0b' : '#2f6bff'
})

const cards = computed(() => {
  const s = stats.value
  return [
    { label: '今日成功', value: s.today_success ?? '-', color: '#16a34a', chip: 'rgba(22,163,74,.12)', icon: 'CircleCheck' },
    { label: '今日失败/拦截', value: s.today_failed ?? '-', color: '#ef4444', chip: 'rgba(239,68,68,.12)', icon: 'CircleClose' },
    { label: '今日限额', value: `${s.quota ?? 0}/${s.daily_limit ?? '-'}`, color: '#2f6bff', chip: 'rgba(47,107,255,.12)', icon: 'Odometer' },
    { label: '排队中 / 监控频道', value: `${s.queue ?? 0} / ${s.channels ?? 0}`, color: '#d97706', chip: 'rgba(217,119,6,.12)', icon: 'FolderOpened' },
  ]
})

const statusTag = (s) => ({ success: 'success', failed: 'danger', blocked: 'warning',
                            queued: 'info', running: 'primary' }[s] || 'info')
const ts = (t) => {
  const d = new Date(t * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

let ws = null
let wsTimer = null
let disposed = false
const connectWs = () => {
  if (disposed) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/logs?token=${localStorage.getItem('token')}`)
  ws.onopen = () => { wsOk.value = true }
  ws.onmessage = (ev) => {
    lines.value.push(JSON.parse(ev.data))
    if (lines.value.length > 400) lines.value.splice(0, lines.value.length - 400)
    if (autoScroll.value) nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
  }
  ws.onclose = () => {
    wsOk.value = false
    if (!disposed) wsTimer = setTimeout(connectWs, 3000)
  }
  ws.onerror = () => ws.close()
}

const load = async (withSpace = false) => {
  try {
    const [s, r] = await Promise.all([api.get('/api/dashboard/stats'), api.get('/api/dashboard/recent')])
    stats.value = s.data
    recent.value = r.data
    // 空间信息（登录后才有）：调用会打 115 接口，只在需要时刷新（后端另有 5 分钟缓存）
    if (withSpace) {
      try {
        const sp = await api.get('/api/account/115/status')
        if (sp.data.space) space.value = sp.data.space
      } catch { /* ignore */ }
    }
    renderPie()
  } catch { /* 网络抖动时保留上次数据，不打断定时刷新 */ }
}

const renderPie = () => {
  if (!pieRef.value) return
  if (!pieChart) pieChart = echarts.init(pieRef.value)
  const agg = {}
  for (const t of recent.value) {
    const k = t.category || '未分类'
    agg[k] = (agg[k] || 0) + 1
  }
  const data = Object.entries(agg).map(([name, value]) => ({ name, value }))
  if (!data.length) {
    pieChart.clear()
    pieChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'middle',
      textStyle: { color: '#999', fontSize: 13, fontWeight: 400 } } })
    return
  }
  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
    legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 12 } },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['50%', '46%'],
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data,
      color: ['#2f6bff', '#5d8bff', '#22c1dc', '#f59e0b', '#ef4444', '#8b5cf6', '#10b981'],
    }],
  })
}

let timer = null
let tick = 0
onMounted(() => {
  load(true)
  connectWs()
  // 10 秒刷新统计/最近记录；115 空间每 60 秒才查一次，避免频繁请求 115 接口
  timer = setInterval(() => {
    tick += 1
    load(tick % 6 === 0)
  }, 10000)
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  disposed = true
  clearInterval(timer)
  clearTimeout(wsTimer)
  window.removeEventListener('resize', onResize)
  if (ws) ws.onclose = null
  if (ws) ws.close()
  if (pieChart) pieChart.dispose()
})
const onResize = () => { if (pieChart) pieChart.resize() }
</script>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
@media (max-width: 1100px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .stat-grid { grid-template-columns: 1fr; } }
@media (max-width: 768px) { .pie-chart { height: 220px; } }
.stat-card :deep(.el-card__body) { padding: 18px; }
.stat-top { display: flex; align-items: center; gap: 10px; }
.stat-icon {
  width: 36px; height: 36px; border-radius: 10px; flex: none;
  display: flex; align-items: center; justify-content: center;
}
.stat-label { color: var(--saas-text-sub); font-size: 13px; }
.stat-value { font-size: 26px; font-weight: 700; margin-top: 10px; letter-spacing: .3px; }
.space-bar { height: 10px; border-radius: 5px; background: #e9eefb; margin-top: 16px; overflow: hidden; }
.space-used { height: 100%; border-radius: 5px; transition: width .6s ease; min-width: 3%; }
.space-text { font-size: 12px; color: var(--saas-text-sub); margin-top: 8px; }
.space-ratio { float: right; font-weight: 600; }
.pie-chart { width: 100%; height: 280px; }
</style>
