<template>
  <div>
  <!-- 步骤 1：确认清理目录 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 1</span>
        <div>
          <div class="step-title">确认清理目录（仅按 sha1 判重）</div>
          <div class="step-desc">扫描指定目录中 sha1 完全相同的重复文件，sha1 为空的条目自动跳过；支持仅扫描当前层或含子目录。</div>
        </div>
      </div>
      <div class="step-ops">
        <el-button type="primary" size="small" @click="openDedupeRuleDialog()">+ 添加清理规则</el-button>
      </div>
    </div>

    <div v-if="dedupeRules.length" class="active-rules-table">
      <el-table :data="dedupeRules" size="small" stripe>
        <el-table-column prop="name" label="规则名称" min-width="120" />
        <el-table-column prop="source_dir" label="扫描目录" min-width="190" />
        <el-table-column label="扫描范围" width="105" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.include_subdirs ? 'primary' : 'info'">
              {{ row.include_subdirs ? '含子目录' : '仅当前层' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="忽略小于" width="95" align="center">
          <template #default="{ row }">{{ row.min_size_mb ? `${row.min_size_mb} MB` : '不限' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="75" align="center">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" size="small" @change="(v) => toggleDedupeRule(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="155" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!row.enabled" @click="previewDedupeRule(row)">扫描预览</el-button>
            <el-button link type="primary" size="small" @click="openDedupeRuleDialog(row)">编辑</el-button>
            <el-popconfirm
              title="删除这条清理规则？已产生的清理记录不会被删除。"
              confirm-button-text="删除"
              cancel-button-text="取消"
              confirm-button-type="danger"
              width="240"
              @confirm="delDedupeRule(row.id)"
            >
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div v-else class="empty-rules-box">
      <span>当前暂无清理规则。请点击右上角「+ 添加清理规则」配置需要扫描重复文件的目录。</span>
    </div>
  </div>

  <!-- 步骤 2：扫描预览与执行 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 2</span>
        <div>
          <div class="step-title">扫描预览与执行清理</div>
          <div class="step-desc">预览只读不删除；确认后每个重复组保留所选文件，其余批量移入 115 回收站，并写入独立清理账本。</div>
        </div>
      </div>
      <div class="step-actions">
        <span class="step-meta">
          启用规则 {{ activeDedupeRules.length }} 条 · 上次清理：{{ status.last_run.dedupe ? fmtTime(status.last_run.dedupe) : '尚未执行' }}
        </span>
        <el-tooltip :content="dedupeBtnTip" :disabled="!dedupeBtnDisabled" placement="top">
          <span>
            <el-button
              type="primary"
              size="default"
              :disabled="dedupeBtnDisabled"
              :loading="dedupePreviewing"
              @click="openDedupePreview()"
            >
              🔍 扫描并预览重复文件
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <div v-if="status.gate.active_mode === 'dedupe'" class="running-banner">
      <div class="rb-left">
        <el-tag type="primary" effect="dark" size="small">重复清理执行中</el-tag>
        <span class="rb-phase">{{ phaseLabel }}</span>
        <span class="rb-label">{{ activeProgressLabel }}</span>
      </div>
      <div class="rb-right">
        <el-progress
          v-if="progressPercent > 0"
          :percentage="progressPercent"
          :stroke-width="10"
          style="width: 180px"
        />
        <el-button type="danger" size="small" plain :loading="stopping" @click="stopMedia">
          安全中断（处理完当前文件后停止）
        </el-button>
      </div>
    </div>
  </div>

  <!-- 步骤 3：清理记录 -->
  <div class="step-card">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 3</span>
        <div>
          <div class="step-title">清理记录与回收站恢复</div>
          <div class="step-desc">每次清理逐条留痕（批次 / 文件名 / 路径 / sha1 / 状态），删除即移入 115 回收站，可前往网盘回收站还原。</div>
        </div>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="mb12"
      title="恢复方式：登录 115 网盘网页端 → 回收站，勾选对应文件后点击「还原」即可放回原目录。"
    />

    <div class="commit-bar">
      <div class="cb-stats">
        <el-select
          v-model="dedupeBatchFilter"
          placeholder="全部清理批次"
          clearable
          filterable
          size="small"
          style="width: 260px"
          @change="loadDedupeRecords"
        >
          <el-option
            v-for="b in dedupeBatches"
            :key="b.batch_id"
            :value="b.batch_id"
            :label="`${b.batch_id}（${b.deleted || 0} 项）`"
          />
        </el-select>
      </div>
      <div class="cb-actions">
        <span class="step-meta">
          共 {{ dedupeBatches.length }} 个批次 · 当前显示 {{ dedupeRecords.length }} 条
        </span>
        <el-button size="small" @click="loadDedupeRecords">🔄 刷新记录</el-button>
      </div>
    </div>

    <div v-if="dedupeRecords.length">
      <el-table :data="dedupeRecords" size="small" stripe max-height="460">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="清理时间" width="165">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="file_name" label="文件名" min-width="190" show-overflow-tooltip />
        <el-table-column prop="file_path" label="原路径" min-width="220" show-overflow-tooltip />
        <el-table-column label="大小" width="95">
          <template #default="{ row }">{{ fmtSizeMb(row.size) || '0 MB' }}</template>
        </el-table-column>
        <el-table-column label="sha1" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.sha1 || '').slice(0, 12) }}</template>
        </el-table-column>
        <el-table-column prop="batch_id" label="批次" width="135" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'deleted' ? 'success' : 'danger'">
              {{ row.status === 'deleted' ? '已入回收站' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div v-else class="empty-box">暂无清理记录。完成一次重复清理后，删除明细会记录在这里。</div>
  </div>

  <!-- 新增/编辑重复清理规则弹窗 -->
  <el-dialog
    v-model="dedupeRuleDialog.open"
    :title="dedupeRuleDialog.id ? '编辑重复清理规则' : '新增重复清理规则'"
    width="min(560px, 94vw)"
  >
    <el-form
      :model="dedupeRuleDialog"
      :label-width="isMobile ? 'auto' : '110px'"
      :label-position="isMobile ? 'top' : 'left'"
    >
      <el-form-item label="规则名称">
        <el-input v-model="dedupeRuleDialog.name" placeholder="例如：电影库重复清理（留空自动取目录名）" />
      </el-form-item>
      <el-form-item label="扫描目录" required>
        <div class="dir-selector-wrap">
          <div v-if="dedupeRuleDialog.source_dir" class="dir-selected-box">
            <span class="dir-icon">📁</span>
            <span class="dir-path-text" :title="dedupeRuleDialog.source_dir">{{ dedupeRuleDialog.source_dir }}</span>
            <el-button size="small" type="primary" plain @click="openDedupeDirPicker">重新选取</el-button>
          </div>
          <div v-else class="dir-unselected-box">
            <el-button type="primary" plain @click="openDedupeDirPicker">📁 选取网盘目录</el-button>
            <span class="required-warn">（* 必选：必须选取扫描目录）</span>
          </div>
        </div>
        <div class="tip-block">将在此目录内扫描 sha1 完全相同的重复文件（必须选取）</div>
      </el-form-item>
      <el-form-item label="扫描范围">
        <el-radio-group v-model="dedupeRuleDialog.include_subdirs">
          <el-radio-button :value="true">含子目录</el-radio-button>
          <el-radio-button :value="false">仅当前层</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="忽略小于">
        <div class="min-size-field">
          <div class="min-size-row">
            <el-input-number
              v-model="dedupeRuleDialog.min_size_mb"
              :min="0"
              :max="1024000"
              :step="10"
              :precision="0"
              :value-on-clear="0"
              style="width: 150px"
            />
            <span class="min-size-unit">MB</span>
          </div>
          <div class="tip-block">0 表示不忽略任何大小（仅限 0～1024000 整数）；小文件不参与判重</div>
        </div>
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="dedupeRuleDialog.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dedupeRuleDialog.open = false">取消</el-button>
      <el-tooltip content="必须选取扫描目录方可保存规则" :disabled="!!dedupeRuleDialog.source_dir" placement="top">
        <span>
          <el-button type="primary" :disabled="!dedupeRuleDialog.source_dir" :loading="savingDedupeRule" @click="saveDedupeRule">
            保存规则
          </el-button>
        </span>
      </el-tooltip>
    </template>
  </el-dialog>

  <!-- 重复文件预览与保留项选择弹窗 -->
  <el-dialog v-model="dedupePreview.visible" title="重复文件预览 — 确认后移入 115 回收站" width="min(980px, 96vw)" top="4vh">
    <div class="mb12" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
      <el-tag type="warning" effect="dark" size="small">重复组 {{ dedupePreview.stats.duplicate_groups || 0 }} 个</el-tag>
      <el-tag type="danger" size="small">可释放 {{ fmtSizeMb(dedupePreview.stats.freeable_bytes) || '0 MB' }}</el-tag>
      <el-tag size="small">已勾选删除 {{ dedupeDeleteCount }} 个</el-tag>
      <el-tag type="info" size="small">已扫描 {{ dedupePreview.stats.scanned || 0 }} 个文件</el-tag>
      <span class="step-meta">
        sha1 为空跳过 {{ dedupePreview.stats.no_sha1 || 0 }} · 小于阈值跳过 {{ dedupePreview.stats.small_skipped || 0 }}
      </span>
    </div>

    <div class="mb12" style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
      <el-input v-model="dedupeSearch" placeholder="搜索文件名 / 路径" clearable style="width: 260px" @input="dedupeGroupPage = 1" />
      <span class="step-meta">{{ dedupeFilteredGroups.length }} 组 · 当前页显示 {{ pagedDedupeGroups.length }} 组</span>
    </div>

    <div v-if="pagedDedupeGroups.length" class="dedupe-group-list">
      <div v-for="g in pagedDedupeGroups" :key="g.sha1" class="dedupe-group">
        <div class="dg-head">
          <el-tag size="small" effect="plain">sha1 {{ g.sha1.slice(0, 12) }}</el-tag>
          <span>每个 {{ fmtSizeMb(g.size) }}</span>
          <span>本组可释放 {{ fmtSizeMb(g.freeable) }}</span>
          <span>{{ g.items.length }} 个副本</span>
          <span style="flex: 1"></span>
          <el-checkbox v-model="dedupeSkip[g.sha1]" size="small">跳过本组</el-checkbox>
        </div>
        <div class="dg-items">
          <div v-for="it in g.items" :key="it.file_id" class="dg-item">
            <el-radio
              v-model="dedupeKeep[g.sha1]"
              :value="it.file_id"
              :disabled="dedupeSkip[g.sha1]"
              @change="(v) => onDedupeKeepChange(g, v)"
            >保留</el-radio>
            <div class="dg-info">
              <span class="dg-name">{{ it.file_name }}</span>
              <span class="dg-path">{{ it.file_path }}</span>
            </div>
            <el-checkbox
              v-model="dedupeDel[it.file_id]"
              size="small"
              :disabled="dedupeSkip[g.sha1] || dedupeKeep[g.sha1] === it.file_id"
            >移入回收站</el-checkbox>
          </div>
        </div>
        <div class="dg-tip">
          {{ dedupeSkip[g.sha1]
            ? '本组已跳过：不会执行任何删除'
            : `保留所选 1 个文件，本次删除 ${dedupeGroupDeleteCount(g)} 个（移入回收站）` }}
        </div>
      </div>
    </div>
    <el-empty v-else :image-size="70" description="没有扫描到 sha1 完全相同的重复文件" />

    <el-pagination
      v-if="dedupeGroupPageCount > 1"
      style="margin-top: 12px; justify-content: flex-end"
      layout="total, prev, pager, next"
      :total="dedupeFilteredGroups.length"
      :page-size="dedupeGroupPageSize"
      v-model:current-page="dedupeGroupPage"
    />

    <template #footer>
      <el-button :loading="dedupePreviewing" @click="rescanDedupe">重新扫描</el-button>
      <el-button @click="dedupePreview.visible = false">取消</el-button>
      <el-button
        type="danger"
        :loading="dedupeRunning"
        :disabled="!dedupeDeleteCount"
        @click="confirmDedupe"
      >
        🗑 确认执行（移入回收站 {{ dedupeDeleteCount }} 个文件）
      </el-button>
    </template>
  </el-dialog>

    <DirPicker v-model="dedupeDirPickerOpen" title="选择重复清理目录" @pick="onDedupeDirPicked" />
  </div>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { api, errMsg, fmtTime, toast } from '../../api'
import { runPreviewJob } from '../../composables/previewJob'
import DirPicker from '../DirPicker.vue'

const {
  status,
  isBusy,
  isMobile,
  phaseLabel,
  activeProgressLabel,
  progressPercent,
  stopping,
  stopMedia,
  loadStatus,
  fmtSizeMb,
} = inject('mediaCtx')

const dedupeDirPickerOpen = ref(false)
function openDedupeDirPicker() {
  dedupeDirPickerOpen.value = true
}
function onDedupeDirPicked(pickedPath) {
  dedupeRuleDialog.value.source_dir = pickedPath
  if (!dedupeRuleDialog.value.name.trim() && pickedPath) {
    const segs = pickedPath.split('/').filter(Boolean)
    if (segs.length) dedupeRuleDialog.value.name = `${segs[segs.length - 1]}重复清理`
  }
}

// ---------- 重复清理状态 ----------
const dedupeRules = ref([])
const activeDedupeRules = computed(() => dedupeRules.value.filter(r => !!r.enabled))
const dedupePreviewing = ref(false)
const dedupeRunning = ref(false)
const savingDedupeRule = ref(false)
const dedupeRecords = ref([])
const dedupeBatches = ref([])
const dedupeBatchFilter = ref('')

const dedupeBtnDisabled = computed(() =>
  !status.value.running || isBusy.value || !activeDedupeRules.value.length)

const dedupeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (activeDedupeRules.value.length === 0) return '暂无启用的清理规则，请先点击上方添加'
  if (isBusy.value) return '已有独占整理、刮削或清理任务运行中（严格互斥），请等待结束'
  return ''
})

async function loadDedupeRules() {
  try {
    const { data } = await api.get('/api/media/dedupe/rules')
    dedupeRules.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function loadDedupeRecords() {
  try {
    const { data } = await api.get('/api/media/dedupe/records', {
      params: { limit: 300, batch_id: dedupeBatchFilter.value || '' },
    })
    dedupeBatches.value = data.batches || []
    dedupeRecords.value = data.records || []
  } catch (e) { toast.error(errMsg(e)) }
}

// 重复清理规则弹窗（与整理/刮削规则完全独立）
const dedupeRuleDialog = ref({
  open: false, id: 0, name: '', source_dir: '', include_subdirs: true, min_size_mb: 0, enabled: true,
})


// ---------- 重复清理：规则 CRUD ----------
function openDedupeRuleDialog(row = null) {
  dedupeRuleDialog.value = row ? {
    open: true,
    id: row.id,
    name: row.name || '',
    source_dir: row.source_dir || '',
    include_subdirs: !!row.include_subdirs,
    min_size_mb: Number(row.min_size_mb || 0),
    enabled: !!row.enabled,
  } : {
    open: true, id: 0, name: '', source_dir: '', include_subdirs: true, min_size_mb: 0, enabled: true,
  }
}

async function saveDedupeRule() {
  if (!dedupeRuleDialog.value.source_dir) {
    toast.error('扫描目录为必选项，必须先选取网盘目录')
    return
  }
  const rawMin = dedupeRuleDialog.value.min_size_mb
  const minSizeMb = rawMin === '' || rawMin === null || rawMin === undefined ? 0 : Number(rawMin)
  if (!Number.isFinite(minSizeMb) || !Number.isInteger(minSizeMb) || minSizeMb < 0 || minSizeMb > 1024000) {
    toast.error('忽略体积请输入 0～1024000 之间的整数（单位 MB）')
    return
  }
  dedupeRuleDialog.value.min_size_mb = minSizeMb
  savingDedupeRule.value = true
  try {
    const payload = {
      name: (dedupeRuleDialog.value.name || '').trim(),
      source_dir: dedupeRuleDialog.value.source_dir,
      include_subdirs: !!dedupeRuleDialog.value.include_subdirs,
      min_size_mb: minSizeMb,
      enabled: !!dedupeRuleDialog.value.enabled,
    }
    if (dedupeRuleDialog.value.id) await api.put(`/api/media/dedupe/rules/${dedupeRuleDialog.value.id}`, payload)
    else await api.post('/api/media/dedupe/rules', payload)
    dedupeRuleDialog.value.open = false
    toast.success('清理规则保存成功')
    await Promise.all([loadDedupeRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { savingDedupeRule.value = false }
}

async function toggleDedupeRule(row, val) {
  try {
    await api.put(`/api/media/dedupe/rules/${row.id}`, {
      name: row.name || '',
      source_dir: row.source_dir,
      include_subdirs: !!row.include_subdirs,
      min_size_mb: Number(row.min_size_mb || 0),
      enabled: !!val,
    })
    await Promise.all([loadDedupeRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function delDedupeRule(id) {
  try {
    await api.delete(`/api/media/dedupe/rules/${id}`)
    toast.success('已删除清理规则')
    await Promise.all([loadDedupeRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

// ---------- 重复清理：扫描预览与确认执行 ----------
const dedupePreview = ref({ visible: false, ruleIds: [], groups: [], stats: {} })
const dedupeKeep = ref({})
const dedupeDel = ref({})
const dedupeSkip = ref({})
const dedupeSearch = ref('')
const dedupeGroupPage = ref(1)
const dedupeGroupPageSize = 20

const dedupeFilteredGroups = computed(() => {
  const kw = dedupeSearch.value.trim().toLowerCase()
  const groups = dedupePreview.value.groups || []
  if (!kw) return groups
  return groups.filter(g => (g.items || []).some(it =>
    `${it.file_name || ''} ${it.file_path || ''}`.toLowerCase().includes(kw)))
})
const dedupeGroupPageCount = computed(() =>
  Math.max(1, Math.ceil(dedupeFilteredGroups.value.length / dedupeGroupPageSize)))
const pagedDedupeGroups = computed(() =>
  dedupeFilteredGroups.value.slice(
    (dedupeGroupPage.value - 1) * dedupeGroupPageSize,
    dedupeGroupPage.value * dedupeGroupPageSize,
  ))
const dedupeGroupDeleteCount = (g) => {
  if (dedupeSkip.value[g.sha1]) return 0
  const keep = dedupeKeep.value[g.sha1] || g.keep_id
  return (g.items || []).filter((it) =>
    it.file_id !== keep && dedupeDel.value[it.file_id] !== false).length
}
const dedupeDeleteItems = computed(() => {
  const out = []
  for (const g of dedupePreview.value.groups || []) {
    if (dedupeSkip.value[g.sha1]) continue
    const keep = dedupeKeep.value[g.sha1] || g.keep_id
    for (const it of g.items || []) {
      if (it.file_id !== keep && dedupeDel.value[it.file_id] !== false) {
        out.push({ file_id: it.file_id, sha1: g.sha1, size: it.size })
      }
    }
  }
  return out
})
const dedupeDeleteCount = computed(() => dedupeDeleteItems.value.length)

// 切换保留项：新保留项取消删除勾选，其余副本恢复默认“删除”
const onDedupeKeepChange = (g, keepId) => {
  const next = { ...dedupeDel.value }
  for (const it of g.items || []) next[it.file_id] = it.file_id !== keepId
  dedupeDel.value = next
}

function applyDedupePreview(data, ruleIds) {
  const keep = {}
  const del = {}
  const skip = {}
  for (const g of data.groups || []) {
    keep[g.sha1] = g.keep_id
    skip[g.sha1] = false
    for (const it of g.items || []) del[it.file_id] = it.file_id !== g.keep_id
  }
  dedupeKeep.value = keep
  dedupeDel.value = del
  dedupeSkip.value = skip
  dedupePreview.value = {
    visible: true,
    ruleIds: [...ruleIds],
    groups: data.groups || [],
    stats: data.stats || {},
  }
  dedupeSearch.value = ''
  dedupeGroupPage.value = 1
}

async function previewDedupeRule(row) {
  await openDedupePreview([row.id])
}

async function openDedupePreview(targetRuleIds = null) {
  if (dedupePreviewing.value) return
  const ruleIds = Array.isArray(targetRuleIds) && targetRuleIds.length
    ? targetRuleIds
    : activeDedupeRules.value.map(r => r.id)
  if (!ruleIds.length) {
    toast.warning('暂无启用的清理规则，请先添加并启用规则')
    return
  }
  dedupePreviewing.value = true
  try {
    const data = await runPreviewJob('/api/media/dedupe/preview', { rule_ids: ruleIds },
      { text: '正在扫描重复文件，请勿进行其他操作……（按 Esc 可取消扫描）' })
    if (!data.ok) {
      toast.error(data.msg || '预览失败')
      return
    }
    if (data.truncated) toast.warning(data.msg || '本轮扫描达到预算上限，已返回部分结果')
    const blocked = data.stats?.blocked_rules || []
    if (blocked.length && !(data.groups || []).length) {
      toast.warning(blocked[0].msg || '源目录设置不合法，已跳过扫描')
      return
    }
    if (!(data.groups || []).length) {
      toast.info(`未发现 sha1 完全相同的重复文件（已扫描 ${data.stats?.scanned || 0} 个文件）`)
      return
    }
    applyDedupePreview(data, ruleIds)
  } catch (e) {
    if (e?.silent) return  // 等待超时：已由预览任务给出提示，扫描仍在后台继续
    if (e?.cancelled) toast.info('已取消扫描')
    else toast.error(errMsg(e))
  }
  finally { dedupePreviewing.value = false }
}

async function rescanDedupe() {
  const ruleIds = dedupePreview.value.ruleIds || []
  dedupePreview.value.visible = false
  await openDedupePreview(ruleIds.length ? ruleIds : null)
}

async function confirmDedupe() {
  if (!dedupeDeleteCount.value) {
    toast.info('每个重复组至少保留一个文件，当前没有可删除项')
    return
  }
  dedupeRunning.value = true
  try {
    const { data } = await api.post('/api/media/dedupe/run', {
      rule_ids: dedupePreview.value.ruleIds,
      delete_items: dedupeDeleteItems.value,
    })
    dedupePreview.value.visible = false
    toast.success(data.msg || `已发起重复清理（批次 ${data.batch_id}）`)
    await Promise.all([loadStatus(), loadDedupeRecords()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { dedupeRunning.value = false }
}

defineExpose({
  loadDedupeRules,
  loadDedupeRecords,
  hasRecords: () => dedupeRecords.value.length > 0,
})
</script>
