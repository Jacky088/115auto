<template>
  <el-card shadow="never" class="page-card">
    <div class="toolbar">
      <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px" @change="load">
        <el-option v-for="(v, k) in statusLabel" :key="k" :label="v" :value="k" />
      </el-select>
      <el-input v-model="filter.search" placeholder="搜索标题/分享码" clearable style="width: 220px"
                @keyup.enter="load" @clear="load" />
      <el-button @click="load">查询</el-button>
      <el-popconfirm title="确认重试全部失败任务？将重新入队。" confirm-button-text="重试"
                     cancel-button-text="取消" confirm-button-type="primary" width="240"
                     @confirm="retryAll">
        <template #reference>
          <el-button type="warning" plain :loading="retrying">批量重试失败任务</el-button>
        </template>
      </el-popconfirm>
    </div>

    <el-table :data="items" size="small" v-loading="loading" empty-text="暂无记录">
      <el-table-column prop="created_at" label="时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tooltip :content="row.share_url"><span>{{ row.title || row.share_code }}</span></el-tooltip>
        </template>
      </el-table-column>
      <el-table-column prop="channel" label="来源" width="120" />
      <el-table-column prop="category" label="分类" width="85" />
      <el-table-column prop="kind" label="类型" width="80">
        <template #default="{ row }">
          <el-tag :type="row.kind === 'offline' ? 'warning' : 'primary'" size="small">
            {{ row.kind === 'offline' ? '云下载' : '分享' }}
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
      <el-table-column label="操作" width="130">
        <template #default="{ row }">
          <el-button v-if="['failed', 'skipped', 'blocked'].includes(row.status)"
                     size="small" type="primary" text @click="retry(row)">重试</el-button>
          <el-popconfirm v-if="['queued', 'submitted', 'failed', 'skipped'].includes(row.status)" title="确认删除该任务记录？"
                         confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                         width="220" @confirm="del(row)">
            <template #reference><el-button size="small" type="danger" text>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top: 14px; justify-content: flex-end" layout="total, prev, pager, next"
                   :total="total" :page-size="size" v-model:current-page="page" @current-change="load" />
  </el-card>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk, statusLabel } from '../api'

const isMobile = ref(window.innerWidth <= 768)
const onRz = () => { isMobile.value = window.innerWidth <= 768 }
onMounted(() => window.addEventListener('resize', onRz))
onUnmounted(() => window.removeEventListener('resize', onRz))

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 30
const loading = ref(false)
const retrying = ref(false)
const filter = reactive({ status: '', search: '' })

const statusTag = (s) => ({ success: 'success', failed: 'danger', blocked: 'warning',
                            queued: 'info', running: 'primary', duplicate: 'info', skipped: 'info' }[s] || 'info')

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/api/transfers', {
      params: { status: filter.status, search: filter.search, page: page.value, size },
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}
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
const retry = async (row) => {
  try {
    await api.post(`/api/transfers/${row.id}/retry`)
    ElMessage.success('已重新入队')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const del = async (row) => {
  try {
    await api.delete(`/api/transfers/${row.id}`)
    ElMessage.success('任务记录已删除')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
onMounted(load)
</script>
