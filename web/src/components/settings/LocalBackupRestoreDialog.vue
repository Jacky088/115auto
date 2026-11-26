<template>
  <el-dialog
    :model-value="modelValue"
    title="本地历史备份查看与恢复（data/backups/）"
    width="740px"
    align-center
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="local-restore-dialog">
      <div class="dialog-tip-row">
        <span>服务器本地存储目录：<code>data/backups/</code>（自动保留最新 10 份）</span>
        <el-button size="small" :loading="loading" @click="loadList">刷新列表</el-button>
      </div>

      <el-table :data="backups" size="small" stripe max-height="360" style="margin-top: 12px">
        <template #empty>
          <el-empty :image-size="60" description="暂无本地定时备份（勾选「本地存储」并在定时触发后生成）" />
        </template>
        <el-table-column prop="name" label="备份文件名" min-width="260" show-overflow-tooltip />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ fmtSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="备份时间" width="160">
          <template #default="{ row }">{{ row.mtime_str || (row.mtime ? fmtTime(row.mtime) : '-') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
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
                type="primary"
                link
                size="small"
                @click="downloadItem(row)"
              >
                下载
              </el-button>
              <el-button
                type="danger"
                link
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
        提示：恢复将覆盖当前全部业务数据（登录密码与通知密钥保留不变；恢复后系统将回到停止状态，需在顶栏手动点开始运行）。
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})

defineEmits(['update:modelValue'])

const loading = ref(false)
const backups = ref([])
const restoringFile = ref('')
const deletingFile = ref('')

const loadList = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/api/backup/local/list')
    backups.value = Array.isArray(data) ? data : []
  } catch (e) {
    backups.value = []
    ElMessage.error(errMsg(e, '获取本地备份列表失败'))
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (val) => {
  if (val) loadList()
})

const fmtBackupCounts = (s) => (
  `频道 ${s.channels || 0}、规则 ${s.rules || 0}、路由 ${s.routes || 0}、` +
  `黑名单 ${s.blacklist || 0}、订阅 ${s.subscriptions || 0}、转存 ${s.transfers || 0}、` +
  `自动整理 ${s.auto_move_rules || 0}、整理刮削 ${s.media_rules || 0}、操作账本 ${s.organized_records || 0}` +
  (s.media_tasks != null ? `、待刮削任务 ${s.media_tasks}` : '')
)

const restoreItem = async (row) => {
  if (!row?.name) return
  restoringFile.value = row.name
  try {
    // 1. 获取预览摘要
    const { data: prevResp } = await api.post(`/api/backup/local/preview?filename=${encodeURIComponent(row.name)}`)
    const s = prevResp.summary || {}

    const ok = await confirmAction({
      title: '从本地备份恢复？',
      message: (
        `文件名：${row.name}（${fmtSize(row.size)}）\n` +
        `导出时间：${s.exported_at || row.mtime_str || '未知'}\n` +
        `备份内容：设置 ${s.settings || 0} 项、${fmtBackupCounts(s)}。\n\n` +
        `恢复将整体覆盖当前全部业务数据（登录密码与通知密钥保留不变）。恢复后系统将回到停止状态。`
      ),
      confirmText: '覆盖恢复',
      cancelText: '取消',
      type: 'warning',
      danger: true,
    })
    if (!ok) return

    // 2. 执行恢复
    const { data: restResp } = await api.post(`/api/backup/local/restore?filename=${encodeURIComponent(row.name)}`)
    const stats = restResp.stats || {}
    await ElMessageBox.alert(
      `恢复完成：设置 ${stats.settings || 0} 项、${fmtBackupCounts(stats)}。页面即将刷新以加载恢复后的数据。`,
      '恢复成功',
      { confirmButtonText: '刷新页面' },
    )
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(errMsg(e, '恢复本地备份失败'))
    }
  } finally {
    restoringFile.value = ''
  }
}

const downloadItem = async (row) => {
  if (!row?.name) return
  try {
    const resp = await api.get(`/api/backup/local/download?filename=${encodeURIComponent(row.name)}`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = row.name
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(errMsg(e, '下载备份文件失败'))
  }
}

const deleteItem = async (row) => {
  if (!row?.name) return
  const ok = await confirmAction({
    title: '删除本地备份',
    message: `确定永久删除本地备份文件「${row.name}」吗？此操作无法撤销。`,
    confirmText: '删除',
    cancelText: '取消',
    type: 'warning',
    danger: true,
  })
  if (!ok) return

  deletingFile.value = row.name
  try {
    await api.delete(`/api/backup/local?filename=${encodeURIComponent(row.name)}`)
    ElMessage.success(`已删除本地备份文件 ${row.name}`)
    await loadList()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除备份文件失败'))
  } finally {
    deletingFile.value = ''
  }
}

defineExpose({
  loadList,
})
</script>

<style scoped>
.local-restore-dialog .dialog-tip-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--text-muted);
}

.table-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
