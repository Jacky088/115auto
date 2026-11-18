<template>
  <div>

  <!-- 步骤 1：确认整理目录 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 1</span>
        <div>
          <div class="step-title">确认整理目录（源目录 ➔ 目标目录）</div>
          <div class="step-desc">将包含杂乱命名与层级的源目录文件，标准化重命名、建立季夹并归档到目标规范目录。</div>
        </div>
      </div>
      <div class="step-ops">
        <el-button type="primary" size="small" @click="openOrganizeRuleDialog()">+ 添加整理规则</el-button>
      </div>
    </div>

    <div v-if="organizeRules.length" class="active-rules-table">
      <el-table :data="organizeRules" size="small" stripe>
        <el-table-column prop="name" label="规则名称" min-width="120" />
        <el-table-column prop="source_dir" label="源扫描目录（杂乱文件）" min-width="180" />
        <el-table-column label="目标整理目录（规范存放）" min-width="180">
          <template #default="{ row }">
            <span :class="{ 'text-muted': !row.target_dir || row.target_dir === row.source_dir }">
              {{ row.target_dir || (row.source_dir + '（默认在源目录整理）') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="媒体类型" width="125" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.media_type === 'movie'" size="small" type="primary" effect="plain">仅电影</el-tag>
            <el-tag v-else-if="row.media_type === 'tv'" size="small" type="success" effect="plain">仅剧集/动漫</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">全部处理(不限)</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="75" align="center">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" size="small" @change="(v) => toggleRuleEnabled(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openOrganizeRuleDialog(row)">编辑</el-button>
            <el-popconfirm
              title="删除这条整理规则？删除后无法恢复。"
              confirm-button-text="删除"
              cancel-button-text="取消"
              confirm-button-type="danger"
              width="220"
              @confirm="delRule(row.id)"
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
      <span>当前暂无整理规则。请点击右上角「+ 添加整理规则」配置杂乱源目录与归档目标目录。</span>
    </div>
  </div>

  <!-- 步骤 2：运行目录整理 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 2</span>
        <div>
          <div class="step-title">运行目录整理</div>
          <div class="step-desc">按电影/剧集标准层级规范重命名与移动、配套字幕自动随动、单线程顺序防风控；建议先 Dry-Run 模拟预览。</div>
        </div>
      </div>
      <div class="step-actions">
        <span class="step-meta">
          启用规则 {{ organizeRules.length }} 条 · 上次运行：{{ status.last_run.organize ? fmtTime(status.last_run.organize) : '尚未执行' }}
        </span>
        <el-button size="default" :disabled="isBusy" :loading="previewing" @click="openPreview">
          🔍 预览整理效果 (Dry-Run)
        </el-button>
        <el-tooltip :content="organizeBtnTip" :disabled="!organizeBtnDisabled" placement="top">
          <span>
            <el-button
              type="primary"
              size="default"
              :disabled="organizeBtnDisabled"
              :loading="startingOrganize || status.gate.organize_running"
              @click="runOrganize"
            >
              🚀 运行目录整理
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <!-- 整理运行中的进度展示与安全中断 -->
    <div v-if="status.gate.active_mode === 'organize'" class="running-banner">
      <div class="rb-left">
        <el-tag type="primary" effect="dark" size="small">目录整理执行中</el-tag>
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

  <!-- 步骤 3：整理结果与操作账本（彻底剥离刮削概念） -->
  <div class="step-card">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 3</span>
        <div>
          <div class="step-title">整理结果与操作账本</div>
          <div class="step-desc">每次 115 重命名与移动（含配套字幕随动）均留痕记录，支持单条或整批无损恢复原文件名与路径。</div>
        </div>
      </div>
    </div>

    <!-- 统一暂存/筛选控制台（与刮削完全一致的胶囊筛选按钮组） -->
    <div class="commit-bar">
      <div class="cb-stats">
        <el-radio-group v-model="recordFilter" size="small">
          <el-radio-button value="all">全部记录 ({{ recordCounts.total }})</el-radio-button>
          <el-radio-button value="0">已生效 ({{ recordCounts.active }})</el-radio-button>
          <el-radio-button value="1">已撤销 ({{ recordCounts.undone }})</el-radio-button>
        </el-radio-group>
      </div>
      <div class="cb-actions">
        <el-button size="small" @click="loadRecords">🔄 刷新结果</el-button>
      </div>
    </div>

    <div v-if="records.length" class="records-table-wrap">
      <el-table :data="pagedRecords" stripe size="small">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="orig_name" label="原文件名" min-width="190" show-overflow-tooltip />
        <el-table-column prop="dest_name" label="整理后规范文件名" min-width="190" show-overflow-tooltip />
        <el-table-column prop="dest_path" label="目标规范目录" min-width="180" show-overflow-tooltip />
        <el-table-column label="动作" width="105">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.action }}</el-tag></template>
        </el-table-column>
        <el-table-column label="批次" width="120" prop="batch_id" />
        <el-table-column label="状态" width="85">
          <template #default="{ row }">
            <el-tag size="small" :type="row.undone ? 'info' : 'success'">{{ row.undone ? '已撤销' : '已生效' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <template v-if="!row.undone">
              <el-popconfirm
                title="撤销这条整理？文件将重命名并移回源目录。"
                confirm-button-text="撤销"
                cancel-button-text="取消"
                confirm-button-type="warning"
                width="240"
                @confirm="undoOne(row.id)"
              >
                <template #reference>
                  <el-button link type="warning" size="small">撤销此条</el-button>
                </template>
              </el-popconfirm>
              <el-popconfirm
                title="撤销本批次全部整理？文件将批量移回源目录。"
                confirm-button-text="撤销同批"
                cancel-button-text="取消"
                confirm-button-type="danger"
                width="260"
                @confirm="undoBatch(row.batch_id)"
              >
                <template #reference>
                  <el-button link type="danger" size="small">撤销同批</el-button>
                </template>
              </el-popconfirm>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <!-- 整理结果分页栏（与刮削分页完全对齐） -->
      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="recordPage"
          v-model:page-size="recordPageSize"
          :page-sizes="[10, 15, 20, 50]"
          :total="records.length"
          :layout="paginationLayout"
          :pager-count="isMobile ? 5 : 7"
          background
          size="small"
        />
      </div>
    </div>
    <div v-else class="empty-box">
      当前暂无目录整理记录。确认上方整理目录后，点击「运行目录整理」或「预览整理效果」即可开始。
    </div>
  </div>

  <!-- Dry-Run 预览抽屉 -->
  <el-drawer v-model="previewDrawer.open" title="目录整理 Dry-Run 预览（只读不改盘）" size="72%">
    <div class="mb12">
      共扫描到 <b>{{ previewDrawer.total }}</b> 个视频/原盘项目，其中
      <el-tag type="warning" size="small">{{ previewDrawer.pending_count }} 项需要调整命名/位置</el-tag>
      ，其余已符合规范结构。
      <div v-if="previewDrawer.scan_stats" class="stat-line" style="margin-top: var(--sp-1)">
        已过滤：非视频 {{ previewDrawer.scan_stats.non_video }} ·
        类型互斥 {{ previewDrawer.scan_stats.media_type_skip }} ·
        花絮广告 {{ previewDrawer.scan_stats.extra_skip }} ·
        其他规则认领 {{ previewDrawer.scan_stats.dedup_seen }}
      </div>
    </div>
    <el-table :data="previewDrawer.items" size="small" stripe max-height="72vh">
      <el-table-column prop="orig_path" label="当前 115 路径" min-width="230" show-overflow-tooltip />
      <el-table-column label="整理后规范路径" min-width="250" show-overflow-tooltip>
        <template #default="{ row }">{{ row.target_path }}/{{ row.target_name }}</template>
      </el-table-column>
      <el-table-column label="类型" width="75">
        <template #default="{ row }"><el-tag size="small">{{ row.media_type === 'tv' ? '剧集' : '电影' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="字幕" width="60" align="center" prop="subtitle_count" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.already_ok ? 'success' : 'warning'">
            {{ row.already_ok ? '已规范跳过' : '待重命名/移动' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-drawer>


  <el-dialog v-model="orgPreview.visible" title="目录整理预览 — 确认后执行" :width="'min(920px, 96vw)'" top="5vh">
    <div class="mb12" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
      <el-tag type="warning" effect="dark" size="small">待整理 {{ orgPreview.pending }} 条</el-tag>
      <el-tag type="success" size="small">已符合规范 {{ orgPreview.total - orgPreview.pending }} 条</el-tag>
      <el-tag size="small">涉及 {{ orgGroups.length }} 个目标目录</el-tag>
      <span style="font-size: var(--fs-caption); color: var(--saas-text-sub)">
        {{ orgFromCache ? '暂存的预览缓存，确认后重新扫描执行' : '预览快照，确认后重新扫描执行' }}
      </span>
    </div>
    <div v-if="orgPreview.scan_stats" class="skip-stats">
      已过滤：非视频 {{ orgPreview.scan_stats.non_video }} ·
      类型互斥 {{ orgPreview.scan_stats.media_type_skip }} ·
      花絮广告 {{ orgPreview.scan_stats.extra_skip }} ·
      其他规则认领 {{ orgPreview.scan_stats.dedup_seen }}
    </div>
    <div v-if="orgCountdown > 0" class="stat-line">
      ⏱ {{ Math.floor(orgCountdown / 60) }} 分 {{ orgCountdown % 60 }} 秒后自动暂存
    </div>
    <div v-if="orgPreview.scan_stats" style="margin-bottom: 8px; font-size: var(--fs-caption); color: var(--saas-text-sub)">
      已过滤：非视频 {{ orgPreview.scan_stats.non_video }} ·
      类型互斥 {{ orgPreview.scan_stats.media_type_skip }} ·
      花絮广告 {{ orgPreview.scan_stats.extra_skip }} ·
      其他规则认领 {{ orgPreview.scan_stats.dedup_seen }}
    </div>
    <div v-if="orgCountdown > 0" style="margin-bottom: 8px; font-size: var(--fs-caption); color: var(--saas-text-sub)">
      ⏱ {{ Math.floor(orgCountdown / 60) }} 分 {{ orgCountdown % 60 }} 秒后自动暂存
    </div>

    <el-collapse v-if="orgGroups.length" v-model="orgCollapseNames" class="mb12">
      <el-collapse-item :title="`分组摘要（${orgGroups.length} 个目标目录）`" name="groups">
        <div v-for="g in orgGroups" :key="g.path"
             style="display: flex; justify-content: space-between; gap: 10px; padding: 3px 4px; font-size: var(--fs-caption); cursor: pointer"
             @click="orgGroupFilter = orgGroupFilter === g.path ? '' : g.path; orgPage = 1">
          <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                :style="orgGroupFilter === g.path ? 'color: var(--el-color-primary); font-weight: 600' : ''">
            {{ g.path }}
          </span>
          <span style="flex-shrink: 0; color: var(--saas-text-sub); display: flex; align-items: center; gap: 8px">
            <el-checkbox
              :model-value="orgGroupAllSelected(g.path)"
              size="small"
              @click.stop
              @change="(v) => toggleOrgGroup(g.path, v)"
            >整组执行</el-checkbox>
            {{ g.count }} 条
          </span>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="mb12" style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
      <el-input v-model="orgSearch" placeholder="搜索文件名 / 剧名 / 路径" clearable style="width: 240px"
                @input="orgPage = 1" />
      <el-select v-model="orgGroupFilter" placeholder="按目标目录筛选" clearable filterable
                 style="width: 280px" @change="orgPage = 1">
        <el-option v-for="g in orgGroups" :key="g.path" :value="g.path"
                   :label="`${g.path}（${g.count}）`" />
      </el-select>
      <span style="font-size: var(--fs-caption); color: var(--saas-text-sub)">
        {{ orgFiltered.length }} 条{{ orgGroupFilter ? '（已筛选）' : '' }} · 已勾选 {{ orgSelectedCount }} / {{ orgPendingItems.length }} 条
      </span>
      <span style="font-size: var(--fs-caption); color: var(--saas-text-sub)">
        取消勾选即本次不执行；预览后新增文件不会处理
      </span>
    </div>

    <el-table :data="orgPagedRows" size="small" stripe max-height="44vh">
      <template #empty><el-empty :image-size="60" description="无匹配条目" /></template>
      <el-table-column width="50" align="center">
        <template #header>
          <el-checkbox
            :model-value="orgAllSelected"
            :indeterminate="orgPartSelected"
            @change="toggleOrgAll"
          />
        </template>
        <template #default="{ row }">
          <el-checkbox v-model="orgSelected[row.file_id]" />
        </template>
      </el-table-column>
      <el-table-column type="index" :index="(i) => (orgPage - 1) * orgPageSize + i + 1" width="55" />
      <el-table-column prop="orig_name" label="文件名" min-width="230" show-overflow-tooltip />
      <el-table-column label="解析结果" width="210" show-overflow-tooltip>
        <template #default="{ row }">
          {{ orgParsedLabel(row) }}
          <el-tag v-if="row.is_bdmv" size="small" type="info" style="margin-left: 4px">原盘</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="现位置 → 目标" min-width="300" show-overflow-tooltip>
        <template #default="{ row }">
          <span style="color: var(--saas-text-sub)">{{ row.orig_path }}</span>
          <span style="margin: 0 4px">➔</span>
          <span>{{ row.target_path }}/{{ row.target_name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.media_type === 'tv' ? 'success' : 'primary'">
            {{ row.media_type === 'tv' ? '剧' : '影' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination v-if="orgPageCount > 1"
                   style="margin-top: 12px; justify-content: flex-end"
                   layout="total, prev, pager, next" :total="orgFiltered.length"
                   :page-size="orgPageSize" v-model:current-page="orgPage" />

    <template #footer>
      <el-button @click="orgRescan" :loading="startingOrganize">重新扫描</el-button>
      <el-button @click="orgPreview.visible = false">取消</el-button>
      <el-button type="primary" :loading="orgConfirming" :disabled="!orgSelectedCount" @click="confirmRunOrganize">
        🚀 确认执行（已选 {{ orgSelectedCount }} / {{ orgPreview.pending }} 个文件）
      </el-button>
    </template>
  </el-dialog>
  </div>
</template>

<script setup>
import { computed, inject, onUnmounted, ref, watch } from 'vue'
import { api, errMsg, fmtTime, toast } from '../../api'
import { runPreviewJob } from '../../composables/previewJob'

const {
  status,
  organizeRules,
  activeOrganizeRules,
  isBusy,
  isMobile,
  paginationLayout,
  phaseLabel,
  activeProgressLabel,
  progressPercent,
  stopping,
  stopMedia,
  loadStatus,
  openOrganizeRuleDialog,
  toggleRuleEnabled,
  delRule,
} = inject('mediaCtx')

const recordFilter = ref('all')
const recordPage = ref(1)
const recordPageSize = ref(15)
const rawRecords = ref([])
const startingOrganize = ref(false)
const previewing = ref(false)

const records = computed(() => {
  if (recordFilter.value === '0') return rawRecords.value.filter(r => !r.undone)
  if (recordFilter.value === '1') return rawRecords.value.filter(r => !!r.undone)
  return rawRecords.value
})
const recordCounts = computed(() => {
  const total = rawRecords.value.length
  let active = 0
  let undone = 0
  for (const r of rawRecords.value) {
    if (r.undone) undone++
    else active++
  }
  return { total, active, undone }
})
const pagedRecords = computed(() => {
  const start = (recordPage.value - 1) * recordPageSize.value
  return records.value.slice(start, start + recordPageSize.value)
})
watch(recordFilter, () => { recordPage.value = 1 })

const organizeBtnDisabled = computed(() => !status.value.running || isBusy.value || activeOrganizeRules.value.length === 0)
const organizeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (activeOrganizeRules.value.length === 0) return '暂无启用的整理目录规则，请先点击上方添加'
  if (isBusy.value) return '已有独占整理或刮削任务运行中（严格互斥），请等待结束'
  return ''
})

async function loadRecords() {
  try {
    const { data } = await api.get('/api/media/records', { params: { limit: 200 } })
    rawRecords.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

// 预览缓存与自动暂存：与转存分流同一套交互——扫描结果缓存 10 分钟，
// 期内再次点击直接调出（确认执行仍会重新扫描，文件有变动按最新状态整理）
const ORG_PREVIEW_CACHE_TTL = 10 * 60 * 1000
const orgPreviewCache = ref(null)   // { ts, data }
const orgFromCache = ref(false)
const orgCountdown = ref(0)         // 弹窗自动暂存倒计时（秒）= 缓存剩余寿命
let orgPreviewTimer = null

const openOrgPreviewDialog = (data, fromCache) => {
  orgPreview.value = {
    visible: true,
    items: data.items || [],
    pending: data.pending_count,
    total: data.total,
    scan_stats: data.scan_stats || null,
  }
  const sel = {}
  for (const it of (data.items || [])) {
    if (!it.already_ok && it.file_id) sel[it.file_id] = true
  }
  orgSelected.value = sel
  orgPage.value = 1
  orgSearch.value = ''
  orgGroupFilter.value = ''
  orgCollapseNames.value = []
  orgFromCache.value = !!fromCache
  startOrgCountdown()
}
const startOrgCountdown = () => {
  stopOrgCountdown()
  const tick = () => {
    if (!orgPreviewCache.value) { stopOrgCountdown(); orgPreview.value.visible = false; return }
    const remainMs = orgPreviewCache.value.ts + ORG_PREVIEW_CACHE_TTL - Date.now()
    orgCountdown.value = Math.max(0, Math.ceil(remainMs / 1000))
    if (remainMs <= 0) {
      // 10 分钟未响应：自动暂存关闭（结果已在缓存，期内再次点击可直接调出）
      stopOrgCountdown()
      orgPreview.value.visible = false
      toast.info('预览已暂存：缓存有效期内再次点击「运行目录整理」可直接调出，无需重新扫描')
    }
  }
  tick()
  orgPreviewTimer = setInterval(tick, 1000)
}
const stopOrgCountdown = () => {
  if (orgPreviewTimer) { clearInterval(orgPreviewTimer); orgPreviewTimer = null }
}

async function runOrganize() {
  if (startingOrganize.value) return
  // 缓存命中：10 分钟内的预览结果直接拉起，不重复消耗 115 扫描请求
  if (orgPreviewCache.value && Date.now() - orgPreviewCache.value.ts < ORG_PREVIEW_CACHE_TTL) {
    openOrgPreviewDialog(orgPreviewCache.value.data, true)
    return
  }
  // 两段式执行：先 dry-run 预览待整理清单，用户在弹窗确认后才真正发起。
  // 异步扫描任务 + 轮询进度：大目录不再阻塞请求超时，按 Esc 可取消
  startingOrganize.value = true
  let data
  try {
    data = await runPreviewJob('/api/media/organize/preview', {},
      { text: '正在扫描待整理媒体，请勿进行其他操作……（按 Esc 可取消扫描）' })
  } catch (e) {
    if (e?.silent) return  // 等待超时：已由预览任务给出提示，扫描仍在后台继续
    if (e?.cancelled) toast.info('已取消扫描')
    else toast.error(errMsg(e))
    return
  } finally {
    startingOrganize.value = false
  }
  if (!data.ok) {
    toast.error(data.msg || '预览失败')
    return
  }
  if (data.truncated) toast.warning(data.msg || '本轮扫描达到预算上限，已返回部分结果')
  if (!data.pending_count) {
    toast.info(`当前没有需要整理的文件（已符合规范 ${data.total} 条）`)
    return
  }
  orgPreviewCache.value = { ts: Date.now(), data }
  openOrgPreviewDialog(data, false)
}

const orgRescan = async () => {
  orgPreview.value.visible = false
  orgPreviewCache.value = null
  stopOrgCountdown()
  await runOrganize()
}

// ---- 整理预览确认弹窗（分层应对大清单：汇总 → 分组摘要 → 搜索/筛选 + 前端分页） ----
const orgPreview = ref({ visible: false, items: [], pending: 0, total: 0 })
const orgSearch = ref('')
const orgGroupFilter = ref('')
const orgPage = ref(1)
const orgPageSize = 50
const orgConfirming = ref(false)
const orgCollapseNames = ref([])

// 只列「待整理」条目；已符合规范的仅在统计头计数
const orgPendingItems = computed(() =>
  (orgPreview.value.items || []).filter((it) => !it.already_ok))

// 分组摘要：按目标目录（剧/季）聚合，错位归档在此层面一眼可见
const orgGroups = computed(() => {
  const map = new Map()
  for (const it of orgPendingItems.value) {
    const key = it.target_path || '(未知目标)'
    const g = map.get(key) || { path: key, count: 0 }
    g.count += 1
    map.set(key, g)
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count)
})

// 本地搜索 + 按目标目录筛选（纯前端过滤，零请求）
const orgFiltered = computed(() => {
  const kw = orgSearch.value.trim().toLowerCase()
  return orgPendingItems.value.filter((it) => {
    if (orgGroupFilter.value && (it.target_path || '') !== orgGroupFilter.value) return false
    if (!kw) return true
    return [it.orig_name, it.orig_path, it.target_name, it.target_path,
            it.parsed_title, it.year].some((s) => String(s || '').toLowerCase().includes(kw))
  })
})
const orgPageCount = computed(() => Math.max(1, Math.ceil(orgFiltered.value.length / orgPageSize)))
const orgPagedRows = computed(() =>
  orgFiltered.value.slice((orgPage.value - 1) * orgPageSize, orgPage.value * orgPageSize))

// 预览勾选：默认全选，取消勾选即本次不执行；确认时仅提交勾选的 file_id
const orgSelected = ref({})
const orgSelectedIds = computed(() => orgPendingItems.value
  .filter((it) => !!orgSelected.value[it.file_id])
  .map((it) => it.file_id))
const orgSelectedCount = computed(() => orgSelectedIds.value.length)
const orgAllSelected = computed(() =>
  orgPendingItems.value.length > 0 && orgSelectedCount.value === orgPendingItems.value.length)
const orgPartSelected = computed(() =>
  orgSelectedCount.value > 0 && orgSelectedCount.value < orgPendingItems.value.length)
const orgGroupIds = (path) => orgPendingItems.value
  .filter((it) => (it.target_path || '(未知目标)') === path)
  .map((it) => it.file_id)
const orgGroupAllSelected = (path) => {
  const ids = orgGroupIds(path)
  return ids.length > 0 && ids.every((id) => !!orgSelected.value[id])
}
const toggleOrgAll = (val) => {
  const next = {}
  for (const it of orgPendingItems.value) next[it.file_id] = !!val
  orgSelected.value = next
}
const toggleOrgGroup = (path, val) => {
  const next = { ...orgSelected.value }
  for (const id of orgGroupIds(path)) next[id] = !!val
  orgSelected.value = next
}
const orgParsedLabel = (it) => {
  const parts = [it.parsed_title || it.orig_name]
  if (it.year) parts.push(`(${it.year})`)
  if (it.media_type === 'tv' && (it.season != null || it.episode != null)) {
    parts.push(` S${String(it.season ?? 1).padStart(2, '0')}E${String(it.episode ?? 0).padStart(2, '0')}`)
  }
  return parts.join('')
}

const confirmRunOrganize = async () => {
  if (!orgSelectedIds.value.length) {
    toast.warning('请至少勾选一个需要执行目录整理的文件')
    return
  }
  orgConfirming.value = true
  try {
    const { data } = await api.post('/api/media/organize/run', {
      include_ids: orgSelectedIds.value,
    })
    orgPreview.value.visible = false
    orgPreviewCache.value = null  // 执行会改变网盘状态，缓存预览已过时
    stopOrgCountdown()
    toast.success(data.msg || '目录整理已发起')
    await Promise.all([loadStatus(), loadRecords()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { orgConfirming.value = false }
}

// ---------- Dry-Run 预览 ----------
const previewDrawer = ref({ open: false, total: 0, pending_count: 0, items: [] })

async function openPreview() {
  if (previewing.value) return
  previewing.value = true
  try {
    const data = await runPreviewJob('/api/media/organize/preview', {},
      { text: '正在扫描待整理媒体，请勿进行其他操作……（按 Esc 可取消扫描）' })
    if (!data.ok) {
      toast.error(data.msg || '预览失败')
      return
    }
    if (data.truncated) toast.warning(data.msg || '本轮扫描达到预算上限，已返回部分结果')
    const blockedRules = data.scan_stats?.blocked_rules || []
    if (blockedRules.length && !data.total) {
      toast.warning(blockedRules[0].msg || '源目录设置不合法，已跳过扫描')
      return
    }
    previewDrawer.value = { open: true, total: data.total, pending_count: data.pending_count,
                            items: data.items || [], scan_stats: data.scan_stats || null }
  } catch (e) {
    if (e?.silent) return  // 等待超时：已由预览任务给出提示，扫描仍在后台继续
    if (e?.cancelled) toast.info('已取消扫描')
    else toast.error(errMsg(e))
  }
  finally { previewing.value = false }
}

// ---------- 账本撤销 & 本地 NFO 查看 ----------
async function undoOne(recordId) {
  try {
    const { data } = await api.post('/api/media/records/undo', { record_id: recordId })
    toast.success(data.msg)
    await loadRecords()
  } catch (e) { toast.error(errMsg(e)) }
}

async function undoBatch(batchId) {
  try {
    const { data } = await api.post('/api/media/records/undo', { batch_id: batchId })
    toast.success(data.msg)
    await loadRecords()
  } catch (e) { toast.error(errMsg(e)) }
}

onUnmounted(stopOrgCountdown)

defineExpose({
  loadRecords,
  hasRecords: () => records.value.length > 0,
})
</script>
