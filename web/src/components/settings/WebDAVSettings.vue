<template>
  <div class="section-card">
    <div class="section-title">WebDAV 云端备份存储</div>

    <!-- 已配置状态（隐藏敏感输入框，展示连接状态与更换按钮） -->
    <template v-if="configured && !editing">
      <el-form-item label="配置状态">
        <div class="range-row dav-status-row">
          <div class="dav-identity">
            <el-tag :type="form.last_test_ok ? 'success' : 'info'" effect="plain" size="small">
              {{ form.last_test_ok ? '✓ 已配置并连接' : '✓ 已配置' }}
            </el-tag>
            <span class="tip dav-identity-text">
              <code>{{ form.url }}</code>（{{ form.username }}）
            </span>
          </div>
          <div class="row-actions">
            <el-button text type="primary" size="small" @click="startEdit">更换</el-button>
            <el-button text type="danger" size="small" @click="handleClear">清除</el-button>
          </div>
        </div>
        <div class="tip dav-subline">
          远端目录：<code>{{ form.root_dir }}</code> · 保留最近 <b>{{ form.keep_count }}</b> 份整机迁移包
        </div>
        <div v-if="form.last_backup_status" class="tip dav-last-backup">
          上次备份执行：{{ form.last_backup_status }}
          <template v-if="formattedLastBackupTime">（{{ formattedLastBackupTime }}）</template>
        </div>
      </el-form-item>
    </template>

    <!-- 未配置或更换时的编辑输入区 -->
    <template v-else>
      <el-form-item label="服务地址">
        <div class="range-row">
          <el-input
            v-model="form.url"
            class="dav-url-input"
            placeholder="例如 https://dav.jianguoyun.com/dav/ 或 http://192.168.1.100:5005"
            @input="onFieldChange"
          />
          <el-button
            type="primary"
            plain
            :loading="testing"
            @click="testWebDAV"
          >
            测试连接
          </el-button>
          <span v-if="testedOk" class="tip dav-tested">
            ✅ 连接与读写权限测试通过
          </span>
        </div>
        <div class="tip">支持坚果云、群晖、威联通、Alist、Nextcloud 或自建 WebDAV，可用于自动异地容灾冷备</div>
      </el-form-item>

      <el-form-item label="账户认证">
        <div class="range-row dav-cred-row">
          <el-input
            v-model="form.username"
            placeholder="用户名 / 账户"
            @input="onFieldChange"
          />
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="configured ? '留空则保持原有应用密码' : '应用密码 / Token'"
            @input="onFieldChange"
          />
        </div>
      </el-form-item>

      <el-form-item label="备份目录">
        <div class="range-row">
          <el-input
            v-model="form.root_dir"
            class="dav-dir-input"
            placeholder="/115AutoSave_Backup"
            @input="onFieldChange"
          />
        </div>
        <div class="tip">远端子目录，不存在时测试与备份阶段将自动创建</div>
      </el-form-item>

      <el-form-item label="自动轮转">
        <div class="range-row">
          <span class="tip">保留最近</span>
          <el-input-number
            v-model="form.keep_count"
            :min="1"
            :max="100"
            :step="1"
            :step-strictly="true"
            :precision="0"
          />
          <span class="tip row-hint">份整机迁移包（超出将自动删除超期旧备份，防占满空间）</span>
        </div>
      </el-form-item>

      <el-form-item>
        <div class="range-row">
          <el-button
            type="primary"
            :loading="saving"
            @click="saveWebDAV"
          >
            保存 WebDAV 配置
          </el-button>
          <el-button v-if="configured" @click="cancelEdit">取消</el-button>
          <span v-if="!testedOk" class="tip warn-text row-hint">
            ⚠️ 必须先点击「测试连接」验证通过后方可保存
          </span>
        </div>
      </el-form-item>
    </template>

    <!-- 从 WebDAV 恢复弹窗 -->
    <WebDAVRestoreDialog
      v-model="restoreVisible"
      :root-dir="form.root_dir"
      :initial-filter="restoreFilter"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'
import WebDAVRestoreDialog from './WebDAVRestoreDialog.vue'

const emit = defineEmits(['backup-done', 'status-changed'])

const form = reactive({
  url: '',
  username: '',
  password: '',
  root_dir: '/115AutoSave_Backup',
  keep_count: 10,
  password_set: false,
  last_test_ok: false,
  last_backup_status: '',
  last_backup_at: 0,
})

const editing = ref(false)
const testing = ref(false)
const testedOk = ref(false)
const saving = ref(false)
const exporting = ref(false)
const restoreVisible = ref(false)
const restoreFilter = ref('all')

const configured = computed(() => !!(form.url && form.username && (form.password || form.password_set)))
const davReady = computed(() => configured.value && (testedOk.value || form.last_test_ok) && !editing.value)

const formattedLastBackupTime = computed(() => {
  if (form.last_backup_at && Number(form.last_backup_at) > 946684800) {
    const t = fmtTime(form.last_backup_at)
    if (t && t !== '-') return t
  }
  if (form.last_backup_status) {
    const m = form.last_backup_status.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/)
    if (m) {
      return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`
    }
  }
  return ''
})

const onFieldChange = () => {
  // 任何关键参数变动均需重新测试通过
  testedOk.value = false
}

const loadConfig = async () => {
  try {
    const { data } = await api.get('/api/backup/webdav/config')
    if (data) {
      form.url = data.url || ''
      form.username = data.username || ''
      form.password = ''
      form.password_set = !!data.password_set
      form.root_dir = data.root_dir || '/115AutoSave_Backup'
      form.keep_count = data.keep_count || 10
      form.last_test_ok = !!data.last_test_ok
      form.last_backup_status = data.last_backup_status || ''
      form.last_backup_at = data.last_backup_at || 0
      testedOk.value = !!data.last_test_ok

      // 若后端已配置且有有效密码，默认折叠隐藏；若未配置则展开编辑态
      if (data.enabled && data.password_set) {
        editing.value = false
      } else {
        editing.value = true
      }
      emit('status-changed', davReady.value)
    }
  } catch { /* ignore */ }
}

const startEdit = () => {
  form.password = ''
  testedOk.value = false
  editing.value = true
  emit('status-changed', davReady.value)
}

const cancelEdit = () => {
  editing.value = false
  loadConfig()
}

const testWebDAV = async () => {
  if (!form.url || !form.username) {
    ElMessage.warning('请先填写 WebDAV 服务地址与用户名')
    return
  }
  testing.value = true
  try {
    const { data } = await api.post('/api/backup/webdav/test', {
      url: form.url,
      username: form.username,
      password: form.password,
      root_dir: form.root_dir,
    })
    if (data?.ok) {
      testedOk.value = true
      form.last_test_ok = true
      saveOk('WebDAV 测试成功', data.message || '读写删权限已全部验证通过')
    } else {
      testedOk.value = false
      form.last_test_ok = false
      ElMessage.error(data?.message || '测试失败')
    }
  } catch (e) {
    testedOk.value = false
    form.last_test_ok = false
    ElMessage.error(errMsg(e, 'WebDAV 连接测试失败'))
  } finally {
    testing.value = false
  }
}

const saveWebDAV = async () => {
  if (!testedOk.value) {
    ElMessage.warning('请先点击「测试连接」验证权限通过后方可保存（更换配置同理）')
    return
  }
  saving.value = true
  try {
    await api.post('/api/backup/webdav/config', {
      url: form.url,
      username: form.username,
      password: form.password,
      root_dir: form.root_dir,
      keep_count: form.keep_count,
    })
    saveOk('WebDAV 配置已保存')
    editing.value = false
    await loadConfig()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存 WebDAV 配置失败'))
  } finally {
    saving.value = false
  }
}

const handleClear = async () => {
  const confirmed = await confirmAction({
    title: '清除 WebDAV 配置？',
    message: '确定要清除当前 WebDAV 云端备份配置与凭据吗？清除后将停止自动云端备份。',
    confirmText: '清除配置',
    danger: true,
  })
  if (!confirmed) return
  try {
    await api.delete('/api/backup/webdav/config')
    saveOk('WebDAV 配置已清除')
    form.url = ''
    form.username = ''
    form.password = ''
    form.password_set = false
    form.last_test_ok = false
    testedOk.value = false
    editing.value = true
    emit('status-changed', false)
  } catch (e) {
    ElMessage.error(errMsg(e, '清除配置失败'))
  }
}

const exportToWebDAV = async (bundleOpt) => {
  if (!davReady.value) {
    ElMessage.warning('请先在下方配置 WebDAV 并测试连接成功后再执行备份')
    return
  }
  exporting.value = true
  try {
    const { data } = await api.post('/api/backup/webdav/export_now', {
      meta_mode: bundleOpt?.meta_mode || 'nfo',
      slim_db: bundleOpt?.slim_db ?? true,
      include_credentials: !!bundleOpt?.include_credentials,
    })
    const pruneMsg = data.pruned > 0 ? `，已自动清理 ${data.pruned} 份旧备份` : ''
    saveOk('WebDAV 上传完成', `${data.filename}（${fmtSize(data.bytes)}）上传成功${pruneMsg}`)
    emit('backup-done')
    await loadConfig()
  } catch (e) {
    ElMessage.error(errMsg(e, '上传至 WebDAV 失败'))
  } finally {
    exporting.value = false
  }
}

const openRestoreDialog = (filter = 'all') => {
  restoreFilter.value = filter || 'all'
  restoreVisible.value = true
}

onMounted(() => {
  loadConfig()
})

defineExpose({
  davReady,
  isExporting: exporting,
  exportToWebDAV,
  openRestoreDialog,
  loadConfig,
})
</script>
