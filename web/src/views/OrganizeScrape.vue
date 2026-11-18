<template>
  <div class="media-page" v-loading="pageLoading">
    <!-- 顶部系统运行状态联动警告 -->
    <el-alert
      v-if="!status.running"
      type="warning"
      show-icon
      :closable="false"
      class="mb16"
      title="系统未运行，请先点击顶栏「运行」"
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
      :title="`后台正在执行「${mediaModeLabel}」，单线程互斥锁生效中...`"
    />

    <!-- 主工作台：三大独立程序与设置标签页 -->
    <el-card shadow="never" class="page-card">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane name="organize">
          <template #label>
            <span>📁 目录整理</span>
            <el-badge
              v-if="(status.counts.records || 0) > 0"
              :value="status.counts.records"
              type="info"
              class="tab-badge"
            />
          </template>
          <OrganizeTab ref="organizeTabRef" />
        </el-tab-pane>

        <el-tab-pane name="scrape">
          <template #label>
            <span>🎬 媒体刮削</span>
            <el-badge
              v-if="(status.counts.need_confirm + status.counts.staged) > 0"
              :value="status.counts.need_confirm + status.counts.staged"
              class="tab-badge"
            />
          </template>
          <ScrapeTab ref="scrapeTabRef" />
        </el-tab-pane>

        <el-tab-pane name="dedupe">
          <template #label>
            <span>🧹 重复清理</span>
            <el-badge
              v-if="(status.counts.dedupe_rules || 0) > 0"
              :value="status.counts.dedupe_rules"
              type="info"
              class="tab-badge"
            />
          </template>
          <DedupeTab ref="dedupeTabRef" />
        </el-tab-pane>

        <el-tab-pane label="⚙️ 策略与设置" name="settings">
          <MediaSettingsTab ref="settingsTabRef" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <MediaRuleDialog ref="ruleDialogRef" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { api, errMsg, toast } from '../api'
import '../components/media/media.css'
import OrganizeTab from '../components/media/OrganizeTab.vue'
import ScrapeTab from '../components/media/ScrapeTab.vue'
import DedupeTab from '../components/media/DedupeTab.vue'
import MediaSettingsTab from '../components/media/MediaSettingsTab.vue'
import MediaRuleDialog from '../components/media/MediaRuleDialog.vue'

const activeTab = ref('organize')
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
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') return '正在暂停转存并消化现有队列'
  if (mp.phase === 'scanning') return '正在礼貌扫描 115 目录树'
  return '单队列顺序处理中'
})

const activeProgressLabel = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  const dp = status.value.gate.drain_progress || status.value.gate.drain_status || {}
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') {
    const rem = dp.remaining_total ?? dp.unfinished ?? '?'
    return `排队消化剩余转存（待消化 ${rem} 个，已等 ${dp.elapsed_sec ?? 0}s）`
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
  stopping.value = true
  try {
    const { data } = await api.post('/api/media/stop')
    toast.info(data.msg)
    await loadStatus()
  } catch (e) { toast.error(errMsg(e)) }
  finally { stopping.value = false }
}

async function toggleRuleEnabled(row, val) {
  try {
    await api.put(`/api/media/rules/${row.id}`, {
      ...row,
      organize_enabled: !!row.organize_enabled,
      scrape_enabled: !!row.scrape_enabled,
      enabled: !!val,
    })
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function delRule(id) {
  try {
    await api.delete(`/api/media/rules/${id}`)
    toast.success('已删除规则')
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

let pollTimer = null
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
  pollTimer = setInterval(async () => {
    await loadStatus()
    if (activeTab.value === 'organize' && (status.value.gate.active_mode === 'organize' || !organizeTabRef.value?.hasRecords())) {
      await organizeTabRef.value?.loadRecords()
    } else if (activeTab.value === 'scrape') {
      await scrapeTabRef.value?.loadTasks()
    } else if (activeTab.value === 'dedupe') {
      if (status.value.gate.active_mode === 'dedupe' || !dedupeTabRef.value?.hasRecords()) {
        await dedupeTabRef.value?.loadDedupeRecords()
      }
    }
  }, 2500)
})

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onResize)
  }
  if (pollTimer) clearInterval(pollTimer)
})
</script>
