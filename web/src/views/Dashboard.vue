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
        <el-table-column prop="kind" label="类型" width="80">
          <template #default="{ row }">
            <el-tag :type="row.kind === 'offline' ? 'warning'
                            : (row.priority > 0 ? 'danger' : 'primary')" size="small">
              {{ row.kind === 'offline' ? '云下载'
                 : (row.priority > 0 ? '补全' : (row.increment ? '追更' : '分享')) }}
            </el-tag>
          </template>
        </el-table-column>
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
          <el-tooltip content="仅清空当前显示，不影响「日志查询」中的历史日志">
            <el-button @click="lines = []">清屏</el-button>
          </el-tooltip>
          <el-tag size="small" :type="wsOk ? 'success' : 'danger'">{{ wsOk ? '已连接' : '未连接' }}</el-tag>
          <el-button text type="primary" style="margin-left: auto" @click="$router.push('/history')">
            历史日志 →
          </el-button>
        </div>
      </template>
      <!-- 模块筛选：从日志流中动态收集出现过的 module，点击切换「全部/某模块」 -->
      <div class="module-chips">
        <span class="m-chip" :class="{ active: modFilter === '' }" @click="modFilter = ''">全部</span>
        <span v-for="m in logModules" :key="m" class="m-chip" :class="{ active: modFilter === m }"
              @click="modFilter = m">{{ m }}</span>
      </div>
      <div class="log-window" ref="logBox" @touchstart.passive="autoScroll = false">
        <div v-for="(l, i) in shownLines" :key="i" class="log-line">
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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

// ---- 模块筛选：从日志流中出现过的 module 动态收集 ----
const modFilter = ref('')
const logModules = ref([])
const shownLines = computed(() => modFilter.value
  ? lines.value.filter((l) => l.module === modFilter.value)
  : lines.value)

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
  // 鉴权走 HttpOnly Cookie（sa_auth，WebSocket 握手自动携带同源 Cookie）：
  // token 不再经子协议传递，前端无需持有任何 token
  ws = new WebSocket(`${proto}://${location.host}/ws/logs`)
  ws.onopen = () => { wsOk.value = true; replayWithoutSeq = true }
  ws.onmessage = (ev) => {
    let l
    try { l = JSON.parse(ev.data) } catch { return } // 丢弃异常帧，不中断消息处理
    if (typeof l.seq === 'number') {
      // 后端重连时会补发最近 100 条：seq <= lastSeq 的都是已显示过的，直接丢弃
      if (lastSeq !== null && l.seq <= lastSeq) return
      lastSeq = l.seq
    } else if (replayWithoutSeq) {
      lines.value = [] // 旧后端无 seq：重连补发前先清屏，避免重复累积
    }
    replayWithoutSeq = false
    lines.value.push(l)
    if (l.module && !logModules.value.includes(l.module)) logModules.value.push(l.module)
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
const load = async () => {
  if (loading) return // 上一轮未完成时跳过，防止定时器并发堆积
  loading = true
  try {
    const [s, r] = await Promise.all([api.get('/api/dashboard/stats'), api.get('/api/dashboard/recent')])
    stats.value = s.data
    recent.value = r.data
    // 分类占比只用本地数据，先画出来：不能等待 115 空间接口（它会打 115，慢的时候要好几秒）
    renderPie()
    retryRender()
  } catch { /* 网络抖动时保留上次数据，不打断定时刷新 */ } finally {
    loading = false
  }
}

// 115 空间单独加载，与统计/分类占比互不阻塞（后端另有 5 分钟缓存）
let spaceLoading = false
const loadSpace = async () => {
  if (spaceLoading) return
  spaceLoading = true
  try {
    const sp = await api.get('/api/account/115/status')
    if (sp.data.space) space.value = sp.data.space
  } catch { /* 未登录或读取失败时保持上次数据 */ } finally {
    spaceLoading = false
  }
}

// ---- 图表渲染公共逻辑 ----
// 空白修复：容器处于 0 尺寸时（首屏布局未完成/卡片被隐藏/切回标签页等）echarts.init
// 会得到 0×0 空白画布且不会自愈。这里改为：① 尺寸未就绪时不渲染，由 ResizeObserver
// 在尺寸可用/变化时触发重画；② 主题切换（html.dark 变化）时用深色配色重画。
const chartSizeOk = (el) => !!(el && el.clientWidth > 0 && el.clientHeight > 0)
const chartIsDark = () => document.documentElement.classList.contains('dark')
const chartTextColor = () => (chartIsDark() ? '#8a97b3' : '#44506b')
const chartEmptyColor = () => (chartIsDark() ? '#5d6b85' : '#999')
const chartCardBorder = () => (chartIsDark() ? '#26303f' : '#fff')
const chartTrackColor = () => (chartIsDark() ? '#26303f' : '#e9eefb')

const renderPie = () => {
  const el = pieRef.value
  if (!el || !chartSizeOk(el)) return // 尺寸未就绪：等 ResizeObserver 触发
  if (!pieChart) pieChart = echarts.init(el)
  const agg = {}
  for (const t of recent.value) {
    const k = t.category || '未分类'
    agg[k] = (agg[k] || 0) + 1
  }
  const data = Object.entries(agg).map(([name, value]) => ({ name, value }))
  if (!data.length) {
    pieChart.clear()
    pieChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'middle',
      textStyle: { color: chartEmptyColor(), fontSize: 13, fontWeight: 400 } } })
    return
  }
  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
    legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8,
              textStyle: { fontSize: 12, color: chartTextColor() } },
    series: [{
      type: 'pie', radius: ['48%', '70%'], center: ['50%', '44%'],
      itemStyle: { borderRadius: 6, borderColor: chartCardBorder(), borderWidth: 2 },
      label: { show: false },
      data,
      color: ['#4f6bff', '#8b5cf6', '#22c1dc', '#f59e0b', '#ef4444', '#5d8bff', '#10b981'],
    }],
  }, true)
}

// 115 空间环形图（与分类占比同款圆环风格；颜色按占用率：<70 蓝 / 70~90 黄 / ≥90 红）
let spaceChart = null
const spaceRef = ref(null)
const spaceRatioLabel = computed(() => {
  const r = space.value.ratio || 0
  return r >= 90 ? '空间告急' : r >= 70 ? '空间较满' : '空间充足'
})
const renderSpace = () => {
  const el = spaceRef.value
  if (!el || !chartSizeOk(el) || !space.value.total) return // 尺寸未就绪或无数据：等 ResizeObserver
  if (!spaceChart) spaceChart = echarts.init(el)
  const used = space.value.ratio || 0
  spaceChart.setOption({
    tooltip: { trigger: 'item', formatter: `{d}% 已使用（${fmtSize(space.value.use)} / ${fmtSize(space.value.total)}）` },
    title: {
      text: `${used}%`, subtext: '已使用',
      left: 'center', top: '36%',
      textStyle: { fontSize: 24, fontWeight: 700, color: spaceColor.value },
      subtextStyle: { fontSize: 12, color: chartEmptyColor() },
    },
    series: [{
      type: 'pie', radius: ['62%', '80%'], center: ['50%', '48%'],
      startAngle: 90, silent: false, label: { show: false },
      itemStyle: { borderRadius: 8, borderColor: chartCardBorder(), borderWidth: 2 },
      data: [
        { value: used, itemStyle: { color: spaceColor.value } },
        { value: Math.max(100 - used, 0), itemStyle: { color: chartTrackColor() } },
      ],
    }],
  }, true)
}

const renderAll = () => { renderPie(); renderSpace() }

// 路由切换/首屏布局未完成时容器可能是 0 尺寸：短间隔重试几次，避免图表空白且不再触发 ResizeObserver
let renderRetryTimer = null
const retryRender = (tries = 5) => {
  clearTimeout(renderRetryTimer)
  // 实例若在 0×0 容器上初始化过，setOption 不会自愈，必须显式 resize
  if (pieChart && chartSizeOk(pieRef.value)) pieChart.resize()
  if (spaceChart && chartSizeOk(spaceRef.value)) spaceChart.resize()
  renderAll()
  const piePending = !chartSizeOk(pieRef.value)
  const spacePending = !!space.value.total && !chartSizeOk(spaceRef.value)
  if ((piePending || spacePending) && tries > 0) {
    renderRetryTimer = setTimeout(() => retryRender(tries - 1), 150)
  }
}

// 空间图容器只在有数据时才渲染出来：挂载后补观察，否则尺寸变化不会触发重画
watch(spaceRef, (el) => {
  if (!el) return
  if (ro) ro.observe(el)
  nextTick(() => retryRender())
})

let timer = null
let tick = 0
let ro = null
let roTimer = null
let themeObs = null
onMounted(() => {
  load()
  loadSpace()
  connectWs()
  // 10 秒刷新统计/最近记录；115 空间每 60 秒才查一次，避免频繁请求 115 接口
  timer = setInterval(() => {
    tick += 1
    load()
    if (tick % 6 === 0) loadSpace()
  }, 10000)
  window.addEventListener('resize', onResize)
  // ResizeObserver：容器尺寸从 0 变为可用（或任何尺寸变化）时自动重画 —— 空白修复的关键
  ro = new ResizeObserver(() => {
    clearTimeout(roTimer)
    roTimer = setTimeout(() => retryRender(), 100) // 防抖，避免拖拽窗口时为频繁重画
  })
  if (pieRef.value) ro.observe(pieRef.value)
  if (spaceRef.value) ro.observe(spaceRef.value)
  nextTick(() => retryRender()) // 首屏补画：路由/卡片动画结束后尺寸才稳定
  // 主题切换（html.dark 类变化）时用新配色重画图表
  themeObs = new MutationObserver(() => renderAll())
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})
onUnmounted(() => {
  disposed = true
  clearInterval(timer)
  clearTimeout(wsTimer)
  clearTimeout(roTimer)
  clearTimeout(renderRetryTimer)
  window.removeEventListener('resize', onResize)
  if (ro) ro.disconnect()
  if (themeObs) themeObs.disconnect()
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
.space-ratio-tag { margin-left: auto; font-size: var(--fs-caption); font-weight: 600; }
.space-empty { text-align: center; color: #999; font-size: var(--fs-body); padding: 60px 0; }
.stat-card :deep(.el-card__body) { padding: 18px; }
.stat-top { display: flex; align-items: center; gap: 10px; }
.stat-icon {
  width: 36px; height: 36px; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .35), 0 3px 10px rgba(31, 45, 90, .08);
}
.stat-label { color: var(--saas-text-sub); font-size: var(--fs-body); }
.stat-value {
  font-size: var(--fs-stat); font-weight: 700; margin-top: 10px; letter-spacing: .3px;
  font-variant-numeric: tabular-nums;
}
.module-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.m-chip {
  padding: 3px 14px; border-radius: 999px; font-size: var(--fs-caption); cursor: pointer; user-select: none;
  border: 1px solid var(--saas-line); color: var(--saas-text-sub);
  background: var(--glass-bg-strong); transition: all .15s ease;
}
.m-chip:hover { border-color: var(--el-color-primary); background: rgba(79, 107, 255, .12); color: var(--el-color-primary); }
.m-chip.active {
  background: var(--grad-brand); border-color: transparent; color: #fff;
  box-shadow: 0 3px 10px rgba(79, 107, 255, .35);
}
.space-bar { height: 10px; border-radius: 5px; background: #e9eefb; margin-top: 16px; overflow: hidden; }
.space-used { height: 100%; border-radius: 5px; transition: width .6s ease; min-width: 3%; }
.space-text { font-size: var(--fs-caption); color: var(--saas-text-sub); margin-top: 8px; }
.space-ratio { float: right; font-weight: 600; }
.pie-chart { width: 100%; height: 280px; }
</style>
