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
    </div>
    <!-- 第二行：115 空间（环形）+ 分类占比（环形），左右等高并排 -->
    <div class="charts-grid page-card">
      <el-card shadow="never" class="chart-card">
        <template #header>
          <div class="toolbar" style="margin-bottom: 0">
            <span>115 空间</span>
            <span v-if="space.total" class="space-ratio-tag" :style="{ color: spaceColor }">
              {{ spaceRatioLabel }}
            </span>
          </div>
        </template>
        <template v-if="space.total">
          <div ref="spaceRef" class="ring-chart"></div>
          <div class="space-text center">
            已用 {{ fmtSize(space.use) }} / {{ fmtSize(space.total) }}
          </div>
        </template>
        <div v-else class="space-empty">{{ space.total === 0 ? '-' : '未登录或读取失败' }}</div>
      </el-card>

      <el-card shadow="never" class="chart-card">
        <template #header>
          <div class="toolbar" style="margin-bottom: 0">
            <span>最近 20 条转存的分类占比</span>
          </div>
        </template>
        <div ref="pieRef" class="ring-chart"></div>
      </el-card>
    </div>

    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>最近转存（最新 20 条）</span>
          <el-button text type="primary" style="margin-left: auto"
                     @click="$router.push('/transfers')">查看全部转存记录 →</el-button>
        </div>
      </template>
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
          <el-button @click="autoScroll = !autoScroll">{{ autoScroll ? '暂停滚动' : '自动滚动' }}</el-button>
          <el-tooltip content="仅清空当前显示内容，不影响已保存的历史日志（可在「日志查询」页面查看）">
            <el-button @click="lines = []">清屏</el-button>
          </el-tooltip>
          <el-tag size="small" :type="wsOk ? 'success' : 'danger'">{{ wsOk ? '已连接' : '未连接' }}</el-tag>
          <el-button text type="primary" style="margin-left: auto" @click="$router.push('/history')">
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
let lastSeq = null        // 已显示的最大日志序号：后端重连补发时据此去重
let replayWithoutSeq = false
const connectWs = () => {
  if (disposed) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = localStorage.getItem('token')
  // token 经 Sec-WebSocket-Protocol 头传递（浏览器 WebSocket 无法自定义请求头），
  // 避免 JWT 出现在 URL 里被访问日志/代理记录
  ws = new WebSocket(`${proto}://${location.host}/ws/logs`, ['bearer', token])
  ws.onopen = () => { wsOk.value = true; replayWithoutSeq = true }
  ws.onmessage = (ev) => {
    const l = JSON.parse(ev.data)
    if (typeof l.seq === 'number') {
      // 后端重连时会补发最近 100 条：seq <= lastSeq 的都是已显示过的，直接丢弃
      if (lastSeq !== null && l.seq <= lastSeq) return
      lastSeq = l.seq
    } else if (replayWithoutSeq) {
      lines.value = [] // 旧后端无 seq：重连补发前先清屏，避免重复累积
    }
    replayWithoutSeq = false
    lines.value.push(l)
    if (lines.value.length > 400) lines.value.splice(0, lines.value.length - 400)
    if (autoScroll.value) nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
  }
  ws.onclose = () => {
    wsOk.value = false
    if (!disposed) wsTimer = setTimeout(connectWs, 3000)
  }
  ws.onerror = () => ws.close()
}

let loading = false
const load = async (withSpace = false) => {
  if (loading) return // 上一轮未完成时跳过，防止定时器并发堆积
  loading = true
  try {
    const [s, r] = await Promise.all([api.get('/api/dashboard/stats'), api.get('/api/dashboard/recent')])
    stats.value = s.data
    recent.value = r.data
    // 空间信息（登录后才有）：调用会打 115 接口，只在需要时刷新（后端另有 5 分钟缓存）
    if (withSpace) {
      try {
        const sp = await api.get('/api/account/115/status')
        if (sp.data.space) { space.value = sp.data.space; nextTick(renderSpace) }
      } catch { /* ignore */ }
    }
    renderPie()
  } catch { /* 网络抖动时保留上次数据，不打断定时刷新 */ } finally {
    loading = false
  }
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
      type: 'pie', radius: ['48%', '70%'], center: ['50%', '44%'],
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data,
      color: ['#2f6bff', '#5d8bff', '#22c1dc', '#f59e0b', '#ef4444', '#8b5cf6', '#10b981'],
    }],
  })
}

// 115 空间环形图（与分类占比同款圆环风格；颜色按占用率：<70 蓝 / 70~90 黄 / ≥90 红）
let spaceChart = null
const spaceRef = ref(null)
const spaceRatioLabel = computed(() => {
  const r = space.value.ratio || 0
  return r >= 90 ? '空间告急' : r >= 70 ? '空间较满' : '空间充足'
})
const renderSpace = () => {
  if (!spaceRef.value || !space.value.total) return
  if (!spaceChart) spaceChart = echarts.init(spaceRef.value)
  const used = space.value.ratio || 0
  spaceChart.setOption({
    tooltip: { trigger: 'item', formatter: `{d}% 已使用（${fmtSize(space.value.use)} / ${fmtSize(space.value.total)}）` },
    title: {
      text: `${used}%`, subtext: '已使用',
      left: 'center', top: '36%',
      textStyle: { fontSize: 24, fontWeight: 700, color: spaceColor.value },
      subtextStyle: { fontSize: 12, color: '#999' },
    },
    series: [{
      type: 'pie', radius: ['62%', '80%'], center: ['50%', '48%'],
      startAngle: 90, silent: false, label: { show: false },
      itemStyle: { borderRadius: 8, borderColor: '#fff', borderWidth: 2 },
      data: [
        { value: used, itemStyle: { color: spaceColor.value } },
        { value: Math.max(100 - used, 0), itemStyle: { color: '#e9eefb' } },
      ],
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
  if (spaceChart) spaceChart.dispose()
})
const onResize = () => { if (pieChart) pieChart.resize(); if (spaceChart) spaceChart.resize() }
</script>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
@media (max-width: 1100px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .stat-grid { grid-template-columns: 1fr; } }
/* 图表行：空间（左）+ 分类占比（右）等高并排 */
.charts-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 16px; align-items: stretch; }
@media (max-width: 900px) { .charts-grid { grid-template-columns: 1fr; } }
.chart-card { display: flex; flex-direction: column; }
.chart-card :deep(.el-card__body) { flex: 1; display: flex; flex-direction: column; }
.ring-chart { width: 100%; height: 280px; flex: 1; min-height: 240px; }
@media (max-width: 768px) { .ring-chart { height: 220px; min-height: 200px; } }
.space-text.center { text-align: center; margin-top: 0; padding-bottom: 8px; }
.space-ratio-tag { margin-left: auto; font-size: 12px; font-weight: 600; }
.space-empty { text-align: center; color: #999; font-size: 13px; padding: 60px 0; }
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
