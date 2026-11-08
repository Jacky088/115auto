<template>
  <el-card shadow="never" class="page-card">
    <div class="toolbar">
      <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px" @change="load">
        <el-option v-for="(v, k) in statusLabel" :key="k" :label="v" :value="k" />
      </el-select>
      <el-input v-model="filter.search" placeholder="搜索标题/分享码" clearable style="width: 220px"
                @keyup.enter="load" @clear="load" />
      <el-button @click="load">查询</el-button>
      <el-button type="danger" plain :disabled="!selected.length" @click="delSelected">
        批量删除{{ selected.length ? `（${selected.length}）` : '' }}
      </el-button>
      <el-popconfirm title="确认重试全部失败任务？将重新入队。" confirm-button-text="重试"
                     cancel-button-text="取消" confirm-button-type="primary" width="240"
                     @confirm="retryAll">
        <template #reference>
          <el-button type="warning" plain :loading="retrying">批量重试失败任务</el-button>
        </template>
      </el-popconfirm>
    </div>

    <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon
              style="margin-bottom: 10px" />

    <el-table ref="tableRef" :data="items" size="small" v-loading="loading" empty-text="暂无记录"
              @selection-change="onSelect">
      <el-table-column type="selection" width="42" :selectable="canDelete" />
      <el-table-column prop="created_at" label="时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <span>{{ row.title || row.share_code }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="channel" label="来源" width="120" />
      <el-table-column prop="category" label="分类" width="85" />
      <el-table-column prop="kind" label="类型" width="80">
        <template #default="{ row }">
          <el-tag :type="row.kind === 'offline' ? 'warning'
                          : (row.priority > 0 ? 'danger' : 'primary')" size="small">
            {{ row.kind === 'offline' ? '云下载'
               : (row.priority > 0 ? '补全' : (row.increment ? '追更' : '分享')) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="target_dir" label="目标目录" width="150" show-overflow-tooltip />
      <el-table-column prop="size" label="大小" width="85">
        <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
      </el-table-column>
      <el-table-column prop="file_count" label="文件数" width="75" />
      <el-table-column prop="attempts" label="重试" width="60" />
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tooltip :content="row.error" :disabled="!row.error">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel[row.status] || row.status }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <div class="op-actions">
            <el-button v-if="['failed', 'skipped', 'blocked'].includes(row.status)"
                       type="primary" text @click="retry(row)">重试</el-button>
            <el-button v-if="row.status === 'success' && row.kind === 'share'
                            && row.channel && row.message_id"
                       type="primary" text @click="openBackfill(row)">补全</el-button>
            <el-popconfirm v-if="['queued', 'submitted', 'failed', 'skipped', 'duplicate'].includes(row.status)"
                           title="确认删除该任务记录？"
                           confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                           width="220" @confirm="del(row)">
              <template #reference><el-button type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="bfVisible" title="向前补全早期分集" width="480px">
      <el-form label-width="90px">
        <el-form-item label="剧名关键词">
          <el-input v-model="bfForm.keyword" placeholder="用于匹配频道历史帖子的剧名" />
        </el-form-item>
        <el-form-item label="当前集数">
          <el-input-number v-model="bfForm.ep" :min="1" style="width: 130px" />
          <span class="bf-line" style="margin-left: 8px">{{ bfRange }}</span>
        </el-form-item>
        <el-form-item label="频道">
          <span class="bf-line">@{{ bfForm.channel }}</span>
        </el-form-item>
      </el-form>
      <div class="bf-tip">
        将向前翻找频道历史里同一剧集的早期分集（最多 50 页）并加入转存队列；
        补全任务优先消耗每日额度，已存过的集数不会重复。
        早期链接已失效的集数会失败并注明原因，之后补发的新链接会照常转存。
      </div>
      <template #footer>
        <el-button @click="bfVisible = false">取消</el-button>
        <el-button type="primary" :loading="bfLoading" @click="doBackfill">开始补全</el-button>
      </template>
    </el-dialog>

    <el-pagination style="margin-top: 14px; justify-content: flex-end" layout="total, sizes, prev, pager, next"
                   :total="total" :page-size="size" :page-sizes="[20, 50, 100]"
                   v-model:current-page="page" v-model:page-size="size"
                   @current-change="load" @size-change="onSizeChange" />
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk, statusLabel } from '../api'

const isMobile = ref(window.innerWidth <= 768)
const onRz = () => { isMobile.value = window.innerWidth <= 768 }
onMounted(() => window.addEventListener('resize', onRz))
onUnmounted(() => window.removeEventListener('resize', onRz))

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)  // 每页条数：默认 50，可在分页器里切换（20/50/100）
const loading = ref(false)
const retrying = ref(false)
const filter = reactive({ status: '', search: '' })
const tableRef = ref(null)
const selected = ref([])
const canDelete = (row) => !['running', 'success'].includes(row.status)

const statusTag = (s) => ({ success: 'success', failed: 'danger', blocked: 'warning',
                            queued: 'info', running: 'primary', duplicate: 'info', skipped: 'info' }[s] || 'info')

const loadError = ref('')
const load = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.get('/api/transfers', {
      params: { status: filter.status, search: filter.search, page: page.value, size: size.value },
    })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    loadError.value = errMsg(e)
  } finally {
    loading.value = false
  }
}

// 切换每页条数：回到第 1 页并重新加载
const onSizeChange = () => { page.value = 1; load() }
const retryAll = async () => {
  retrying.value = true
  try {
    const { data } = await api.post('/api/transfers/retry-failed')
    if (data.count > 0) {
      saveOk('批量重试成功', `${data.count} 个失败任务已重新入队`)
    } else {
      ElMessage.info('当前没有失败任务')
    }
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { retrying.value = false }
}
const onSelect = (rows) => { selected.value = rows }
const delSelected = async () => {
  const n = selected.value.length
  if (!n) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${n} 条任务记录？删除后仪表盘的最近转存记录会同步移除。`,
      '批量删除', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }  // 用户取消
  try {
    const { data } = await api.post('/api/transfers/batch-delete',
      { ids: selected.value.map(r => r.id) })
    if (data.skipped) {
      ElMessage.warning(`已删除 ${data.deleted} 条；${data.skipped} 条因正在转存/已成功被跳过`)
    } else {
      ElMessage.success(`已删除 ${data.deleted} 条任务记录`)
    }
    tableRef.value?.clearSelection()
    selected.value = []
    // 当前页被删空时回到上一页，避免停在空白页
    if (items.value.length <= n && page.value > 1) page.value -= 1
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const retry = async (row) => {
  try {
    await api.post(`/api/transfers/${row.id}/retry`)
    ElMessage.success('已重新入队')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
// ---- 向前补全：以该条成功转存为锚点，向前翻找频道历史里同剧的早期分集 ----
const bfVisible = ref(false)
const bfLoading = ref(false)
const bfForm = reactive({ transferId: 0, keyword: '', channel: '', ep: null })
const bfRange = computed(() => bfForm.ep
  ? `将向前查找 E01~E${String(bfForm.ep - 1).padStart(2, '0')}`
  : '填写当前集数后确定查找范围（按剧名关键词匹配早期分集帖）')
const stripEpisode = (t) => (t || '')
  .replace(/[Ss]\d{1,2}\s?[Ee]\d{1,3}/g, '')
  .replace(/[Ee][Pp]\s?\d{1,3}/g, '')
  .replace(/第\s?\d{1,3}\s?[集话話期]/g, '')
  .replace(/[（(]\d{4}[）)]?/g, '')
  .trim()
const openBackfill = (row) => {
  const t = row.title || ''
  const m = t.match(/[Ss]\d{1,2}\s?[Ee](\d{1,3})/) || t.match(/第\s?(\d{1,3})\s?[集话話期]/)
            || t.match(/[Ee][Pp]\s?(\d{1,3})/)
  bfForm.transferId = row.id
  bfForm.keyword = stripEpisode(t) || t
  bfForm.channel = row.channel || ''
  bfForm.ep = m ? parseInt(m[1], 10) : null
  bfVisible.value = true
}
const doBackfill = async () => {
  if (!bfForm.keyword.trim()) { ElMessage.warning('请填写剧名关键词'); return }
  if (!bfForm.ep) { ElMessage.warning('请填写当前集数（标题未识别出集数时需手动填写）'); return }
  bfLoading.value = true
  try {
    const { data } = await api.post('/api/backfill/episodes', {
      transfer_id: bfForm.transferId, keyword: bfForm.keyword.trim(),
      current_ep: bfForm.ep,
    })
    saveOk('已开始向前补全', `《${data.base}》${data.episodes}，任务将优先入队`)
    bfVisible.value = false
  } catch (e) { ElMessage.error(errMsg(e)) } finally { bfLoading.value = false }
}
const del = async (row) => {
  try {
    await api.delete(`/api/transfers/${row.id}`)
    ElMessage.success('任务记录已删除')
    tableRef.value?.clearSelection()
    selected.value = []
    if (items.value.length <= 1 && page.value > 1) page.value -= 1
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
onMounted(load)
</script>

<style scoped>
/* 操作列：重试 / 删除 固定左右排列，不随列宽换行堆叠 */
.op-actions {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 6px;
  white-space: nowrap;
}
.op-actions :deep(.el-button) {
  margin-left: 0 !important;
  padding: 4px 6px;
}
.op-actions :deep(.el-tooltip__trigger) { display: inline-flex; }
</style>
