<template>
  <div v-loading="pageLoading">
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
    <!-- 第二行：115 空间（胶囊进度条 + 三项指标）+ 全历史分类占比（左紧凑圆环 + 右分类明细榜） -->
    <OverviewCards
      :space="space"
      :space-loaded="spaceLoaded"
      :categories="stats.categories || {}"
    />

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
        <span class="m-chip" :class="{ active: modFilter === '' }" role="button" tabindex="0"
              :aria-pressed="modFilter === ''" aria-label="显示全部模块日志"
              @click="modFilter = ''" @keydown.enter="modFilter = ''" @keydown.space.prevent="modFilter = ''">全部</span>
        <span v-for="m in logModules" :key="m" class="m-chip" :class="{ active: modFilter === m }"
              role="button" tabindex="0" :aria-pressed="modFilter === m" :aria-label="'只看 ' + m + ' 模块日志'"
              @click="modFilter = m" @keydown.enter="modFilter = m" @keydown.space.prevent="modFilter = m">{{ m }}</span>
      </div>
      <div class="log-window" ref="logBox" @touchstart.passive="autoScroll = false">
        <div v-for="(l, i) in shownLines" :key="i" class="log-line">
          <span style="color: var(--text-muted)">{{ ts(l.ts) }}</span>
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
import { api, fmtSize, fmtTime, statusLabel } from '../api'
import { usePolling } from '../composables/usePolling'
import OverviewCards from '../components/dashboard/OverviewCards.vue'

const stats = ref({})
const space = ref({ total: 0, use: 0, free: 0, ratio: 0 })
const spaceLoaded = ref(false)
const recent = ref([])
const lines = ref([])
const wsOk = ref(false)
const autoScroll = ref(true)
const logBox = ref(null)

// ---- 模块筛选：从日志流中出现过的 module 动态收集 ----
const modFilter = ref('')
const logModules = ref([])
const shownLines = computed(() => modFilter.value
  ? lines.value.filter((l) => l.module === modFilter.value)
  : lines.value)

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
// 首屏加载遮罩：只在第一次拉取时显示，之后的定时刷新不闪遮罩
const pageLoading = ref(true)
const load = async () => {
  if (loading) return // 上一轮未完成时跳过，防止定时器并发堆积
  loading = true
  try {
    const [s, r] = await Promise.all([api.get('/api/dashboard/stats'), api.get('/api/dashboard/recent')])
    stats.value = s.data
    recent.value = r.data
  } catch { /* 网络抖动时保留上次数据，不打断定时刷新 */ } finally {
    loading = false
    pageLoading.value = false
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
    spaceLoaded.value = true
  }
}

let tick = 0
// 10 秒刷新统计/最近记录；115 空间每 60 秒才查一次，避免频繁请求 115 接口。
// 页面不可见时轮询自动暂停（切回标签页立即补一次）
const statPoll = usePolling(() => {
  tick += 1
  load()
  if (tick % 6 === 0) loadSpace()
}, 10000)
onMounted(() => {
  load()
  loadSpace()
  connectWs()
  statPoll.start()
})
onUnmounted(() => {
  disposed = true
  statPoll.stop()
  clearTimeout(wsTimer)
  if (ws) ws.onclose = null
  if (ws) ws.close()
})
</script>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
@media (max-width: 1100px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .stat-grid { grid-template-columns: 1fr; } }
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
  padding: 3px 14px; border-radius: var(--r-pill); font-size: var(--fs-caption); cursor: pointer; user-select: none;
  border: 1px solid var(--saas-line); color: var(--saas-text-sub);
  background: var(--glass-bg-strong); transition: all .15s ease;
}
.m-chip:hover { border-color: var(--el-color-primary); background: rgba(79, 107, 255, .12); color: var(--el-color-primary); }
.m-chip.active {
  background: var(--grad-brand); border-color: transparent; color: #fff;
  box-shadow: 0 3px 10px rgba(79, 107, 255, .35);
}
</style>
