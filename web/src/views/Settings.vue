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
        <div class="settings-toolbar">
          <div class="toolbar-left">
            <span class="settings-title">系统设置</span>
            <el-tag v-if="dirty" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
            <el-button v-if="dirty" type="primary" size="small" @click="save">保存设置</el-button>
            <el-button size="small" class="doctor-btn" :loading="doctorLoading" @click="onDoctorClick">系统自检</el-button>
            <span class="tip pc-only-tip" style="margin-left: 4px">修改需点击「保存设置」后生效</span>
          </div>

          <div class="toolbar-right">
            <el-autocomplete
              v-model="searchQuery"
              :fetch-suggestions="querySearch"
              placeholder="🔍 搜索设置项（如 Bark、重试、备份...）"
              clearable
              size="small"
              class="settings-search-input"
              popper-class="settings-search-popper"
              @select="onSearchSelect"
            >
              <template #default="scope">
                <div v-if="scope && scope.item" class="search-item">
                  <span class="search-label">{{ scope.item.value }}</span>
                  <el-tag size="small" effect="light" class="search-tab-tag">{{ tabLabel(scope.item.tab) }}</el-tag>
                </div>
              </template>
            </el-autocomplete>
          </div>
        </div>
      </template>

      <el-form label-position="top" class="settings-form" autocomplete="off" @submit.prevent>
        <!-- 阻止浏览器密码管理器将设置页首个输入框（TG 代理）误认为登录用户名 -->
        <div style="position: absolute; top: -9999px; left: -9999px; width: 0; height: 0; overflow: hidden;" aria-hidden="true">
          <input type="text" name="fake_username_remember" tabindex="-1" autocomplete="username" />
          <input type="password" name="fake_password_remember" tabindex="-1" autocomplete="current-password" />
        </div>
        <el-tabs v-model="activeTab" class="settings-tabs" @tab-change="onTabChange">
          <el-tab-pane name="transfer">
            <template #label><span class="tab-label"><el-icon><Refresh /></el-icon><span>转存控制</span></span></template>
            <TransferSettings
              ref="transferRef"
              :f="f"
              v-model:poll-preset="pollPreset"
              v-model:transfer-preset="transferPreset"
              v-model:retry-preset="retryPreset"
            />
          </el-tab-pane>

          <el-tab-pane name="notify">
            <template #label><span class="tab-label"><el-icon><Bell /></el-icon><span>消息通知</span></span></template>
            <NotifySettings :f="f" @sync-f="syncF" />
          </el-tab-pane>

          <el-tab-pane name="schedule">
            <template #label><span class="tab-label"><el-icon><Timer /></el-icon><span>风控管理</span></span></template>
            <ScheduleSettings :f="f" @dirty="onChildDirty" />
          </el-tab-pane>

          <el-tab-pane name="wash">
            <template #label><span class="tab-label"><el-icon><MagicStick /></el-icon><span>洗版升级</span></span></template>
            <WashSettings :f="f" @dirty="onChildDirty" />
          </el-tab-pane>

          <el-tab-pane name="backup">
            <template #label><span class="tab-label"><el-icon><FolderChecked /></el-icon><span>备份恢复</span></span></template>
            <BackupSettings ref="backupRef" :f="f" @dirty="onChildDirty" />
          </el-tab-pane>

          <el-tab-pane name="api">
            <template #label><span class="tab-label"><el-icon><Key /></el-icon><span>开放接口</span></span></template>
            <ApiSettings :f="f" />
          </el-tab-pane>
        </el-tabs>

        <div class="btn-row" style="margin-top: 16px">
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
    <el-dialog v-model="doctorOpen" title="系统自检" width="min(760px, calc(100vw - 36px))" align-center>
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
        <el-button type="primary" :loading="doctorLoading" @click="executeDoctor">重新检查</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { confirmAction } from '../composables/confirmDialog'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import { usePersistedTab } from '../composables/usePersistedTab'
import TransferSettings from '../components/settings/TransferSettings.vue'
import ScheduleSettings from '../components/settings/ScheduleSettings.vue'
import NotifySettings from '../components/settings/NotifySettings.vue'
import WashSettings from '../components/settings/WashSettings.vue'
import BackupSettings from '../components/settings/BackupSettings.vue'
import ApiSettings from '../components/settings/ApiSettings.vue'
import '../components/settings/settings.css'

const route = useRoute()
const transferRef = ref(null)
const backupRef = ref(null)
const f = ref({})
const loaded = ref(false)
const loadError = ref('')

const TAB_LABELS = {
  transfer: '转存控制',
  notify: '消息通知',
  schedule: '风控管理',
  wash: '洗版升级',
  backup: '备份恢复',
  api: '开放接口',
}
const tabLabel = (tab) => TAB_LABELS[tab] || tab

const activeTab = usePersistedTab('settings', Object.keys(TAB_LABELS), 'transfer', route)
const searchQuery = ref('')

const retryPreset = ref('标准（1分/5分/30分）')
const pollPreset = ref('标准（30秒~2分）')
const transferPreset = ref('标准（1~3分）')
const dirty = ref(false)

let suppressWatch = false

const SEARCH_INDEX = [
  // 转存控制 (transfer)
  { value: '转存总开关与云下载总开关', tab: 'transfer', keywords: ['开关', '转存', '离线', '云下载', 'ed2k', '磁力'] },
  { value: 'Telegram 监听模式 (网页轮询 / Userbot)', tab: 'transfer', keywords: ['tg', '监听', '轮询', 'userbot', '模式', '频道'] },
  { value: '网络代理设置 (HTTP / SOCKS5)', tab: 'transfer', keywords: ['代理', 'proxy', 'socks5', 'http', '梯子'] },
  { value: '轮询检索间隔', tab: 'transfer', keywords: ['轮询', '间隔', '秒', '分钟', '频控'] },
  { value: '转存与云下载执行间隔', tab: 'transfer', keywords: ['转存', '云下载', '执行', '延迟', '间隔', '频控'] },
  { value: '失败重试退避阶梯 (60s/300s/1800s)', tab: 'transfer', keywords: ['重试', '失败', '退避', '报错', '阶梯'] },
  { value: '115 空间不足自动暂停', tab: 'transfer', keywords: ['空间', '暂停', '门禁', '门闸', '熔断', 'gb', 'tb', '容量'] },
  { value: '每日转存上限 (1~50 笔/天)', tab: 'transfer', keywords: ['每日', '限额', '上限', '额度', '50'] },
  { value: '分享体积下限 (过滤微小资源)', tab: 'transfer', keywords: ['体积', '下限', '过滤', '小文件', 'gb'] },
  { value: '拟人化连存小憩 (防风控休息)', tab: 'transfer', keywords: ['小憩', '休息', '连存', '防风控', '笔数', '时间'] },

  // 消息通知 (notify)
  { value: 'Telegram Bot 推送与测试', tab: 'notify', keywords: ['tg', 'telegram', 'bot', 'token', 'chat_id', '通知', '推送', '测试'] },
  { value: 'Bark (iOS) 苹果设备通知', tab: 'notify', keywords: ['bark', 'ios', '苹果', 'iphone', '推送', 'key', '设备'] },
  { value: 'PushPlus (推送加) 微信通知', tab: 'notify', keywords: ['pushplus', '推送加', '微信', 'token'] },
  { value: 'ntfy 自建/公共主题推送', tab: 'notify', keywords: ['ntfy', 'topic', '自建', '主题', '推送'] },
  { value: '通用 Webhook 推送 (企业微信 / 钉钉 / 飞书)', tab: 'notify', keywords: ['webhook', '企业微信', '钉钉', '飞书', '推送', '模板'] },
  { value: '每日转存摘要报告 (日报推送时间)', tab: 'notify', keywords: ['每日', '摘要', '日报', '统计', '定时', '推送时间'] },

  // 风控管理 (schedule)
  { value: '定时计划与避让时间段 (静默防风控)', tab: 'schedule', keywords: ['定时', '计划', '避让', '深夜', '时间段', '起止', '跨零点'] },
  { value: '开工随机扰动 (Jitter 随机微差)', tab: 'schedule', keywords: ['扰动', '随机', 'jitter', '微差', '防封'] },
  { value: '运行健康巡检间隔（云下载任务检查）', tab: 'schedule', keywords: ['健康', '巡检', '离线', '检查', '间隔', '云下载'] },
  { value: '同名去重缓存时间 (秒)', tab: 'schedule', keywords: ['去重', '缓存', 'ttl', '秒'] },

  // 追更洗版 (wash)
  { value: '分享追更模式 (同一链接增量转存)', tab: 'wash', keywords: ['追更', '增量', '分享', '同链接', '集数', '加更'] },
  { value: '按文件名同名去重', tab: 'wash', keywords: ['去重', '同名', '查重', '文件名'] },
  { value: '智能画质洗版升级 (高画质替换低画质)', tab: 'wash', keywords: ['洗版', '画质', '4k', '1080p', '升级', '替换', '优质'] },
  { value: '旧版文件归档目录（移入 115 回收站，可手动还原）', tab: 'wash', keywords: ['旧版', '归档', '回收站', '删除', '目录', '还原'] },

  // 备份恢复 (backup)
  { value: '定时自动备份至 Telegram Bot / WebDAV', tab: 'backup', keywords: ['自动备份', '定时', '计划', '天数', '时间', 'bot'] },
  { value: 'WebDAV 云端备份与恢复', tab: 'backup', keywords: ['webdav', '云端', '坚果云', 'alist', '备份', '恢复'] },
  { value: '导出轻量配置备份 (.json)', tab: 'backup', keywords: ['导出', '配置', '备份', 'json', '下载'] },
  { value: '导出全量迁移包 (含媒体刮削与台账)', tab: 'backup', keywords: ['全量', '迁移包', '导出', '换机', '换服务器', 'bundle'] },
  { value: '恢复历史备份或全量迁移包', tab: 'backup', keywords: ['恢复', '还原', '导入', '上传', '覆盖'] },

  // 开放接口 (api)
  { value: '对外 API Token (开放转存接口)', tab: 'api', keywords: ['api', 'token', '开放', '接口', '外部', '密钥', '第三方', '调用'] },
]

const querySearch = (queryString, cb) => {
  if (!queryString || !queryString.trim()) {
    cb([])
    return
  }
  const q = queryString.trim().toLowerCase()
  const results = SEARCH_INDEX.filter((item) => {
    return (
      item.value.toLowerCase().includes(q) ||
      item.keywords.some((kw) => kw.includes(q))
    )
  })
  cb(results)
}

const onSearchSelect = (item) => {
  if (!item) return
  activeTab.value = item.tab
  nextTick(() => {
    searchQuery.value = ''
    const el = document.querySelector(`.settings-page .${item.tab}-settings-group`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      el.classList.add('tab-pane-highlight')
      setTimeout(() => el.classList.remove('tab-pane-highlight'), 1800)
    }
  })
}

const onTabChange = () => {
  // 保持组件内部响应式状态切换，不劫持修改 window.location.hash，避免与 Hash 路由发生冲突
}

const syncF = (patch) => {
  suppressWatch = true
  Object.assign(f.value, patch)
  nextTick(() => {
    baselineSnapshot = takeSnapshot()
    dirty.value = false
    suppressWatch = false
  })
}

const onChildDirty = () => {
  dirty.value = true
}

// ---- 系统自检 ----
const doctorOpen = ref(false)
const doctorLoading = ref(false)
const doctor = ref(null)
const DOCTOR_TEXT = { ok: '正常', warn: '警告', fail: '失败', skip: '跳过' }
const doctorTagText = (s) => DOCTOR_TEXT[s] || s || ''
const doctorTagType = (s) => (s === 'ok' ? 'success' : s === 'warn' ? 'warning'
  : s === 'fail' ? 'danger' : 'info')
const executeDoctor = async () => {
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

const onDoctorClick = async () => {
  const ok = await confirmAction({
    title: '是否执行系统自检？',
    message: '将对数据库、磁盘与 115 容量、账号登录及队列状态进行只读检查，并生成脱敏诊断报告（不修改任何数据）。',
    confirmText: '开始自检',
    cancelText: '取消',
    type: 'info',
  })
  if (!ok) return
  await executeDoctor()
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

useUnsavedGuard(dirty, () => {
  dirty.value = false
})

let baselineSnapshot = ''

const takeSnapshot = () => JSON.stringify({
  f: f.value,
  retry: retryPreset.value,
  poll: pollPreset.value,
  trans: transferPreset.value,
})

const checkDirty = () => {
  if (suppressWatch || !baselineSnapshot) return
  dirty.value = takeSnapshot() !== baselineSnapshot
}

watch([f, retryPreset, pollPreset, transferPreset], checkDirty, { deep: true })

const load = async () => {
  suppressWatch = true
  loadError.value = ''
  try {
    const raw = (await api.get('/api/settings')).data || {}
    
    // 强类型安全归一化，防止 Element Plus 挂载后隐式转换触发 watch 误判 dirty
    f.value = {
      ...raw,
      daily_limit: Number(raw.daily_limit || 50),
      min_size_gb: Number(raw.min_size_gb ?? 0.2),
      schedule_jitter_min: Number(raw.schedule_jitter_min ?? 5),
      batch_rest_count: Number(raw.batch_rest_count ?? 3),
      batch_rest_min_min: Number(raw.batch_rest_min_min ?? 2),
      batch_rest_max_min: Number(raw.batch_rest_max_min ?? 5),
      health_check_interval_min: Number(raw.health_check_interval_min ?? 10),
      dedup_cache_ttl_sec: Number(raw.dedup_cache_ttl_sec ?? 90),
      auto_backup_days: Number(raw.auto_backup_days ?? 7),
      auto_backup_time: raw.auto_backup_time || '04:00',
      auto_backup_channels: Array.isArray(raw.auto_backup_channels)
        ? raw.auto_backup_channels
        : (raw.auto_backup_channels ? String(raw.auto_backup_channels).split(',').map(s => s.trim()).filter(Boolean) : []),
      bundle_meta_mode: raw.bundle_meta_mode || 'nfo',
      bundle_slim_db: raw.bundle_slim_db !== undefined ? !!raw.bundle_slim_db : true,
      bundle_include_credentials: raw.bundle_include_credentials !== undefined ? !!raw.bundle_include_credentials : false,
      bundle_export_dest: raw.bundle_export_dest || 'local',
      bundle_restore_source: raw.bundle_restore_source || 'local',
      bundle_restore_credentials: raw.bundle_restore_credentials !== undefined ? !!raw.bundle_restore_credentials : false,
      bundle_restore_meta: raw.bundle_restore_meta !== undefined ? !!raw.bundle_restore_meta : true,
      batch_rest_enabled: raw.batch_rest_enabled !== undefined ? !!raw.batch_rest_enabled : true,
    }

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
      if (!Array.isArray(cur)) return arr[1].label
      const m = arr.find((p) => pick(p).every((n, i) => n === cur[i]))
      return (m || arr[1]).label
    }
    retryPreset.value = hit(RETRY_PRESETS, f.value.retry_backoff || [], (p) => p.sec)
    pollPreset.value = hit(POLL_PRESETS, [f.value.poll_interval_min, f.value.poll_interval_max],
                           (p) => [p.min, p.max])
    transferPreset.value = hit(TRANSFER_PRESETS, [f.value.transfer_interval_min, f.value.transfer_interval_max],
                               (p) => [p.min, p.max])

    loaded.value = true
    await nextTick()
    // 挂载稳定后锁定基准快照
    baselineSnapshot = takeSnapshot()
    dirty.value = false
    suppressWatch = false
  } catch (e) {
    loadError.value = errMsg(e) || '设置加载失败'
    ElMessage.error(loadError.value)
    suppressWatch = false
  }
}

const validateForm = () => {
  // 1. 每日转存上限 (1 ~ 50)
  const dl = Number(f.value.daily_limit)
  if (!Number.isInteger(dl) || dl < 1 || dl > 50) {
    ElMessage.warning('每日转存上限必须是 1 ~ 50 之间的整数')
    activeTab.value = 'transfer'
    return false
  }

  // 2. 分享体积下限 (>= 0)
  const ms = Number(f.value.min_size_gb)
  if (isNaN(ms) || ms < 0 || ms > 1000) {
    ElMessage.warning('分享体积下限需为 ≥0 的数字')
    activeTab.value = 'transfer'
    return false
  }

  // 3. 定时计划与开工随机扰动
  if (f.value.schedule_enabled) {
    const sStart = String(f.value.schedule_start || '').trim()
    const sEnd = String(f.value.schedule_end || '').trim()
    if (!sStart || !sEnd) {
      ElMessage.warning('定时计划的开始时间与结束时间不能为空')
      activeTab.value = 'schedule'
      return false
    }
    if (sStart === sEnd) {
      ElMessage.warning('计划起止时间不能相同')
      activeTab.value = 'schedule'
      return false
    }
    const jm = Number(f.value.schedule_jitter_min)
    if (!Number.isInteger(jm) || jm < 0 || jm > 30) {
      ElMessage.warning('随机扰动需为 0~30 的整数')
      activeTab.value = 'schedule'
      return false
    }
  }

  // 4. 拟人化连存小憩 (count 2~20, min 1~60, max 1~60, min <= max)
  if (f.value.batch_rest_enabled) {
    const rc = Number(f.value.batch_rest_count)
    if (!Number.isInteger(rc) || rc < 2 || rc > 20) {
      ElMessage.warning('连存小憩笔数必须是 2 ~ 20 之间的整数')
      activeTab.value = 'schedule'
      return false
    }
    const rmin = Number(f.value.batch_rest_min_min)
    const rmax = Number(f.value.batch_rest_max_min)
    if (!Number.isInteger(rmin) || rmin < 1 || rmin > 60) {
      ElMessage.warning('最短小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      activeTab.value = 'schedule'
      return false
    }
    if (!Number.isInteger(rmax) || rmax < 1 || rmax > 60) {
      ElMessage.warning('最长小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      activeTab.value = 'schedule'
      return false
    }
    if (rmin > rmax) {
      ElMessage.warning(`小憩时间区间有误：最短小憩时间（${rmin} 分钟）不能大于最长小憩时间（${rmax} 分钟）`)
      activeTab.value = 'schedule'
      return false
    }
  }

  // 5. 定时自动备份 (1 ~ 365, HH:MM, 途径与 TG Bot 校验)
  if (f.value.auto_backup_enabled) {
    const channels = Array.isArray(f.value.auto_backup_channels)
      ? f.value.auto_backup_channels
      : (typeof f.value.auto_backup_channels === 'string' && f.value.auto_backup_channels.trim()
          ? f.value.auto_backup_channels.split(',').map((x) => x.trim()).filter(Boolean)
          : [])
    if (channels.length === 0) {
      ElMessage.warning('开启「定时自动备份」需至少选择一种备份途径（Telegram Bot / WebDAV / 本地存储）')
      activeTab.value = 'backup'
      return false
    }
    if (channels.includes('tgbot')) {
      const token = String(f.value.notify_token || '').trim()
      const chatId = String(f.value.notify_chat_id || '').trim()
      if (!token || !chatId) {
        ElMessage.warning('定时自动备份已勾选 Telegram Bot 途径，请先前往「消息通知」配置 Bot Token 与 Chat ID')
        activeTab.value = 'notify'
        return false
      }
    }
    const bd = Number(f.value.auto_backup_days)
    if (!Number.isInteger(bd) || bd < 1 || bd > 365) {
      ElMessage.warning('自动备份间隔必须是 1 ~ 365 天之间的整数')
      activeTab.value = 'backup'
      return false
    }
    if (!f.value.auto_backup_time) {
      ElMessage.warning('请选择自动备份的推送时间')
      activeTab.value = 'backup'
      return false
    }
  }

  // 6. 洗版升级必须填写旧版归档目录
  if (f.value.wash_enabled) {
    const dir = String(f.value.wash_archive_dir || '').trim()
    if (!dir || dir === '/' || !dir.startsWith('/') || dir.split('/').includes('..')) {
      ElMessage.warning('开启「洗版升级」必须先选择旧版归档目录（不能为根目录 /）')
      activeTab.value = 'wash'
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
    baselineSnapshot = takeSnapshot()
    dirty.value = false
    saveOk('设置已保存', '转存间隔、限额、开关等已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(load)
</script>
