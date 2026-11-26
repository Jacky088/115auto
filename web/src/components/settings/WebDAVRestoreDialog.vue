<template>
  <el-dialog
    :model-value="modelValue"
    title="从 WebDAV 浏览并恢复历史备份"
    :width="'min(760px, calc(100vw - 44px))'"
    align-center
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="webdav-restore-dialog">
      <div class="dialog-tip-row">
        <span>远端备份目录：<code>{{ rootDir }}</code></span>
        <div class="dialog-controls">
          <el-radio-group v-model="filterKind" size="small" class="bundle-capsule-radios">
            <el-radio-button value="all">全部 ({{ backups.length }})</el-radio-button>
            <el-radio-button value="auto">定时备份 ({{ autoCount }})</el-radio-button>
            <el-radio-button value="migration">手动迁移 ({{ manualCount }})</el-radio-button>
          </el-radio-group>
          <el-button size="small" :loading="loading" @click="loadList">刷新列表</el-button>
        </div>
      </div>

      <div v-if="loadError" class="tip-box is-warn dav-list-error">
        <span>{{ loadError }}</span>
        <el-button size="small" type="danger" plain @click="loadList">重试</el-button>
      </div>

      <el-table :data="filteredBackups" size="small" stripe max-height="360" style="margin-top: 12px">
        <template #empty>
          <el-empty
            :image-size="60"
            :description="loadError ? '备份列表读取失败，请按上方提示检查 WebDAV 配置' : '远端目录暂无符合筛选条件的 .zip 备份包'"
          />
        </template>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag
              :type="row.kind === 'auto' ? 'success' : 'primary'"
              size="small"
              effect="plain"
            >
              {{ row.kind === 'auto' ? '定时备份' : '手动迁移' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="迁移包文件" min-width="240" show-overflow-tooltip />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ fmtSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="160">
          <template #default="{ row }">{{ row.mtime ? fmtTime(row.mtime) : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button
                type="danger"
                plain
                size="small"
                :loading="restoringFile === row.name"
                @click="restoreItem(row)"
              >
                恢复
              </el-button>
              <el-button
                type="danger"
                text
                size="small"
                :loading="deletingFile === row.name"
                @click="deleteItem(row)"
              >
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="tip-box is-warn" style="margin-top: 12px">
        恢复将整库覆盖当前运行库，自动迁移媒体元数据并保持系统停止；恢复前建议先在本地留存备份。
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  rootDir: { type: String, default: '/115AutoSave_Backup' },
  initialFilter: { type: String, default: 'all' },
  restoreCredentials: { type: Boolean, default: false },
  restoreMeta: { type: Boolean, default: true },
})

defineEmits(['update:modelValue'])

const loading = ref(false)
const loadError = ref('')
const backups = ref([])
const restoringFile = ref('')
const deletingFile = ref('')
const filterKind = ref('all')

const autoCount = computed(() => backups.value.filter((b) => b.kind === 'auto').length)
const manualCount = computed(() => backups.value.filter((b) => b.kind === 'migration').length)

const filteredBackups = computed(() => {
  if (filterKind.value === 'all') return backups.value
  return backups.value.filter((b) => b.kind === filterKind.value)
})

const loadList = async () => {
  loading.value = true
  loadError.value = ''
  try {
    // WebDAV 目录遍历（PROPFIND）在大目录/慢网盘上可能超过默认 30s，单独放宽
    const { data } = await api.get('/api/backup/webdav/list', { timeout: 60000 })
    backups.value = Array.isArray(data) ? data : []
  } catch (e) {
    backups.value = []
    loadError.value = errMsg(e, '获取 WebDAV 备份列表失败')
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (val) => {
  if (val) {
    filterKind.value = props.initialFilter || 'all'
    loadList()
  }
})

const deleteItem = async (row) => {
  if (!row?.name) return
  const confirmed = await confirmAction({
    title: '删除 WebDAV 备份文件？',
    message: (
      `确定要永久删除 WebDAV 远端备份文件？\n\n` +
      `文件名：${row.name}\n` +
      `文件大小：${fmtSize(row.size)}\n\n` +
      `删除后远端存储将无法找回此文件。`
    ),
    confirmText: '确认删除',
    cancelText: '取消',
    danger: true,
  })
  if (!confirmed) return
  deletingFile.value = row.name
  try {
    await api.delete(`/api/backup/webdav/file?filename=${encodeURIComponent(row.name)}`)
    saveOk('备份已删除', row.name)
    await loadList()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除 WebDAV 备份失败'))
  } finally {
    deletingFile.value = ''
  }
}

const restoreItem = async (row) => {
  if (!row?.name) return
  restoringFile.value = row.name
  try {
    // 1. 获取预览（大包需流式从 WebDAV 下载，给予 10 分钟超时上限）
    const { data: prevResp } = await api.post(
      `/api/backup/webdav/restore_preview?filename=${encodeURIComponent(row.name)}`,
      null,
      { timeout: 600000 },
    )
    const p = prevResp.preview || {}
    const c = p.counts || {}
    const credTip = props.restoreCredentials
      ? '凭据与 Session：将随包覆盖还原并刷新登录令牌；'
      : '凭据与 Session：保持本机当前密码与登录态不变；'
    const metaTip = props.restoreMeta
      ? '媒体刮削资产：将还原并自动迁移路径到本机目录。'
      : '媒体刮削资产：跳过不还原。'

    const ok = await confirmAction({
      title: '从 WebDAV 恢复迁移包？',
      message: (
        `确定拉取并恢复此迁移包？\n` +
        `文件名：${row.name}（${fmtSize(row.size)}）\n` +
        `导出时间：${p.exported_at || '未知'}\n` +
        `数据库内容：设置 ${c.settings || 0} 项、转存规则 ${c.rules || 0} 条、转存记录 ${c.transfers || 0} 条\n` +
        `${credTip}\n${metaTip}\n\n` +
        `恢复将整体替换当前数据，系统将回到停止状态。`
      ),
      confirmText: '覆盖恢复',
      cancelText: '取消',
      type: 'warning',
      danger: true,
    })
    if (!ok) return

    // 2. 执行恢复（透传用户勾选的凭据与元数据开关，10 分钟长超时）
    const q = new URLSearchParams({
      filename: row.name,
      restore_credentials: String(!!props.restoreCredentials),
      restore_meta: String(!!props.restoreMeta),
    })
    await api.post(`/api/backup/webdav/restore_apply?${q.toString()}`, null, { timeout: 600000 })
    await ElMessageBox.alert('WebDAV 迁移包恢复完成，页面即将刷新。', '恢复成功', { confirmButtonText: '刷新页面' })
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(errMsg(e, '恢复迁移包失败'))
    }
  } finally {
    restoringFile.value = ''
  }
}
</script>

<style scoped>
.webdav-restore-dialog .dialog-tip-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--text-muted);
  flex-wrap: wrap;
  gap: 10px;
}

/* 远端列表读取失败提示：左侧原因（可换行）+ 右侧重试按钮 */
.dav-list-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
  overflow-wrap: anywhere;
}

.dialog-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

@media (max-width: 768px) {
  .webdav-restore-dialog .dialog-tip-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .dialog-controls {
    width: 100%;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
