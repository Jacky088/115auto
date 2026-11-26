<template>
<!-- 整理预览弹窗（两段式执行第一步）：确认后才真正排队执行 -->
<el-dialog v-model="previewVisible" title="整理预览 — 将移动以下文件" :width="'min(860px, calc(100vw - 36px))'" align-center>
  <el-alert v-if="previewFromCache" type="warning" :closable="false" show-icon style="margin-bottom: 10px"
            title="暂存的预览缓存，确认后重新扫描执行" />
  <el-alert v-else type="info" :closable="false" show-icon style="margin-bottom: 10px"
            title="预览快照，确认后重新扫描执行" />
  <el-alert v-if="previewData?.risk_aborted" type="error" :closable="false" show-icon style="margin-bottom: 10px"
            title="预览过程触发 115 风控信号，已中止剩余规则的预览，以下仅为部分结果。" />
  <el-alert v-if="!previewRows.length" type="success" :closable="false" show-icon style="margin-bottom: 10px"
            title="本次没有需要移动的文件——如有拦截/跳过，原因与样例见下方统计" />
  <div v-if="previewCountdown > 0" class="stat-line">
    ⏱ {{ Math.floor(previewCountdown / 60) }} 分 {{ previewCountdown % 60 }} 秒后自动暂存
  </div>
  <div v-if="previewGroups.length" style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 8px">
    <span style="font-size: var(--fs-caption); color: var(--saas-text-sub)">按规则整组：</span>
    <el-checkbox
      v-for="g in previewGroups"
      :key="g.label"
      :model-value="g.allSelected"
      @change="(v) => togglePreviewGroup(g, v)"
    >{{ g.label }}（{{ g.ids.length }}）</el-checkbox>
  </div>
  <div v-if="previewRows.length" style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 8px">
    <el-input
      v-model="previewSearch"
      placeholder="搜索文件名 / 路径 / 命中词"
      clearable
      size="small"
      style="width: 240px"
      @input="previewPage = 1"
    />
    <span style="font-size: var(--fs-caption); color: var(--saas-text-sub)">
      {{ previewSearch.trim() ? `匹配 ${previewFilteredRows.length} / 共 ${previewRows.length} 条` : `共 ${previewRows.length} 条` }} · 已勾选 {{ previewSelectedCount }} 条
    </span>
  </div>
  <el-table :data="previewPagedRows" size="small" max-height="46vh">
    <template #empty><el-empty :image-size="60" :description="previewSearch.trim() ? '无匹配文件' : '暂无待移动文件'" /></template>
    <el-table-column width="50" align="center">
      <template #header>
        <el-checkbox
          :model-value="previewAllSelected"
          :indeterminate="previewPartSelected"
          @change="togglePreviewAll"
        />
      </template>
      <template #default="{ row }">
        <el-checkbox v-model="previewSelected[row.id]" />
      </template>
    </el-table-column>
    <el-table-column type="index" :index="(i) => (previewPage - 1) * previewPageSize + i + 1" label="#" width="55" />
    <el-table-column prop="name" label="文件名" min-width="220" show-overflow-tooltip />
    <el-table-column prop="from_path" label="源位置" width="150" show-overflow-tooltip />
    <el-table-column prop="dest_path" label="目标位置" min-width="220" show-overflow-tooltip />
    <el-table-column prop="size" label="大小" width="90">
      <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
    </el-table-column>
    <el-table-column prop="matched" label="命中词" width="120" show-overflow-tooltip />
    <el-table-column prop="rule_label" label="规则" width="170" show-overflow-tooltip />
  </el-table>
  <el-pagination
    v-if="previewPageCount > 1"
    style="margin-top: 10px; justify-content: flex-end"
    layout="total, prev, pager, next"
    :total="previewFilteredRows.length"
    :page-size="previewPageSize"
    v-model:current-page="previewPage"
  />
  <div v-if="previewData?.rules?.some((r) => r.skip_ep > 0 || r.skip_movie > 0 || r.skip_ambiguous > 0 || r.skip_junk > 0 || r.error)" style="margin-top: 10px">
    <div v-for="r in previewData.rules" :key="r.rule_id" style="font-size: var(--fs-caption); color: var(--saas-text-sub); line-height: 1.7">
      <span v-if="r.skip_ep > 0">「{{ r.target_dir }}」按季集查重将跳过 {{ r.skip_ep }} 个文件{{ r.skip_ep_samples?.length ? `（如 ${r.skip_ep_samples.slice(0, 2).join('、')}）` : '' }}；</span>
      <span v-if="r.skip_movie > 0">「{{ r.target_dir }}」电影库规则跳过 {{ r.skip_movie }} 个带集数标识的文件（留给剧集规则）；</span>
      <span v-if="r.skip_ambiguous > 0" style="color: var(--el-color-warning)">
        「{{ r.target_dir }}」同名歧义跳过 {{ r.skip_ambiguous }} 个文件（多库同名且无集数标识，留源目录待手动处理）{{ r.skip_ambiguous_samples?.length ? `（如 ${r.skip_ambiguous_samples.slice(0, 2).join('、')}）` : '' }}；
      </span>
      <span v-if="r.skip_junk > 0">「{{ r.target_dir }}」云下载垃圾过滤跳过 {{ r.skip_junk }} 个文件（广告域名/微小体积）；</span>
      <span v-if="r.error" style="color: var(--el-color-danger)">「{{ r.target_dir }}」{{ r.error }}；</span>
    </div>
  </div>
  <template v-if="previewRows.length" #footer>
    <el-button
      type="primary"
      :loading="previewConfirming"
      :disabled="!previewSelectedCount"
      @click="confirmRunAfterPreview"
    >
      确认执行（已选 {{ previewSelectedCount }} / {{ previewRows.length }} 个文件）
    </el-button>
  </template>
</el-dialog>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtSize } from '../../api'
import { runPreviewJob } from '../../composables/previewJob'

const emit = defineEmits(['started'])

const runningMove = ref(false)
const previewVisible = ref(false)
const previewData = ref(null)
const previewConfirming = ref(false)

const PREVIEW_CACHE_TTL = 10 * 60 * 1000
const previewCache = ref(null)      // { ts, data }
const previewFromCache = ref(false)
const previewCountdown = ref(0)     // 弹窗自动暂存倒计时（秒）= 缓存剩余寿命
let previewTimer = null

const openPreviewDialog = (data, fromCache) => {
  previewData.value = data
  previewFromCache.value = !!fromCache
  const sel = {}
  for (const r of (data?.rules || [])) {
    for (const it of (r.planned || [])) sel[it.id] = true
  }
  previewSelected.value = sel
  previewSearch.value = ''
  previewPage.value = 1
  previewVisible.value = true
  startPreviewCountdown()
}

const startPreviewCountdown = () => {
  stopPreviewCountdown()
  const tick = () => {
    if (!previewCache.value) { stopPreviewCountdown(); previewVisible.value = false; return }
    const remainMs = previewCache.value.ts + PREVIEW_CACHE_TTL - Date.now()
    previewCountdown.value = Math.max(0, Math.ceil(remainMs / 1000))
    if (remainMs <= 0) {
      stopPreviewCountdown()
      previewVisible.value = false
      ElMessage.info('预览已暂存，期内点击「立即排队执行」可直接调出')
    }
  }
  tick()
  previewTimer = setInterval(tick, 1000)
}

const stopPreviewCountdown = () => {
  if (previewTimer) { clearInterval(previewTimer); previewTimer = null }
}

const runPreview = async (ruleIds = null) => {
  if (runningMove.value) return
  const hasFilter = Array.isArray(ruleIds) && ruleIds.length > 0
  if (!hasFilter && previewCache.value && Date.now() - previewCache.value.ts < PREVIEW_CACHE_TTL) {
    openPreviewDialog(previewCache.value.data, true)
    return
  }
  runningMove.value = true
  let pv
  try {
    pv = await runPreviewJob('/api/auto-move/preview', hasFilter ? { rule_ids: ruleIds } : null,
      { text: '正在扫描待整理文件，请勿进行其他操作……（按 Esc 可取消扫描）' })
  } catch (e) {
    if (e?.silent) return  // 等待超时：已由预览任务给出提示，扫描仍在后台继续
    if (e?.cancelled) ElMessage.info('已取消扫描')
    else ElMessage.error(errMsg(e))
    return
  } finally {
    runningMove.value = false
  }
  if (!pv || !pv.ok) {
    ElMessage.warning((pv && pv.msg) || '预览失败')
    return
  }
  if (pv.truncated) ElMessage.warning(pv.msg || '本轮扫描达到预算上限，已返回部分结果')
  previewCache.value = { ts: Date.now(), data: pv }
  openPreviewDialog(pv, false)
}

const previewRows = computed(() => {
  const rows = []
  for (const r of (previewData.value?.rules || [])) {
    for (const it of (r.planned || [])) {
      rows.push({ rule_label: `${r.source_dir} ➔ ${r.target_dir}`, ...it })
    }
  }
  return rows
})

const previewSearch = ref('')
const previewPage = ref(1)
const previewPageSize = 50
const previewFilteredRows = computed(() => {
  const kw = previewSearch.value.trim().toLowerCase()
  if (!kw) return previewRows.value
  return previewRows.value.filter((r) =>
    [r.name, r.from_path, r.dest_path, r.matched, r.rule_label]
      .some((s) => String(s || '').toLowerCase().includes(kw)))
})
const previewPageCount = computed(() =>
  Math.max(1, Math.ceil(previewFilteredRows.value.length / previewPageSize)))
const previewPagedRows = computed(() =>
  previewFilteredRows.value.slice(
    (previewPage.value - 1) * previewPageSize,
    previewPage.value * previewPageSize,
  ))

const previewSelected = ref({})
const previewGroups = computed(() => {
  const map = new Map()
  for (const r of previewRows.value) {
    if (!map.has(r.rule_label)) map.set(r.rule_label, [])
    map.get(r.rule_label).push(r.id)
  }
  return [...map.entries()].map(([label, ids]) => ({
    label,
    ids,
    allSelected: ids.length > 0 && ids.every((id) => !!previewSelected.value[id]),
  }))
})
const previewSelectedIds = computed(() => previewRows.value
  .filter((r) => !!previewSelected.value[r.id])
  .map((r) => r.id))
const previewSelectedCount = computed(() => previewSelectedIds.value.length)
const previewAllSelected = computed(() =>
  previewRows.value.length > 0 && previewSelectedCount.value === previewRows.value.length)
const previewPartSelected = computed(() =>
  previewSelectedCount.value > 0 && previewSelectedCount.value < previewRows.value.length)
const togglePreviewAll = (val) => {
  const next = {}
  for (const r of previewRows.value) next[r.id] = !!val
  previewSelected.value = next
}
const togglePreviewGroup = (group, val) => {
  const next = { ...previewSelected.value }
  for (const id of group.ids) next[id] = !!val
  previewSelected.value = next
}

const confirmRunAfterPreview = async () => {
  if (!previewSelectedIds.value.length) {
    ElMessage.warning('请至少勾选一个需要执行分流整理的文件')
    return
  }
  previewConfirming.value = true
  try {
    const { data } = await api.post('/api/auto-move/run', {
      include_ids: previewSelectedIds.value,
    })
    if (data.ok) {
      previewVisible.value = false
      previewCache.value = null
      stopPreviewCountdown()
      ElMessage.info(data.msg || '已发起排队整理，正在等待转存/云下载队列终点...')
      emit('started', {
        gate_state: data.gate_state || 'draining',
        drain_status: data.drain_status || null,
      })
    } else {
      ElMessage.warning(data.msg || '执行未完成')
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    previewConfirming.value = false
  }
}

onUnmounted(stopPreviewCountdown)

defineExpose({
  runPreview,
  runningMove,
})
</script>
