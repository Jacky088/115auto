<template>
  <el-card shadow="never" class="page-card" v-loading="loading">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>订阅追剧</span>
        <div class="sub-only-row">
          <el-switch v-model="subOnly" @change="toggleSubOnly" />
          <span class="tip">订阅模式：开启后仅转存命中订阅关键词的内容</span>
        </div>
      </div>
    </template>

    <div class="toolbar">
      <el-input v-model="addForm.keyword" maxlength="100" show-word-limit
                placeholder="关键词，如剧集名（必填，≤100 字）" style="width: 280px"
                @keyup.enter="add" />
      <el-input v-model="addForm.note" maxlength="200" placeholder="备注（可选）" style="width: 200px"
                @keyup.enter="add" />
      <el-button type="primary" :loading="adding" @click="add">添加订阅</el-button>
    </div>

    <el-table :data="items" empty-text="还没有订阅，在上方输入关键词后点「添加订阅」">
      <el-table-column prop="keyword" label="关键词" min-width="150" show-overflow-tooltip>
        <template #default="{ row }"><span class="mono">{{ row.keyword }}</span></template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.note || '-' }}</template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="!!row.enabled" @change="(v) => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="最近集数" width="100">
        <template #default="{ row }">{{ row.last_ep || '-' }}</template>
      </el-table-column>
      <el-table-column label="最近命中" min-width="180">
        <template #default="{ row }">
          <template v-if="row.last_match_at">
            <div>{{ fmtTime(row.last_match_at) }}</div>
            <div v-if="row.last_title" class="hit-title">{{ row.last_title }}</div>
          </template>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="190">
        <template #default="{ row }">
          <div class="sub-actions">
            <el-popconfirm title="重置后将清除该订阅的追更进度记忆，下次命中重新记录。确认重置？"
                           confirm-button-text="重置" cancel-button-text="取消" confirm-button-type="warning"
                           width="280" @confirm="resetProgress(row)">
              <template #reference><el-button type="warning" text>重置进度</el-button></template>
            </el-popconfirm>
            <el-popconfirm title="删除后将不再按该关键词自动追剧。确认删除？"
                           confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                           width="260" @confirm="del(row)">
              <template #reference><el-button type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtTime, saveOk } from '../api'

const items = ref([])
const subOnly = ref(false)
const loading = ref(false)
const adding = ref(false)
const addForm = reactive({ keyword: '', note: '' })

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/api/subscriptions')
    items.value = data.items || []
    subOnly.value = !!data.subscription_only
  } catch (e) { ElMessage.error(errMsg(e)) } finally { loading.value = false }
}

// 订阅模式开关：后端 PUT /api/settings 为按键合并，可单独提交
const toggleSubOnly = async (v) => {
  try {
    await api.put('/api/settings', { subscription_only: !!v })
    saveOk(v ? '订阅模式已开启' : '订阅模式已关闭', v ? '仅转存命中订阅关键词的内容' : '恢复转存全部内容')
  } catch (e) {
    subOnly.value = !v  // 保存失败时恢复开关显示
    ElMessage.error(errMsg(e))
  }
}

const add = async () => {
  const kw = addForm.keyword.trim()
  if (!kw) { ElMessage.warning('请输入订阅关键词'); return }
  if (adding.value) return
  adding.value = true
  try {
    await api.post('/api/subscriptions', { keyword: kw, note: addForm.note.trim() })
    ElMessage.success(`已添加订阅「${kw}」`)
    addForm.keyword = ''
    addForm.note = ''
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { adding.value = false }
}

const toggle = async (row, v) => {
  try {
    await api.put(`/api/subscriptions/${row.id}`, { enabled: !!v })
    row.enabled = v ? 1 : 0
    ElMessage.success(v ? `已启用订阅「${row.keyword}」` : `已停用订阅「${row.keyword}」`)
  } catch (e) {
    row.enabled = v ? 0 : 1
    ElMessage.error(errMsg(e))
  }
}

const resetProgress = async (row) => {
  try {
    await api.put(`/api/subscriptions/${row.id}`, { reset_progress: true })
    ElMessage.success(`已重置「${row.keyword}」的追更进度`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const del = async (row) => {
  try {
    await api.delete(`/api/subscriptions/${row.id}`)
    ElMessage.success(`已删除订阅「${row.keyword}」`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

onMounted(load)
</script>

<style scoped>
.tip { color: #999; font-size: 12px; font-weight: 400; }
.sub-only-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sub-actions { display: flex; align-items: center; flex-wrap: nowrap; gap: 6px; white-space: nowrap; }
.sub-actions :deep(.el-button) { margin-left: 0 !important; }
.hit-title { font-size: 12px; color: #999; margin-top: 2px; word-break: break-all; }
</style>
