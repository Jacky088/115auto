<template>
  <div class="settings-page">
    <el-card v-if="loadError" shadow="never" class="page-card">
      <el-result icon="warning" title="设置加载失败" :sub-title="loadError">
        <template #extra>
          <el-button type="primary" @click="load">重试</el-button>
        </template>
      </el-result>
    </el-card>

    <el-card v-if="loaded" shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <b>系统设置</b>
          <el-tag v-if="dirty" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
          <el-button v-if="dirty" type="primary" size="small" style="margin-left: 8px" @click="save">保存设置</el-button>
          <el-button size="small" style="margin-left: 8px" :loading="doctorLoading" @click="runDoctor">
            系统自检
          </el-button>
          <span style="margin-left: auto" class="tip">修改需点击「保存设置」后生效</span>
        </div>
      </template>

      <el-form label-position="top" class="settings-form">
        <div class="settings-flow">
          <TransferSettings
            ref="transferRef"
            :f="f"
            v-model:poll-preset="pollPreset"
            v-model:transfer-preset="transferPreset"
            v-model:retry-preset="retryPreset"
          />
          <ScheduleSettings :f="f" />
          <NotifySettings :f="f" @sync-f="syncF" />
          <WashSettings :f="f" @dirty="dirty = true" />
          <BackupSettings ref="backupRef" :f="f" />
          <ApiSettings :f="f" />
        </div>

        <div class="btn-row" style="margin-top: 10px">
          <el-button type="primary" :disabled="!dirty" @click="save">保存设置</el-button>
        </div>
      </el-form>
    </el-card>

    <!-- 未保存提醒浮动条：长表单滚到任何位置都能直接保存。
         放在 el-card 之外，避免卡片 backdrop-filter 成为 position:fixed 的包含块 -->
    <div v-if="loaded && dirty" class="save-dock">
      <span class="dock-hint">有未保存的更改</span>
      <el-button type="primary" size="small" @click="save">保存设置</el-button>
    </div>

    <!-- 系统自检弹窗 -->
    <el-dialog v-model="doctorOpen" title="系统自检" width="min(760px, 94vw)">
      <div v-if="doctor" class="toolbar" style="margin-bottom: 10px">
        <el-tag :type="doctorTagType(doctor.overall)" effect="dark">
          {{ doctorTagText(doctor.overall) }}
        </el-tag>
        <span class="tip">
          共 {{ doctor.checks.length }} 项：正常 {{ doctor.counts.ok }} / 警告 {{ doctor.counts.warn }} /
          失败 {{ doctor.counts.fail }} · 版本 {{ doctor.version }}
        </span>
      </div>
      <el-table :data="doctor ? doctor.checks : []" size="small" max-height="420">
        <el-table-column prop="name" label="检查项" width="150" />
        <el-table-column label="状态" width="88">
          <template #default="{ row }">
            <el-tag :type="doctorTagType(row.status)" size="small">{{ doctorTagText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="结果" min-width="260" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button :disabled="!doctor" @click="copyDoctor">复制报告</el-button>
        <el-button type="primary" :loading="doctorLoading" @click="runDoctor">重新检查</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import TransferSettings from '../components/settings/TransferSettings.vue'
import ScheduleSettings from '../components/settings/ScheduleSettings.vue'
import NotifySettings from '../components/settings/NotifySettings.vue'
import WashSettings from '../components/settings/WashSettings.vue'
import BackupSettings from '../components/settings/BackupSettings.vue'
import ApiSettings from '../components/settings/ApiSettings.vue'
import '../components/settings/settings.css'

const transferRef = ref(null)
const backupRef = ref(null)
const f = ref({})
const loaded = ref(false)
const loadError = ref('')

const retryPreset = ref('标准（1分/5分/30分）')
const pollPreset = ref('标准（30秒~2分）')
const transferPreset = ref('标准（1~3分）')
const dirty = ref(false)

let suppressWatch = false

const syncF = (patch) => {
  suppressWatch = true
  Object.assign(f.value, patch)
  nextTick(() => { suppressWatch = false })
}

// ---- 系统自检 ----
const doctorOpen = ref(false)
const doctorLoading = ref(false)
const doctor = ref(null)
const DOCTOR_TEXT = { ok: '正常', warn: '警告', fail: '失败', skip: '跳过' }
const doctorTagText = (s) => DOCTOR_TEXT[s] || s || ''
const doctorTagType = (s) => (s === 'ok' ? 'success' : s === 'warn' ? 'warning'
  : s === 'fail' ? 'danger' : 'info')
const runDoctor = async () => {
  doctorOpen.value = true
  doctorLoading.value = true
  try {
    doctor.value = (await api.get('/api/system/doctor')).data
  } catch (e) {
    ElMessage.error(errMsg(e) || '自检失败')
  } finally {
    doctorLoading.value = false
  }
}
const copyDoctor = async () => {
  const text = doctor.value && doctor.value.report
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } finally { ta.remove() }
  }
  ElMessage.success('自检报告已复制')
}

useUnsavedGuard(dirty)

watch([f, retryPreset, pollPreset, transferPreset], () => {
  if (!suppressWatch) dirty.value = true
}, { deep: true })

const load = async () => {
  suppressWatch = true
  loadError.value = ''
  try {
    f.value = (await api.get('/api/settings')).data
  } catch (e) {
    loadError.value = errMsg(e) || '设置加载失败'
    ElMessage.error(loadError.value)
    suppressWatch = false
    return
  }
  if (f.value.schedule_jitter_min === undefined) f.value.schedule_jitter_min = 5
  if (f.value.batch_rest_enabled === undefined) f.value.batch_rest_enabled = true
  if (f.value.batch_rest_count === undefined) f.value.batch_rest_count = 3
  if (f.value.batch_rest_min_min === undefined) f.value.batch_rest_min_min = 2
  if (f.value.batch_rest_max_min === undefined) f.value.batch_rest_max_min = 5
  if (f.value.health_check_interval_min === undefined) f.value.health_check_interval_min = 10
  if (f.value.dedup_cache_ttl_sec === undefined) f.value.dedup_cache_ttl_sec = 90
  if (!f.value.auto_backup_time) f.value.auto_backup_time = '04:00'

  // 回填三组预设
  const RETRY_PRESETS = [
    { label: '快速（30秒/2分/10分）', sec: [30, 120, 600] },
    { label: '标准（1分/5分/30分）', sec: [60, 300, 1800] },
    { label: '稳健（5分/30分/2小时）', sec: [300, 1800, 7200] },
  ]
  const POLL_PRESETS = [
    { label: '快速（15~45秒）', min: 15, max: 45 },
    { label: '标准（30秒~2分）', min: 30, max: 120 },
    { label: '稳健（3~10分）', min: 180, max: 600 },
  ]
  const TRANSFER_PRESETS = [
    { label: '快速（30秒~1分）', min: 30, max: 60 },
    { label: '标准（1~3分）', min: 60, max: 180 },
    { label: '稳健（3~10分）', min: 180, max: 600 },
  ]

  const hit = (arr, cur, pick) => {
    const m = arr.find((p) => pick(p).every((n, i) => n === cur[i]))
    return (m || arr[1]).label
  }
  retryPreset.value = hit(RETRY_PRESETS, f.value.retry_backoff || [], (p) => p.sec)
  pollPreset.value = hit(POLL_PRESETS, [f.value.poll_interval_min, f.value.poll_interval_max],
                         (p) => [p.min, p.max])
  transferPreset.value = hit(TRANSFER_PRESETS, [f.value.transfer_interval_min, f.value.transfer_interval_max],
                             (p) => [p.min, p.max])

  if (f.value.tg_mode && f.value.tg_mode !== 'polling') {
    transferRef.value?.loadUb?.()
  }
  backupRef.value?.loadBundleEstimate?.()
  loaded.value = true
  await nextTick()
  suppressWatch = false
  dirty.value = false
}

const validateForm = () => {
  // 1. 每日转存上限 (1 ~ 50)
  const dl = Number(f.value.daily_limit)
  if (!Number.isInteger(dl) || dl < 1 || dl > 50) {
    ElMessage.warning('每日转存上限必须是 1 ~ 50 之间的整数')
    return false
  }

  // 2. 分享体积下限 (>= 0)
  const ms = Number(f.value.min_size_gb)
  if (isNaN(ms) || ms < 0 || ms > 1000) {
    ElMessage.warning('分享体积下限需为 ≥0 的数字')
    return false
  }

  // 3. 定时计划与开工随机扰动
  if (f.value.schedule_enabled) {
    const sStart = String(f.value.schedule_start || '').trim()
    const sEnd = String(f.value.schedule_end || '').trim()
    if (!sStart || !sEnd) {
      ElMessage.warning('定时计划的开始时间与结束时间不能为空')
      return false
    }
    if (sStart === sEnd) {
      ElMessage.warning('计划起止时间不能相同')
      return false
    }
    const jm = Number(f.value.schedule_jitter_min)
    if (!Number.isInteger(jm) || jm < 0 || jm > 30) {
      ElMessage.warning('随机扰动需为 0~30 的整数')
      return false
    }
  }

  // 4. 拟人化连存小憩 (count 2~20, min 1~60, max 1~60, min <= max)
  if (f.value.batch_rest_enabled) {
    const rc = Number(f.value.batch_rest_count)
    if (!Number.isInteger(rc) || rc < 2 || rc > 20) {
      ElMessage.warning('连存小憩笔数必须是 2 ~ 20 之间的整数')
      return false
    }
    const rmin = Number(f.value.batch_rest_min_min)
    const rmax = Number(f.value.batch_rest_max_min)
    if (!Number.isInteger(rmin) || rmin < 1 || rmin > 60) {
      ElMessage.warning('最短小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      return false
    }
    if (!Number.isInteger(rmax) || rmax < 1 || rmax > 60) {
      ElMessage.warning('最长小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      return false
    }
    if (rmin > rmax) {
      ElMessage.warning(`小憩时间区间有误：最短小憩时间（${rmin} 分钟）不能大于最长小憩时间（${rmax} 分钟）`)
      return false
    }
  }

  // 5. 定时自动备份 (1 ~ 365, HH:MM, TG Bot 校验)
  if (f.value.auto_backup_enabled) {
    const token = String(f.value.notify_token || '').trim()
    const chatId = String(f.value.notify_chat_id || '').trim()
    if (!token || !chatId) {
      ElMessage.warning('开启「定时自动备份」必须先配置下方 Telegram Bot（Token 与 Chat ID），否则备份文件无法送达')
      return false
    }
    const bd = Number(f.value.auto_backup_days)
    if (!Number.isInteger(bd) || bd < 1 || bd > 365) {
      ElMessage.warning('自动备份间隔必须是 1 ~ 365 天之间的整数')
      return false
    }
    if (!f.value.auto_backup_time) {
      ElMessage.warning('请选择自动备份的推送时间')
      return false
    }
  }

  // 6. 洗版升级必须填写旧版归档目录
  if (f.value.wash_enabled) {
    const dir = String(f.value.wash_archive_dir || '').trim()
    if (!dir.startsWith('/') || dir.split('/').includes('..')) {
      ElMessage.warning('开启「洗版升级」需填写以 / 开头的旧版归档目录，如 /媒体库/洗版归档')
      return false
    }
  }

  return true
}

const save = async () => {
  if (!validateForm()) return
  const RETRY_PRESETS = [
    { label: '快速（30秒/2分/10分）', sec: [30, 120, 600] },
    { label: '标准（1分/5分/30分）', sec: [60, 300, 1800] },
    { label: '稳健（5分/30分/2小时）', sec: [300, 1800, 7200] },
  ]
  const POLL_PRESETS = [
    { label: '快速（15~45秒）', min: 15, max: 45 },
    { label: '标准（30秒~2分）', min: 30, max: 120 },
    { label: '稳健（3~10分）', min: 180, max: 600 },
  ]
  const TRANSFER_PRESETS = [
    { label: '快速（30秒~1分）', min: 30, max: 60 },
    { label: '标准（1~3分）', min: 60, max: 180 },
    { label: '稳健（3~10分）', min: 180, max: 600 },
  ]
  const backoff = (RETRY_PRESETS.find((p) => p.label === retryPreset.value) || RETRY_PRESETS[1]).sec
  const poll = (POLL_PRESETS.find((p) => p.label === pollPreset.value) || POLL_PRESETS[1])
  const trans = (TRANSFER_PRESETS.find((p) => p.label === transferPreset.value) || TRANSFER_PRESETS[1])
  try {
    const payload = { ...f.value }
    for (const k of Object.keys(payload)) {
      if (k.startsWith('auto_move_')) delete payload[k]
    }
    await api.put('/api/settings', {
      ...payload,
      retry_backoff: backoff,
      poll_interval_min: poll.min, poll_interval_max: poll.max,
      transfer_interval_min: trans.min, transfer_interval_max: trans.max,
    })
    dirty.value = false
    saveOk('设置已保存', '转存间隔、限额、开关等已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(load)
</script>
