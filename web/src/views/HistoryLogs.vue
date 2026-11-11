<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="card-head">
        <div class="title-with-badge">
          <span class="card-title">历史日志</span>
          <el-tag size="small" class="count-tag">共 {{ total }} 条日志</el-tag>
          <span class="tip">保留 7 天，到期自动删除</span>
        </div>
        <el-button @click="exportTxt">导出选中日志</el-button>
      </div>
    </template>

    <!-- 按天选择 -->
    <div class="day-chips" v-loading="daysLoading">
      <div v-for="d in days" :key="d.d" class="day-chip" :class="{ active: d.d === selDate }"
           @click="pickDay(d.d)">
        <div class="d-name">
          {{ dayLabel(d.d) }}
          <span v-if="d.err" class="d-badge err">{{ d.err }} 错</span>
          <span v-else-if="d.warn" class="d-badge warn">{{ d.warn }} 警</span>
          <span v-else class="d-badge okdot"></span>
        </div>
        <div class="d-sub">
          <span class="d-count">{{ d.c }} 条</span>
          <span v-if="d.warn" class="d-warn-text">⚠ {{ d.warn }} 警告</span>
          <span v-if="d.err" class="d-err-text">✖ {{ d.err }} 错误</span>
        </div>
      </div>
      <el-empty v-if="!daysLoading && !days.length" description="暂无日志" :image-size="60" />
    </div>

    <!-- 筛选 -->
    <div class="toolbar filter-toolbar" style="margin-top: 14px">
      <div class="filter-selects">
        <el-select v-model="filter.level" clearable placeholder="级别" class="filter-level" @change="load">
          <el-option label="info" value="info" />
          <el-option label="warn" value="warn" />
          <el-option label="error" value="error" />
        </el-select>
        <el-select v-model="filter.module" clearable filterable placeholder="模块" class="filter-module" @change="load">
          <el-option v-for="m in modules" :key="m" :label="m" :value="m" />
        </el-select>
      </div>
      <div class="filter-search-row">
        <el-input v-model="filter.search" placeholder="搜索日志内容" clearable class="filter-search"
                  @keyup.enter="load" @clear="load">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="load">查询</el-button>
        <span class="tip filter-count">共 {{ total }} 条</span>
      </div>
    </div>

    <el-table :data="items" size="small" v-loading="loading">
      <template #empty>
        <el-empty :image-size="60" description="暂无符合条件的日志记录" />
      </template>
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="级别" width="80">
        <template #default="{ row }">
          <el-tag :type="levelTag(row.level)" size="small">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="module" label="模块" width="110">
        <template #default="{ row }"><span class="mono">{{ row.module }}</span></template>
      </el-table-column>
      <el-table-column label="内容" min-width="480">
        <template #default="{ row }">
          <span class="msg-text">{{ row.message }}</span>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination class="log-pager" style="margin-top: 14px; justify-content: flex-end"
                   layout="total, sizes, prev, pager, next, jumper"
                   :total="total" :page-size="size" :page-sizes="[20, 50, 100]"
                   v-model:current-page="page" v-model:page-size="size"
                   @current-change="load" @size-change="onSizeChange" />
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api, errMsg, fmtTime } from '../api'

const days = ref([])
const daysLoading = ref(false)
const selDate = ref('')
const modules = ref([])
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)  // 每页条数：默认 50，可在分页器里切换（20/50/100）
const loading = ref(false)
const filter = reactive({ level: '', module: '', search: '' })

// 切换每页条数：回到第 1 页并重新加载
const onSizeChange = () => { page.value = 1; load() }

const levelTag = (lv) => ({ info: 'info', warn: 'warning', error: 'danger' }[lv] || 'info')

const dayLabel = (d) => {
  const today = new Date()
  const p = (n) => String(n).padStart(2, '0')
  const todayStr = `${today.getFullYear()}-${p(today.getMonth() + 1)}-${p(today.getDate())}`
  const yest = new Date(today.getTime() - 86400_000)
  const yestStr = `${yest.getFullYear()}-${p(yest.getMonth() + 1)}-${p(yest.getDate())}`
  if (d === todayStr) return '今天'
  if (d === yestStr) return '昨天'
  return d.slice(5)
}

const loadDays = async () => {
  daysLoading.value = true
  try {
    days.value = (await api.get('/api/logs/days')).data
    if (!selDate.value && days.value.length) {
      selDate.value = days.value[0].d
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    daysLoading.value = false
  }
}

const pickDay = (d) => {
  selDate.value = d
  page.value = 1
  load()
}

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/api/logs', {
      params: { date: selDate.value, level: filter.level, module: filter.module,
                search: filter.search, page: page.value, size: size.value },
    })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}

const exportTxt = async () => {
  try {
    const resp = await api.get('/api/logs/export', {
      params: { date: selDate.value }, responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs_${selDate.value || '7days'}.txt`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    // blob 响应的错误信息也是 blob
    let msg = '导出失败'
    if (e.response?.data instanceof Blob) {
      try { msg = JSON.parse(await e.response.data.text()).detail || msg } catch { /* keep */ }
    }
    ElMessage.error(msg)
  }
}

onMounted(async () => {
  await loadDays()
  modules.value = (await api.get('/api/logs/modules')).data
  load()
})
</script>

<style scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.title-with-badge { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-title { font-weight: 700; font-size: var(--fs-title); color: var(--saas-text-main); }
.tip { color: var(--saas-text-sub); font-size: var(--fs-caption); font-weight: 400; }
.day-chips { display: flex; gap: 10px; flex-wrap: wrap; }
.day-chip {
  flex: 1; min-width: 150px; padding: 12px 16px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--saas-line); background: var(--glass-bg-strong); color: var(--saas-text-main); transition: all .15s ease;
}
.day-chip:hover {
  border-color: var(--el-color-primary);
  background: rgba(79, 107, 255, .08);
  transform: translateY(-1px);
}
.day-chip.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  box-shadow: 0 2px 10px rgba(47, 107, 255, .12);
}
.d-name { font-weight: 600; font-size: var(--fs-body); color: var(--saas-text-main);
          display: flex; align-items: center; gap: 8px; }
.d-badge {
  font-size: var(--fs-caption); font-weight: 600; padding: 1px 8px; border-radius: 9px;
}
.d-badge.err { background: #fee2e2; color: #dc2626; }
.d-badge.warn { background: #fef3c7; color: #d97706; }
.d-badge.okdot { width: 8px; height: 8px; border-radius: 50%; background: #d1e0ff; padding: 0; }
.d-sub { display: flex; gap: 10px; margin-top: 5px; font-size: var(--fs-caption); align-items: center; }
.d-count { color: var(--saas-text-sub); }
.d-warn-text { color: #d97706; font-weight: 500; }
.d-err-text { color: #dc2626; font-weight: 500; }
.msg-text { font-size: var(--fs-caption); word-break: break-all; white-space: normal; line-height: 1.6; }

/* 筛选栏：桌面端同一行铺开；移动端拆成双栏下拉 + 搜索按钮行，不撑宽卡片 */
.filter-selects { display: inline-flex; align-items: center; gap: 10px; }
.filter-level { width: 120px; }
.filter-module { width: 150px; }
.filter-search-row { display: inline-flex; align-items: center; gap: 10px; }
.filter-search { width: 260px; }
.filter-count { white-space: nowrap; }

@media (max-width: 768px) {
  .day-chip { padding: 10px 12px; }
  .filter-toolbar { flex-direction: column; align-items: stretch; gap: 8px; }
  .filter-selects { width: 100%; display: flex; gap: 8px; }
  .filter-level { flex: 1; min-width: 0; width: auto; }
  .filter-module { flex: 1.25; min-width: 0; width: auto; }
  .filter-search-row { width: 100%; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .filter-toolbar .filter-search { flex: 1 1 150px; min-width: 0; width: auto; }
  .log-pager { justify-content: center !important; }
}
</style>
