<template>
  <div class="media-page" v-loading="pageLoading">
    <!-- 顶部系统运行状态联动警告 -->
    <el-alert
      v-if="!status.running"
      type="warning"
      show-icon
      :closable="false"
      class="mb16"
      title="系统未运行，请先点击顶栏「开始运行」"
    />
    <el-alert
      v-else-if="status.gate.active_mode === 'auto_move'"
      type="info"
      show-icon
      :closable="false"
      class="mb16"
      title="归档任务执行中，结束后再运行整理或刮削"
    />
    <el-alert
      v-else-if="isMediaRunning && status.gate.active_mode !== activeTab"
      type="info"
      show-icon
      :closable="false"
      class="mb16"
      :title="`后台正在执行「${mediaModeLabel}」。同一时间只能运行一个后台任务，请等待其结束后再操作。`"
    />

    <!-- 主工作台：三大独立程序与设置标签页 -->
    <el-card shadow="never" class="page-card">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane name="organize">
          <template #label>
            <span class="tab-label"><el-icon><FolderOpened /></el-icon><span>目录整理</span></span>
          </template>
          <OrganizeTab ref="organizeTabRef" />
        </el-tab-pane>

        <el-tab-pane name="scrape">
          <template #label>
            <span class="tab-label">
              <el-icon><Film /></el-icon>
              <span>媒体刮削</span>
              <el-badge
                v-if="(status.counts.need_confirm + status.counts.staged) > 0"
                :value="status.counts.need_confirm + status.counts.staged"
                class="tab-badge"
              />
            </span>
          </template>
          <ScrapeTab ref="scrapeTabRef" />
        </el-tab-pane>

        <el-tab-pane name="dedupe">
          <template #label>
            <span class="tab-label">
              <el-icon><Brush /></el-icon>
              <span>重复清理</span>
              <el-badge
                v-if="(status.counts.dedupe_rules || 0) > 0"
                :value="status.counts.dedupe_rules"
                type="info"
                class="tab-badge"
              />
            </span>
          </template>
          <DedupeTab ref="dedupeTabRef" />
        </el-tab-pane>

        <el-tab-pane name="settings">
          <template #label>
            <span class="tab-label"><el-icon><Setting /></el-icon><span>策略与设置</span></span>
          </template>
          <MediaSettingsTab ref="settingsTabRef" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <MediaRuleDialog ref="ruleDialogRef" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errMsg, toast } from '../api'
import { confirmAction } from '../composables/confirmDialog'
import { usePolling } from '../composables/usePolling'
import { usePersistedTab } from '../composables/usePersistedTab'
import '../components/media/media.css'
import OrganizeTab from '../components/media/OrganizeTab.vue'
import ScrapeTab from '../components/media/ScrapeTab.vue'
import DedupeTab from '../components/media/DedupeTab.vue'
import MediaSettingsTab from '../components/media/MediaSettingsTab.vue'
import MediaRuleDialog from '../components/media/MediaRuleDialog.vue'

const route = useRoute()
const activeTab = usePersistedTab('media', ['organize', 'scrape', 'dedupe', 'settings'], 'organize', route)
const pageLoading = ref(true)

const organizeTabRef = ref(null)
const scrapeTabRef = ref(null)
const dedupeTabRef = ref(null)
const settingsTabRef = ref(null)
const ruleDialogRef = ref(null)

const isMobile = ref(typeof window !== 'undefined' && window.innerWidth <= 768)
function onResize() {
  isMobile.value = typeof window !== 'undefined' && window.innerWidth <= 768
}
const paginationLayout = computed(() => (isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next'))

const status = ref({
  running: false,
  tmdb_configured: false,
  gate: {
    active_mode: 'none',
    gate_state: 'idle',
    mode_label: '空闲',
    exclusive_busy: false,
    organize_running: false,
    scrape_running: false,
    auto_move_running: false,
    drain_progress: {},
    media_progress: {},
  },
  counts: { need_confirm: 0, staged: 0, queued: 0, running: 0, done: 0, organize_rules: 0, scrape_rules: 0, dedupe_rules: 0, records: 0 },
  last_run: { organize: 0, scrape: 0, dedupe: 0 },
})

const rules = ref([])
const stopping = ref(false)

const organizeRules = computed(() => rules.value.filter(r => !!r.organize_enabled))
const scrapeRules = computed(() => rules.value.filter(r => !!r.scrape_enabled))
const activeOrganizeRules = computed(() => organizeRules.value.filter(r => !!r.enabled))
const activeScrapeRules = computed(() => scrapeRules.value.filter(r => !!r.enabled))

const isBusy = computed(() => !!(status.value.gate.exclusive_busy ?? status.value.gate.busy))
const isMediaRunning = computed(() => ['organize', 'scrape', 'dedupe'].includes(status.value.gate.active_mode))
const mediaModeLabel = computed(() => ({
  organize: '目录整理',
  scrape: '媒体刮削',
  dedupe: '重复清理',
}[status.value.gate.active_mode] || status.value.gate.mode_label || '后台任务'))

const phaseLabel = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') return '正在暂停新转存，等待排队任务处理完'
  if (mp.phase === 'scanning') return '正在扫描 115 目录树'
  return '按顺序逐个处理中'
})

const activeProgressLabel = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  const dp = status.value.gate.drain_progress || status.value.gate.drain_status || {}
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') {
    const rem = dp.remaining_total ?? dp.unfinished ?? '?'
    return `等待队列清空（剩余 ${rem} 个，已等待 ${dp.elapsed_sec ?? 0} 秒）`
  }
  return mp.label || '准备中...'
})

const progressPercent = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  if (!mp.total || mp.total <= 0) return 0
  return Math.min(100, Math.round(((mp.current || 0) / mp.total) * 100))
})

function fmtSizeMb(bytes) {
  const n = Number(bytes || 0)
  if (!n || n <= 0) return ''
  return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

function tmdbUrl(kind, tmdbId) {
  const safeKind = kind === 'tv' ? 'tv' : 'movie'
  const idNum = Number(tmdbId)
  if (!Number.isFinite(idNum) || idNum <= 0) return 'https://www.themoviedb.org/'
  return `https://www.themoviedb.org/${safeKind}/${Math.trunc(idNum)}`
}

async function loadStatus() {
  try {
    const { data } = await api.get('/api/media/status')
    status.value = data
  } catch {}
}

async function loadRules() {
  try {
    const { data } = await api.get('/api/media/rules')
    rules.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function stopMedia() {
  if (!await confirmAction({
    title: `确认中断「${mediaModeLabel.value}」？`,
    message: '将在处理完当前文件后安全停止，已完成的改动保留，未处理的条目下次可继续。',
    confirmText: '确认中断',
    danger: true,
  })) return
  stopping.value = true
  try {
    const { data } = await api.post('/api/media/stop')
    toast.info(data.msg)
    await loadStatus()
  } catch (e) { toast.error(errMsg(e)) }
  finally { stopping.value = false }
}

const togglingRuleIds = new Set()
async function toggleRuleEnabled(row, val) {
  if (togglingRuleIds.has(row.id)) return
  togglingRuleIds.add(row.id)
  try {
    await api.put(`/api/media/rules/${row.id}`, {
      ...row,
      organize_enabled: !!row.organize_enabled,
      scrape_enabled: !!row.scrape_enabled,
      enabled: !!val,
    })
    organizeTabRef.value?.clearPreviewCache?.()
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) {
    toast.error(errMsg(e))
    await loadRules()
  } finally {
    togglingRuleIds.delete(row.id)
  }
}

async function delRule(id) {
  try {
    await api.delete(`/api/media/rules/${id}`)
    toast.success('已删除规则')
    organizeTabRef.value?.clearPreviewCache?.()
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

function openOrganizeRuleDialog(row = null) {
  ruleDialogRef.value?.openOrganizeRuleDialog(row)
}

function openScrapeRuleDialog(row = null) {
  ruleDialogRef.value?.openScrapeRuleDialog(row)
}

provide('mediaCtx', {
  status,
  organizeRules,
  scrapeRules,
  activeOrganizeRules,
  activeScrapeRules,
  isBusy,
  isMobile,
  paginationLayout,
  phaseLabel,
  activeProgressLabel,
  progressPercent,
  stopping,
  stopMedia,
  loadStatus,
  loadRules,
  openOrganizeRuleDialog,
  openScrapeRuleDialog,
  toggleRuleEnabled,
  delRule,
  fmtSizeMb,
  tmdbUrl,
})

function onTabChange(tab) {
  if (tab === 'organize') {
    loadRules()
    organizeTabRef.value?.loadRecords()
    loadStatus()
  } else if (tab === 'scrape') {
    loadRules()
    scrapeTabRef.value?.loadTasks()
    loadStatus()
  } else if (tab === 'dedupe') {
    dedupeTabRef.value?.loadDedupeRules()
    dedupeTabRef.value?.loadDedupeRecords()
    loadStatus()
  } else if (tab === 'settings') {
    settingsTabRef.value?.loadSettings()
    loadStatus()
  }
}

let prevPollMode = 'none'
let prevScrapePending = 0
const mediaPoll = usePolling(async () => {
  await loadStatus()
  const mode = status.value.gate.active_mode
  const curPending = Number(status.value.counts?.running || 0) + Number(status.value.counts?.queued || 0)
  const finishedOrganize = prevPollMode === 'organize' && mode !== 'organize'
  const finishedScrape = (prevPollMode === 'scrape' && mode !== 'scrape') || (prevScrapePending > 0 && curPending === 0)
  const finishedDedupe = prevPollMode === 'dedupe' && mode !== 'dedupe'

  if (activeTab.value === 'organize' && (mode === 'organize' || finishedOrganize || !organizeTabRef.value?.hasRecords())) {
    await organizeTabRef.value?.loadRecords()
  } else if (activeTab.value === 'scrape' && (mode === 'scrape' || curPending > 0 || finishedScrape)) {
    await scrapeTabRef.value?.loadTasks()
  } else if (activeTab.value === 'dedupe' && (mode === 'dedupe' || finishedDedupe || !dedupeTabRef.value?.hasRecords())) {
    await dedupeTabRef.value?.loadDedupeRecords()
  }

  prevPollMode = mode
  prevScrapePending = curPending
}, 2500)

onMounted(async () => {
  onResize()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', onResize)
  }
  try {
    await Promise.all([
      loadStatus(),
      loadRules(),
      organizeTabRef.value?.loadRecords(),
      scrapeTabRef.value?.loadTasks(),
      settingsTabRef.value?.loadSettings(),
      dedupeTabRef.value?.loadDedupeRules(),
      dedupeTabRef.value?.loadDedupeRecords(),
    ])
  } finally {
    pageLoading.value = false
  }
  mediaPoll.start()
})

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onResize)
  }
  mediaPoll.stop()
})
</script>
