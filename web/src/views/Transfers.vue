<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="card-head">
        <div class="title-with-badge">
          <span class="card-title">转存记录</span>
          <el-tag size="small" class="count-tag">共 {{ total }} 条记录</el-tag>
        </div>
      </div>
    </template>
    <div class="toolbar">
      <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px" @change="loadFromFirstPage">
        <el-option v-for="(v, k) in statusLabel" :key="k" :label="v" :value="k" />
      </el-select>
      <el-input v-model="filter.search" placeholder="搜索标题 / 分享码" clearable style="width: 260px"
                @keyup.enter="loadFromFirstPage" @clear="loadFromFirstPage">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button @click="loadFromFirstPage">查询</el-button>
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

    <el-table ref="tableRef" :data="items" size="small" v-loading="loading"
              @selection-change="onSelect">
      <template #empty>
        <el-empty :image-size="60" description="暂无转存记录" />
      </template>
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
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <div class="table-actions">
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

    <BackfillDialog ref="backfillDialogRef" />

    <el-pagination style="margin-top: 14px; justify-content: flex-end" layout="total, sizes, prev, pager, next"
                   :total="total" :page-size="size" :page-sizes="[20, 50, 100]"
                   v-model:current-page="page" v-model:page-size="size"
                   @current-change="load" @size-change="onSizeChange" />
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api, errMsg, fmtSize, fmtTime, saveOk, statusLabel } from '../api'
import BackfillDialog from '../components/BackfillDialog.vue'

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
let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.get('/api/transfers', {
      params: { status: filter.status, search: filter.search, page: page.value, size: size.value },
    })
    if (seq !== loadSeq) return  // 已有更新的请求：丢弃过期响应，防止快速筛选时旧页覆盖新页
    items.value = data.items
    total.value = data.total
  } catch (e) {
    if (seq === loadSeq) loadError.value = errMsg(e)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// 筛选/搜索条件变化：重置回第 1 页，避免停在全空页
const loadFromFirstPage = () => { page.value = 1; load() }

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
// ---- 向前补全：通过统一 BackfillDialog 组件处理 ----
const backfillDialogRef = ref(null)
const openBackfill = (row) => backfillDialogRef.value?.openForTransfer(row)

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
.card-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.title-with-badge { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-title { font-weight: 700; font-size: var(--fs-title); color: var(--saas-text-main); }
</style>
