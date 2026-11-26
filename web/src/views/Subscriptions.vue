<template>
  <el-card shadow="never" class="page-card" v-loading="loading">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>订阅追剧</span>
        <span class="tip">可在「账号管理」为每个频道单独设置「仅订阅」或「全盘转存」</span>
      </div>
    </template>

    <!-- 订阅关键词列表 -->
    <div class="sub-section">
      <div class="sub-title">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
          <span>📋 订阅关键词</span>
          <el-tag size="small" class="count-tag">共 {{ items.length }} 条订阅</el-tag>
          <el-tag size="small" :type="qualityPolicyOn ? 'success' : 'info'" effect="plain">
            质量策略：{{ qualityPolicyOn ? '已开启' : '未开启' }}
          </el-tag>
          <el-tag v-if="qualityPolicyOn" size="small" :type="washEnabled ? 'success' : 'info'" effect="plain">
            洗版：{{ washEnabled ? '已开启' : '未开启' }}
          </el-tag>
          <el-button link type="primary" size="small" @click="$router.push('/settings?tab=wash')">
            {{ qualityPolicyOn ? '调整画质 / 洗版 →' : '去开启质量策略 →' }}
          </el-button>
        </div>
        <span class="tip">命中频道消息后自动转存并追更；在下方 TMDB 海报墙点选「订阅」即可添加</span>
      </div>
      <el-table :data="pagedSubs">
        <template #empty>
          <el-empty :image-size="60" description="还没有订阅，可在下方浏览或搜索后点击「订阅」添加" />
        </template>
        <el-table-column prop="keyword" label="关键词" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="sub-keyword">{{ row.keyword }}</span>
            <div v-if="row.aliases && row.aliases.length" class="sub-alias"
                 :title="row.aliases.join('、')">别名：{{ row.aliases.join('、') }}</div>
            <div v-if="row.exclude_keywords && row.exclude_keywords.length" class="sub-alias"
                 :title="row.exclude_keywords.join('、')">排除：{{ row.exclude_keywords.join('、') }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" @change="(v) => toggle(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="最低画质" width="124">
          <template #default="{ row }">
            <el-tooltip :disabled="qualityPolicyOn" content="需先在「系统设置 → 洗版升级」开启质量策略" placement="top" effect="dark">
              <el-select :model-value="row.min_quality || ''" size="small"
                         :disabled="!qualityPolicyOn"
                         @change="(v) => setMinQuality(row, v)">
                <el-option v-for="o in QUALITY_OPTS" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </el-tooltip>
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
        <el-table-column label="操作" width="304" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button type="primary" text @click="openEdit(row)">编辑</el-button>
              <el-button type="primary" text @click="openBackfill(row)">向前补全</el-button>
              <el-button type="warning" text @click="resetProgress(row)">重置进度</el-button>
              <el-button type="danger" text @click="del(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="items.length > subPageSize" class="sub-pager">
        <el-pagination v-model:current-page="subPage" :page-size="subPageSize"
                       :total="items.length" layout="total, prev, pager, next" size="small" />
      </div>
    </div>

    <el-divider />

    <TmdbDiscoverPanel
      :tmdb-key="tmdbKey"
      :subscriptions="items"
      @key-updated="(k) => { tmdbKey = k }"
      @subscribed="loadSubsOnly"
      @edit-sub="openEdit"
      @configure-sub="openEdit"
      @backfill-sub="openBackfill"
    />
  </el-card>

  <BackfillDialog ref="backfillDialogRef" />
  <SubEditDialog v-model="editOpen" :row="editRow" :quality-policy-on="qualityPolicyOn" @saved="loadSubsOnly" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtTime } from '../api'
import { confirmAction } from '../composables/confirmDialog'
import BackfillDialog from '../components/BackfillDialog.vue'
import SubEditDialog from '../components/subscriptions/SubEditDialog.vue'
import TmdbDiscoverPanel from '../components/subscriptions/TmdbDiscoverPanel.vue'

const items = ref([])
const loading = ref(false)
const tmdbKey = ref('')
const qualityPolicyOn = ref(false)
const washEnabled = ref(false)
const subPage = ref(1)
const subPageSize = 15
const pagedSubs = computed(() => {
  const start = (subPage.value - 1) * subPageSize
  return items.value.slice(start, start + subPageSize)
})

const QUALITY_OPTS = [
  { label: '跟随全局', value: '' },
  { label: '720p', value: '720p' },
  { label: '1080p', value: '1080p' },
  { label: '2160p', value: '2160p' },
]

const clampPage = () => {
  const maxPage = Math.max(1, Math.ceil(items.value.length / subPageSize))
  if (subPage.value > maxPage) subPage.value = maxPage
}

const loadSubsOnly = async () => {
  try {
    const sub = await api.get('/api/subscriptions')
    items.value = sub.data.items || []
    clampPage()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const load = async () => {
  loading.value = true
  try {
    const [sub, st] = await Promise.all([
      api.get('/api/subscriptions'),
      api.get('/api/settings').catch(() => null),
    ])
    items.value = sub.data.items || []
    clampPage()
    if (st?.data) {
      tmdbKey.value = String(st.data.tmdb_api_key || '')
      qualityPolicyOn.value = !!st.data.quality_policy_enabled
      washEnabled.value = !!st.data.wash_enabled && !!String(st.data.wash_archive_dir || '').trim()
    }
  } catch (e) { ElMessage.error(errMsg(e)) } finally { loading.value = false }
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

const setMinQuality = async (row, v) => {
  try {
    await api.put(`/api/subscriptions/${row.id}`, { min_quality: v })
    row.min_quality = v
    ElMessage.success(v ? `「${row.keyword}」最低画质已设为 ${v}` : `「${row.keyword}」已改为跟随全局画质`)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const resetProgress = async (row) => {
  if (!await confirmAction({
    title: `确认重置「${row.keyword}」的追更进度？`,
    message: '已记录的追更集数进度将被清空，下次命中资源时重新记录。',
    confirmText: '确认重置',
    danger: true,
  })) return
  try {
    await api.put(`/api/subscriptions/${row.id}`, { reset_progress: true })
    ElMessage.success(`已重置「${row.keyword}」的追更进度`)
    loadSubsOnly()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const editOpen = ref(false)
const editRow = ref(null)
const openEdit = (row) => {
  editRow.value = row
  editOpen.value = true
}
const backfillDialogRef = ref(null)
const openBackfill = (row) => backfillDialogRef.value?.openForSubscription(row)

const del = async (row) => {
  if (!await confirmAction({
    title: `确认删除订阅「${row.keyword}」？`,
    message: '删除后将不再按该关键词自动追剧与推送进度通知；已转存的资源与历史记录不受影响。',
    confirmText: '确认删除',
    danger: true,
  })) return
  try {
    await api.delete(`/api/subscriptions/${row.id}`)
    ElMessage.success(`已删除订阅「${row.keyword}」`)
    loadSubsOnly()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

onMounted(load)
</script>

<style scoped>
.sub-keyword { font-weight: 500; font-size: var(--fs-body); }
.tip { color: var(--saas-text-sub); font-size: var(--fs-caption); font-weight: 400; }
.hit-title { font-size: var(--fs-caption); color: var(--text-muted); margin-top: 2px; word-break: break-all; }
.sub-alias { font-size: var(--fs-caption); color: var(--text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sub-pager { display: flex; justify-content: flex-end; margin-top: 10px; }
.sub-section { margin-bottom: 4px; min-width: 0; }
.sub-title {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-weight: 700; font-size: var(--fs-body); color: var(--saas-text-main);
  margin-bottom: 12px; padding: 9px 14px;
  background: var(--subcard-head-bg); border: 1px solid var(--saas-line); border-radius: var(--r-md);
}
.sub-title .tip { font-weight: 400; }
</style>
