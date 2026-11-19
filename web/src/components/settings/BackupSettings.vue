<template>
  <div class="backup-settings-group">
    <!-- 定时自动备份 -->
    <div class="section-card">
      <div class="section-title">定时自动备份</div>
      <el-form-item label="自动备份">
        <div class="switch-row">
          <el-switch v-model="f.auto_backup_enabled" />
          <span class="tip">开启后定时把核心设置/业务规则/转存与整理台账打包推送到 TG Bot（超 8MB 自动 Gzip 压缩），可直接发回 Bot 或在本页恢复</span>
        </div>
      </el-form-item>
      <el-form-item label="备份间隔">
        <div class="range-row">
          <el-input-number
            v-model="f.auto_backup_days"
            :min="1"
            :max="365"
            :step="1"
            :step-strictly="true"
            :precision="0"
            :disabled="!f.auto_backup_enabled"
          />
          <span class="tip">天（1~365 天）</span>
          <template v-if="f.auto_backup_enabled">
            <span class="tip">推送时间：</span>
            <el-time-select
              v-model="f.auto_backup_time"
              start="00:00"
              step="00:30"
              end="23:30"
              placeholder="选择时间"
              style="width: 120px"
            />
            <span class="tip range-tip">周期到达当天在此时间推送（默认 04:00，推荐凌晨闲时备份）</span>
          </template>
          <span v-else class="warn-text">（需先开启上方「自动备份」总开关）</span>
        </div>
      </el-form-item>
      <el-form-item label="备份数据">
        <div class="range-row" style="width: 100%">
          <el-button @click="backupExport">导出数据备份</el-button>
        </div>
        <div class="tip" style="width: 100%">
          备份含设置/频道/规则/路由/黑名单/订阅/转存记录/自动整理/整理刮削/操作账本及待刮削/跳过任务（JSON，不含登录密码、Bot Token 等敏感凭据与本地海报目录）
        </div>
      </el-form-item>
      <el-form-item label="从备份恢复">
        <div class="range-row" style="width: 100%">
          <input
            ref="restoreInput"
            type="file"
            accept=".json,.gz,.zip,application/json,application/gzip,application/x-gzip,application/zip"
            style="display: none"
            @change="onRestoreFile"
          />
          <el-button @click="restoreInput?.click()">选择备份文件（JSON / GZ）</el-button>
          <span v-if="restoreFile" class="selected-count">
            {{ restoreFile.name }}（{{ Math.max(1, Math.round(restoreFile.size / 1024)) }} KB）
          </span>
          <el-button
            type="danger"
            plain
            :loading="restoring"
            :disabled="!restoreFile"
            @click="doRestore"
          >
            恢复数据
          </el-button>
        </div>
        <div class="tip" style="width: 100%">
          支持网页导出的 <code>.json</code> 与 TG 自动备份的 <code>.json.gz</code>（及 <code>.zip</code>），将<b class="warn-text">整体覆盖</b>上述全部业务数据
          （登录密码与通知密钥保留不变；新版备份可恢复待刮削/跳过任务，旧版备份会清空旧刮削队列，恢复后系统回到停止状态）。也可把备份文件私聊发给 Bot，回复 <code>/restore confirm</code> 恢复
        </div>
      </el-form-item>
    </div>

    <!-- 整机迁移包（ZIP · 跨机完整迁移） -->
    <div class="section-card">
      <div class="section-title">整机迁移包（ZIP · 跨机完整迁移）</div>
      <el-form-item label="体积预估">
        <div class="bundle-est-box">
          <div class="range-row" style="justify-content: space-between">
            <span v-if="bundleEst" class="bundle-est-metrics">
              数据库 <b>{{ fmtSize(bundleEst.db_bytes) }}</b> ·
              待刮削 <b>{{ bundleEst.pending_media_tasks }}</b>/{{ bundleEst.total_media_tasks }} 条 ·
              NFO <b>{{ bundleEst.meta_nfo_count }}</b> 个（{{ fmtSize(bundleEst.meta_nfo_bytes) }}）·
              海报图 <b>{{ bundleEst.meta_asset_count }}</b> 个（{{ fmtSize(bundleEst.meta_asset_bytes) }}）·
              凭据 <b>{{ bundleEst.credentials_count }}</b> 项
            </span>
            <span v-else class="tip">点击刷新获取当前数据与本地海报体积估算</span>
            <el-button size="small" :loading="estLoading" @click="loadBundleEstimate">刷新估算</el-button>
          </div>
          <div v-if="bundleEst?.meta_root" class="tip" style="margin-top: 4px">
            当前本地元数据目录：<code>{{ bundleEst.meta_root }}</code>
          </div>
        </div>
      </el-form-item>
      <el-form-item label="导出迁移包">
        <div class="bundle-opts">
          <div class="range-row">
            <span class="bundle-label">本地元数据：</span>
            <el-radio-group v-model="bundleOpt.meta_mode" class="bundle-capsule-radios">
              <el-radio-button value="nfo">仅 .nfo（推荐）</el-radio-button>
              <el-radio-button value="all">完整（含海报/背景图）</el-radio-button>
              <el-radio-button value="none">不含元数据</el-radio-button>
            </el-radio-group>
          </div>
          <div class="bundle-switch-group">
            <label class="bundle-check">
              <el-switch v-model="bundleOpt.slim_db" />
              <span>瘦身数据库副本（剥离日志/TMDB 缓存并压缩，不影响当前运行库）</span>
            </label>
            <label class="bundle-check">
              <el-switch v-model="bundleOpt.include_credentials" />
              <span>包含凭据与 Session（115 Cookie / TG Session / 密钥 / 登录密码，导出需验证密码）</span>
            </label>
          </div>
          <div class="range-row" style="margin-top: 2px">
            <el-button type="primary" :loading="bundleExporting" @click="exportBundle">导出迁移包（.zip）</el-button>
            <span v-if="bundleEst" class="tip">
              当前所选内容估算原始体积约 <b>{{ fmtSize(estBundleBytes) }}</b>（ZIP 下载时流式生成，不占额外内存）
            </span>
          </div>
          <div v-if="bundleOpt.include_credentials" class="tip warn-text">
            安全提示：含凭据的迁移包等同于完整账号与登录权限，请勿上传至公网网盘或分享给他人。
          </div>
        </div>
      </el-form-item>
      <el-form-item label="从迁移包恢复">
        <div class="bundle-opts">
          <div class="range-row">
            <input
              ref="bundleInput"
              type="file"
              accept=".zip,application/zip"
              style="display: none"
              @change="onBundleFile"
            />
            <el-button @click="bundleInput?.click()">选择迁移包（.zip）</el-button>
            <span v-if="bundleFile" class="selected-count">
              {{ bundleFile.name }}（{{ fmtSize(bundleFile.size) }}）
            </span>
            <el-button
              type="danger"
              plain
              :loading="bundleRestoring"
              :disabled="!bundleFile"
              @click="doBundleRestore"
            >
              预览并恢复迁移包
            </el-button>
          </div>
          <div class="bundle-switch-group">
            <label class="bundle-check">
              <el-switch v-model="bundleRestoreOpt.restore_credentials" />
              <span>覆盖恢复凭据与 Session（仅在压缩包内含凭据时生效；关闭则保留本机密码与登录态）</span>
            </label>
            <label class="bundle-check">
              <el-switch v-model="bundleRestoreOpt.restore_meta" />
              <span>还原本地元数据（.nfo 与海报图片，并自动迁移路径到本机元信息目录）</span>
            </label>
          </div>
          <div v-if="bundleRestoring" class="bundle-progress-wrap">
            <div class="tip">{{ bundlePhase }}</div>
            <el-progress :percentage="bundleProgress" :stroke-width="14" />
          </div>
          <div class="tip">
            支持最大 <b>2 GB</b> 整机迁移包流式上传（导入前需先在顶部停机）；恢复后本机任务回到停止状态，若还原了密钥将自动刷新当前登录令牌。
          </div>
        </div>
      </el-form-item>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, saveOk } from '../../api'

defineProps({
  f: {
    type: Object,
    required: true,
  },
})

// ---- 备份恢复 ----
const restoreInput = ref(null)
const restoreFile = ref(null)
const restoring = ref(false)

const onRestoreFile = (ev) => {
  const file = ev.target.files?.[0]
  restoreFile.value = null
  if (!file) return
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('文件超过 20MB 上限，无法作为备份导入')
    return
  }
  restoreFile.value = file
  ev.target.value = ''
}

const fmtBackupCounts = (s) => (
  `频道 ${s.channels || 0}、规则 ${s.rules || 0}、路由 ${s.routes || 0}、` +
  `黑名单 ${s.blacklist || 0}、订阅 ${s.subscriptions || 0}、转存 ${s.transfers || 0}、` +
  `自动整理 ${s.auto_move_rules || 0}、整理刮削 ${s.media_rules || 0}、操作账本 ${s.organized_records || 0}` +
  (s.media_tasks != null ? `、待刮削任务 ${s.media_tasks}` : '')
)

const extractBlobErr = async (e) => {
  const d = e?.response?.data
  if (d instanceof Blob) {
    try {
      const txt = await d.text()
      const parsed = JSON.parse(txt)
      if (parsed?.detail) return String(parsed.detail)
    } catch { /* ignore */ }
  }
  return errMsg(e)
}

const backupExport = async () => {
  try {
    const resp = await api.get('/api/backup/export', { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    a.download = m ? m[1] : 'autosave_backup.json'
    a.click()
    URL.revokeObjectURL(url)
    saveOk('备份已导出', '包含设置/频道/规则/路由/黑名单/订阅/转存记录/自动整理/整理刮削/操作账本及待刮削/跳过任务')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const doRestore = async () => {
  const file = restoreFile.value
  if (!file) return
  restoring.value = true
  try {
    const head = new Uint8Array(await file.slice(0, 2).arrayBuffer())
    const lowerName = (file.name || '').toLowerCase()
    const isCompressed = (
      lowerName.endsWith('.gz') || lowerName.endsWith('.json.gz') || lowerName.endsWith('.zip')
      || (head[0] === 0x1f && head[1] === 0x8b)
      || (head[0] === 0x50 && head[1] === 0x4b)
    )
    const kb = Math.max(1, Math.round(file.size / 1024))
    let summary
    let rawBuf = null
    let jsonData = null
    if (isCompressed) {
      rawBuf = await file.arrayBuffer()
      const { data: prev } = await api.post('/api/backup/preview', rawBuf, {
        timeout: 60000,
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Backup-Filename': encodeURIComponent(file.name || 'backup.json.gz'),
        },
      })
      summary = prev.summary
    } else {
      const text = await file.text()
      try { jsonData = JSON.parse(text) } catch { throw new Error('文件不是合法 JSON') }
      if (!jsonData?._meta || !jsonData.settings) throw new Error('不是本程序的备份文件（缺少备份标记）')
      summary = {
        exported_at: jsonData._meta.exported_at || '未知时间',
        settings: Object.keys(jsonData.settings || {}).length,
        channels: (jsonData.channels || []).length,
        rules: (jsonData.rules || []).length,
        routes: (jsonData.routes || []).length,
        blacklist: (jsonData.blacklist || []).length,
        subscriptions: (jsonData.subscriptions || []).length,
        transfers: (jsonData.transfers || []).length,
        auto_move_rules: (jsonData.auto_move_rules || []).length,
        media_rules: (jsonData.media_rules || []).length,
        organized_records: (jsonData.organized_records || []).length,
        media_tasks: Array.isArray(jsonData.media_tasks) ? jsonData.media_tasks.length : 0,
      }
    }
    await ElMessageBox.confirm(
      `备份导出于 ${summary.exported_at || '未知时间'}（${kb} KB），包含：` +
      `设置 ${summary.settings || 0} 项、${fmtBackupCounts(summary)}。` +
      `恢复将覆盖当前全部业务数据（登录密码与通知密钥不变；新版备份含待刮削任务，旧版备份会清空现有队列）。恢复后系统将回到停止状态。`,
      '恢复备份？', { confirmButtonText: '恢复', cancelButtonText: '取消', type: 'warning', confirmButtonClass: 'danger-confirm-btn' })
    const { data: resp } = isCompressed
      ? await api.post('/api/backup/restore', rawBuf, {
          timeout: 120000,
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-Backup-Filename': encodeURIComponent(file.name || 'backup.json.gz'),
          },
        })
      : await api.post('/api/backup/restore', jsonData, { timeout: 120000 })
    await ElMessageBox.alert(
      `恢复完成：设置 ${resp.stats.settings} 项、${fmtBackupCounts(resp.stats)}。` +
      `页面即将刷新以加载恢复后的数据。`, '恢复成功', { confirmButtonText: '刷新页面' })
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(errMsg(e))
  } finally {
    restoring.value = false
  }
}

// ---- 整机迁移包 ----
const bundleEst = ref(null)
const estLoading = ref(false)
const bundleExporting = ref(false)
const bundleOpt = reactive({
  meta_mode: 'nfo',
  slim_db: true,
  include_credentials: false,
})
const bundleInput = ref(null)
const bundleFile = ref(null)
const bundleRestoring = ref(false)
const bundlePhase = ref('')
const bundleProgress = ref(0)
const bundleRestoreOpt = reactive({
  restore_credentials: false,
  restore_meta: true,
})
const MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024

const loadBundleEstimate = async () => {
  estLoading.value = true
  try {
    const { data } = await api.get('/api/backup/bundle/estimate')
    bundleEst.value = data
  } catch { /* ignore */ } finally {
    estLoading.value = false
  }
}

const estBundleBytes = computed(() => {
  const e = bundleEst.value
  if (!e) return 0
  let total = e.db_bytes || 0
  if (bundleOpt.include_credentials) total += e.credentials_bytes || 0
  if (bundleOpt.meta_mode === 'nfo') total += e.meta_nfo_bytes || 0
  else if (bundleOpt.meta_mode === 'all') total += (e.meta_nfo_bytes || 0) + (e.meta_asset_bytes || 0)
  return total
})

const exportBundle = async () => {
  let password = ''
  try {
    if (bundleOpt.include_credentials) {
      const res = await ElMessageBox.prompt(
        '迁移包将包含 115 Cookie、TG Session、加密密钥与 Web 登录密码哈希。请输入当前登录密码确认导出：',
        '安全验证',
        {
          inputType: 'password',
          confirmButtonText: '验证并导出',
          cancelButtonText: '取消',
          inputPlaceholder: '请输入当前 Web 登录密码',
        },
      )
      password = (res?.value || '').trim()
      if (!password) {
        ElMessage.warning('请输入当前登录密码后再导出含凭据迁移包')
        return
      }
    }
    bundleExporting.value = true
    const resp = await api.post('/api/backup/bundle/export', {
      include_credentials: bundleOpt.include_credentials,
      meta_mode: bundleOpt.meta_mode,
      slim_db: bundleOpt.slim_db,
      password,
    }, { responseType: 'blob', timeout: 600000 })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    a.download = m ? m[1] : 'autosave_migration.zip'
    a.click()
    URL.revokeObjectURL(url)
    saveOk(
      '整机迁移包已导出',
      bundleOpt.include_credentials ? '已包含凭据与 Session，请妥善加密保管' : '已打包数据库与所选元数据（不含敏感凭据）',
    )
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(await extractBlobErr(e))
  } finally {
    bundleExporting.value = false
  }
}

const onBundleFile = (ev) => {
  const file = ev.target.files?.[0] || null
  ev.target.value = ''
  if (!file) return
  if (file.size > MAX_BUNDLE_BYTES) {
    ElMessage.error(`迁移包体积超过 2 GB 上限（当前 ${fmtSize(file.size)}），请导出「仅 .nfo」版本后再迁移`)
    return
  }
  if (!(file.name || '').toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请选择 .zip 格式的整机迁移包')
    return
  }
  bundleFile.value = file
  bundleProgress.value = 0
  bundlePhase.value = ''
}

const doBundleRestore = async () => {
  const file = bundleFile.value
  if (!file) return
  bundleRestoring.value = true
  bundleProgress.value = 0
  bundlePhase.value = '正在上传并校验迁移包（1/2）'
  try {
    const { data: prevResp } = await api.post('/api/backup/bundle/preview', file, {
      timeout: 600000,
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: (ev) => {
        if (ev.total) bundleProgress.value = Math.min(99, Math.round((ev.loaded / ev.total) * 100))
      },
    })
    bundleProgress.value = 100
    const p = prevResp.preview || {}
    const c = p.counts || {}
    const credLine = p.has_credentials
      ? (bundleRestoreOpt.restore_credentials
        ? `凭据与 Session：包内包含（${(p.credential_files || []).join('、')}），将【覆盖本机凭据与密码】`
        : '凭据与 Session：包内包含，当前设为【保留本机凭据（跳过覆盖）】')
      : '凭据与 Session：包内未包含，将保留本机密码与登录态'
    const metaLine = p.has_meta
      ? (bundleRestoreOpt.restore_meta
        ? `本地元数据：NFO ${p.meta_nfo_count || 0} 个、图片 ${p.meta_asset_count || 0} 个（${fmtSize(p.meta_bytes || 0)}），将【恢复并适配本机目录】`
        : `本地元数据：包内含 ${p.meta_nfo_count || 0} NFO / ${p.meta_asset_count || 0} 图片，当前设为【跳过不恢复】`)
      : '本地元数据：包内未包含'
    await ElMessageBox.confirm(
      `迁移包导出于 ${p.exported_at || '未知时间'}（压缩包 ${fmtSize(p.bundle_bytes || file.size)}）。\n` +
      `数据库内容：设置 ${c.settings || 0} 项、${fmtBackupCounts(c)}。\n` +
      `${credLine}。\n${metaLine}。\n` +
      `恢复将整库替换当前数据，系统回到停止状态。`,
      '从整机迁移包恢复？',
      { confirmButtonText: '覆盖恢复', cancelButtonText: '取消', type: 'warning', confirmButtonClass: 'danger-confirm-btn' },
    )

    bundleProgress.value = 0
    bundlePhase.value = '正在上传并写入整机数据（2/2）'
    const qs = `restore_credentials=${bundleRestoreOpt.restore_credentials ? 'true' : 'false'}&restore_meta=${bundleRestoreOpt.restore_meta ? 'true' : 'false'}`
    const { data: res } = await api.post(`/api/backup/bundle/restore?${qs}`, file, {
      timeout: 600000,
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: (ev) => {
        if (ev.total) bundleProgress.value = Math.min(99, Math.round((ev.loaded / ev.total) * 100))
      },
    })
    bundleProgress.value = 100
    const rc = res.counts || {}
    await ElMessageBox.alert(
      `迁移包恢复完成：设置 ${rc.settings || 0} 项、${fmtBackupCounts(rc)}；` +
      `凭据${res.restored_credentials ? '已还原' : '未覆盖'}；` +
      `本地元数据写入 ${res.meta_files_restored || 0} 个文件。页面即将刷新。`,
      '迁移恢复成功',
      { confirmButtonText: '刷新页面' },
    )
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(errMsg(e))
  } finally {
    bundleRestoring.value = false
    bundlePhase.value = ''
    bundleProgress.value = 0
  }
}

onMounted(() => {
  loadBundleEstimate()
})

defineExpose({ loadBundleEstimate })
</script>
