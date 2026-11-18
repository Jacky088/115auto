<template>
  <div>
  <!-- 步骤 1：确认刮削目录与 TMDB 配置 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 1</span>
        <div>
          <div class="step-title">确认刮削媒体目录（已整理媒体库）</div>
          <div class="step-desc">直接针对已规范归档的影视库目录进行 TMDB 联网刮削，生成本地元数据（.nfo 与海报），不改变原存放层级。</div>
        </div>
      </div>
      <div class="step-ops">
        <el-tag v-if="status.tmdb_configured" size="small" type="success" effect="plain">TMDB 已就绪</el-tag>
        <el-button v-else link type="warning" size="small" @click="$router.push('/settings')">去设置 TMDB Key →</el-button>
        <el-button type="success" size="small" @click="openScrapeRuleDialog()">+ 添加刮削目录</el-button>
      </div>
    </div>

    <div v-if="scrapeRules.length" class="active-rules-table">
      <el-table :data="scrapeRules" size="small" stripe>
        <el-table-column prop="name" label="规则名称" min-width="120" />
        <el-table-column label="刮削媒体目录（已整理媒体库）" min-width="260">
          <template #default="{ row }">{{ row.target_dir || row.source_dir }}</template>
        </el-table-column>
        <el-table-column label="启用" width="75" align="center">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" size="small" @change="(v) => toggleRuleEnabled(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openScrapeRuleDialog(row)">编辑</el-button>
            <el-popconfirm
              title="删除这条刮削目录规则？删除后无法恢复。"
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
      <span>当前暂无刮削媒体目录。请点击右上角「+ 添加刮削目录」选择已整理好的影视库目录。</span>
    </div>
  </div>

  <!-- 步骤 2：运行媒体刮削 -->
  <div class="step-card mb16">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 2</span>
        <div>
          <div class="step-title">运行媒体刮削</div>
          <div class="step-desc">智能加权召回、TMDB 在线比对。高匹配项自动完成，低置信度或多候选条目即时挂起交由下方核对。</div>
        </div>
      </div>
      <div class="step-actions">
        <span class="step-meta">
          待确认 {{ status.counts.need_confirm || 0 }} · 已选待提交 {{ status.counts.staged || 0 }}
          <template v-if="status.last_run.scrape"> · 上次：{{ fmtTime(status.last_run.scrape) }}</template>
        </span>
        <el-tooltip :content="scrapeBtnTip" :disabled="!scrapeBtnDisabled" placement="top">
          <span>
            <el-button
              type="success"
              size="default"
              :disabled="scrapeBtnDisabled"
              :loading="startingScrape || status.gate.scrape_running"
              @click="runScrape"
            >
              🎬 运行媒体刮削
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <!-- 刮削运行中的进度展示与安全中断 -->
    <div v-if="status.gate.active_mode === 'scrape'" class="running-banner">
      <div class="rb-left">
        <el-tag type="success" effect="dark" size="small">媒体刮削执行中</el-tag>
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

  <!-- 步骤 3：刮削核对与结果管理（纯属刮削闭环） -->
  <div class="step-card">
    <div class="step-header">
      <div class="step-title-wrap">
        <span class="step-badge">步骤 3</span>
        <div>
          <div class="step-title">刮削核对与结果管理</div>
          <div class="step-desc">对低置信度条目进行人工选片核对；已选定条目批量提交生效；已完成条目可查阅本地生成的 NFO 与海报。</div>
        </div>
      </div>
    </div>

    <!-- 统一暂存 & 提交控制台 -->
    <div class="commit-bar">
      <div class="cb-stats">
        <el-radio-group v-model="taskFilter" size="small" @change="onTaskFilterChange">
          <el-radio-button value="pending_group">待处理与队列 ({{ (status.counts.need_confirm||0) + (status.counts.staged||0) + (status.counts.queued||0) + (status.counts.running||0) }})</el-radio-button>
          <el-radio-button value="need_confirm">待确认 ({{ status.counts.need_confirm || 0 }})</el-radio-button>
          <el-radio-button value="staged">已选待提交 ({{ status.counts.staged || 0 }})</el-radio-button>
          <el-radio-button value="done">已完成 ({{ status.counts.done || 0 }})</el-radio-button>
          <el-radio-button value="all">全部记录</el-radio-button>
        </el-radio-group>
      </div>
      <div class="cb-actions">
        <el-button
          v-if="(status.counts.need_confirm || 0) > 0"
          size="small"
          plain
          @click="batchStageTop1"
        >
          ✨ 批量暂存首选项 (≥50分)
        </el-button>
        <el-popconfirm
          title="清理已完结和已忽略的刮削记录？未处理任务将保留。"
          confirm-button-text="清理"
          cancel-button-text="取消"
          confirm-button-type="danger"
          width="280"
          @confirm="clearFinishedTasks"
        >
          <template #reference>
            <el-button size="small">清理已完结</el-button>
          </template>
        </el-popconfirm>
        <el-tooltip
          :content="!status.running ? '顶栏系统开关已停止，暂不允许提交执行' : ((status.counts.staged||0)===0 ? '请先在下方至少点选一部影视（状态变为「已选待提交」），再点击此按钮统一入队执行' : '当前有互斥任务正在执行，请稍候')"
          :disabled="!commitDisabled"
          placement="top"
        >
          <span>
            <el-button
              type="primary"
              size="small"
              :disabled="commitDisabled"
              :loading="committing"
              @click="commitStaged"
            >
              🚀 提交队列（执行已选 {{ status.counts.staged || 0 }} 条）
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <div v-if="!tasks.length" class="empty-box">
      当前筛选条件下暂无刮削任务记录。确认上方刮削目录后，点击「运行媒体刮削」即可开始扫描匹配。
    </div>

    <div v-else class="task-list">
      <div
        v-for="t in pagedTasks"
        :key="t.id"
        class="task-card"
        :class="'is-' + t.status"
      >
        <div class="tc-head">
          <div class="tc-title-row">
            <span class="tc-id">#{{ t.id }}</span>
            <el-tag size="small" :type="taskStatusType(t.status)" effect="dark">
              {{ taskStatusText(t) }}
            </el-tag>
            <el-tag v-if="groupedItems(t).length > 1" size="small" type="warning" effect="plain">
              📦 合并 {{ groupedItems(t).length }} 集
            </el-tag>
            <el-tag v-if="t.select_source" size="small" type="info" effect="plain">🌐 已点选候选</el-tag>
            <span class="tc-orig" :title="t.orig_path">
              {{ t.orig_name }}<template v-if="groupedItems(t).length > 1"> 等共 {{ groupedItems(t).length }} 集</template>
            </span>
          </div>
          <div class="tc-ops">
            <el-button
              v-if="t.status === 'need_confirm' || t.status === 'staged'"
              size="small"
              plain
              @click="openManualSearch(t)"
            >
              🔎 手动搜 TMDB
            </el-button>
            <el-button
              v-if="t.status === 'staged'"
              size="small"
              type="warning"
              plain
              @click="unstageTask(t)"
            >
              ↩ 撤销暂存
            </el-button>
            <el-popconfirm
              v-if="t.status === 'need_confirm' || t.status === 'staged'"
              title="忽略这条视频？本轮及后续刮削将不再处理。"
              confirm-button-text="忽略"
              cancel-button-text="取消"
              confirm-button-type="info"
              width="240"
              @confirm="ignoreTask(t)"
            >
              <template #reference>
                <el-button size="small" plain>忽略</el-button>
              </template>
            </el-popconfirm>
            <el-button
              v-if="t.local_meta_dir"
              size="small"
              type="primary"
              plain
              @click="openMetaModal(t)"
            >
              📄 本地 NFO/海报
            </el-button>
          </div>
        </div>

        <div class="tc-meta">
          <span>原路径：<code>{{ t.orig_path }}</code></span>
          <span v-if="t.parsed && t.parsed.title">
            · 解析词：<b>{{ t.parsed.title }}</b>
            <template v-if="t.parsed.year">({{ t.parsed.year }})</template>
            <template v-if="t.parsed.episode"> · S{{ String(t.parsed.season ?? 1).padStart(2,'0') }}E{{ String(t.parsed.episode).padStart(2,'0') }}</template>
            <template v-if="t.parsed.resolution"> · {{ t.parsed.resolution }}</template>
          </span>
          <span v-if="t.target_name && t.status === 'done'">
            → 落盘：<code>{{ t.target_path }}/{{ t.target_name }}</code>
          </span>
          <span v-if="t.error && t.status === 'failed'" class="tc-err"> · 错误：{{ t.error }}</span>
          <el-button
            v-if="groupedItems(t).length > 1"
            link
            type="primary"
            size="small"
            class="group-toggle-btn"
            @click="toggleGroupExpand(t.id)"
          >
            {{ expandedGroups[t.id] ? '收起剧集清单 ▴' : `展开本组 ${groupedItems(t).length} 集清单 ▾` }}
          </el-button>
        </div>

        <!-- 同剧合并剧集清单（按需展开核对全组集数） -->
        <div v-if="groupedItems(t).length > 1 && expandedGroups[t.id]" class="group-files-box">
          <div v-for="(gi, gidx) in groupedItems(t)" :key="gi.file_id || gidx" class="gf-row">
            <span class="gf-idx">{{ gidx + 1 }}.</span>
            <span class="gf-name" :title="gi.orig_path">{{ gi.orig_name }}</span>
            <el-tag v-if="gi.parsed && gi.parsed.episode != null" size="small" effect="plain">
              S{{ String(gi.parsed.season ?? 1).padStart(2,'0') }}E{{ String(gi.parsed.episode).padStart(2,'0') }}
            </el-tag>
            <span v-if="fmtSizeMb(gi.size)" class="gf-size">{{ fmtSizeMb(gi.size) }}</span>
          </div>
        </div>

        <!-- 已暂存提示横幅 -->
        <div v-if="t.status === 'staged'" class="staged-banner">
          🟡 已选定：<b>{{ t.selected_title }}</b>
          <template v-if="t.selected_year"> ({{ t.selected_year }})</template>
          <span>
            [{{ t.selected_kind === 'tv' ? '剧集' : '电影' }} ·
            <a
              v-if="t.selected_tmdb_id"
              :href="tmdbUrl(t.selected_kind, t.selected_tmdb_id)"
              target="_blank"
              rel="noopener noreferrer"
              class="tmdb-link"
            >tmdb={{ t.selected_tmdb_id }} ↗</a>]
          </span>
          — 等待点击右上角「🚀 提交队列」生效（点击其他候选可直接改选）
        </div>

        <!-- 候选卡片点选区（need_confirm 或 staged 时展示；其余状态只读折叠） -->
        <div v-if="(t.status === 'need_confirm' || t.status === 'staged') && t.candidates && t.candidates.length" class="cand-grid">
          <div
            v-for="(c, idx) in t.candidates"
            :key="c.tmdb_id + '-' + idx"
            class="cand-item"
            :class="{ selected: t.selected_tmdb_id === c.tmdb_id && t.status === 'staged' }"
            @click="selectCandidate(t, idx)"
          >
            <img
              v-if="c.poster_path"
              :src="`/api/tmdb/poster?path=${encodeURIComponent(c.poster_path)}`"
              class="cand-poster"
              loading="lazy"
            />
            <div v-else class="cand-poster cand-no-poster">无海报</div>
            <div class="cand-info">
              <div>
                <div class="cand-top">
                  <el-tag size="small" :type="c.kind === 'tv' ? 'success' : 'primary'" effect="plain">
                    {{ c.kind === 'tv' ? '剧' : '影' }}
                  </el-tag>
                  <span class="cand-name" :title="c.title">{{ c.title }}</span>
                  <span v-if="c.year" class="cand-yr">({{ c.year }})</span>
                  <span v-if="c.rating > 0" class="cand-rating">★{{ Number(c.rating).toFixed(1) }}</span>
                </div>
                <div v-if="c.original_title && c.original_title !== c.title" class="cand-orig-title" :title="c.original_title">
                  原名：{{ c.original_title }}
                </div>
              </div>
              <div class="cand-sub">
                <a
                  :href="tmdbUrl(c.kind, c.tmdb_id)"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="tmdb-link"
                  title="在新标签打开 TMDB 详情页核对"
                  @click.stop
                >
                  TMDB: {{ c.tmdb_id }} ↗
                </a>
                <span class="cand-score" :class="{ high: c.score >= 85 }">{{ Number(c.score || 0).toFixed(0) }} 分</span>
              </div>
              <div v-if="c.overview" class="cand-over" :title="c.overview">{{ c.overview }}</div>
              <div v-if="c.reasons && c.reasons.length" class="cand-reasons" :title="c.reasons.join('；')">
                {{ c.reasons.slice(0, 2).join(' · ') }}
              </div>
            </div>
          </div>
        </div>
        <div v-else-if="t.status === 'need_confirm' && (!t.candidates || !t.candidates.length)" class="no-cand-tip">
          未从 TMDB 召回高相似候选，请点击右侧「🔎 手动搜 TMDB」指定正确条目。
        </div>
      </div>
    </div>

    <!-- 刮削结果分页栏（与整理结果完全对齐） -->
    <div v-if="tasks.length" class="pagination-bar">
      <el-pagination
        v-model:current-page="taskPage"
        v-model:page-size="taskPageSize"
        :page-sizes="[5, 10, 20, 50]"
        :total="tasks.length"
        :layout="paginationLayout"
        :pager-count="isMobile ? 5 : 7"
        background
        size="small"
      />
    </div>
  </div>

  <!-- 手动搜索 TMDB 弹窗 -->
  <el-dialog v-model="manualDialog.open" title="手动搜索 TMDB 并暂存绑定" width="min(640px, 94vw)">
    <div class="mb12 meta-hint">
      待绑定文件：<b>{{ manualDialog.task?.orig_name }}</b>（选定后置为「已选待提交」，需点「提交队列」才执行）
    </div>
    <div class="manual-search-bar mb12">
      <el-input v-model="manualDialog.q" placeholder="输入片名或剧名关键字，按回车搜索..." @keyup.enter="searchManualTmdb" />
      <el-select v-model="manualDialog.kind" style="width:105px" fit-input-width>
        <el-option value="multi" label="全部" />
        <el-option value="tv" label="剧集" />
        <el-option value="movie" label="电影" />
      </el-select>
      <el-input v-model="manualDialog.year" placeholder="年份" style="width:90px" />
      <el-button type="primary" :loading="manualDialog.loading" @click="searchManualTmdb">搜索</el-button>
    </div>
    <div v-if="!manualDialog.results.length && !manualDialog.loading" class="empty-box">
      请输入关键片名搜索 TMDB，或在上方输入框输入结果后点击右侧「设为候选」。
    </div>
    <div class="manual-results">
      <div v-for="r in manualDialog.results" :key="r.kind + '-' + r.tmdb_id" class="mr-row">
        <img v-if="r.poster_path" :src="`/api/tmdb/poster?path=${encodeURIComponent(r.poster_path)}`" class="cand-poster" />
        <div v-else class="cand-poster cand-no-poster">无海报</div>
        <div class="mr-info">
          <div>
            <el-tag size="small" :type="r.kind === 'tv' ? 'success' : 'primary'">{{ r.kind === 'tv' ? '剧' : '影' }}</el-tag>
            <b class="mx8">{{ r.title }}</b>
            <span v-if="r.year" class="cand-yr mx8">({{ r.year }})</span>
            <span v-if="r.rating > 0" class="cand-rating mx8">★{{ Number(r.rating).toFixed(1) }}</span>
            <a
              :href="tmdbUrl(r.kind, r.tmdb_id)"
              target="_blank"
              rel="noopener noreferrer"
              class="tmdb-link"
              style="font-size:12px"
            >
              TMDB:{{ r.tmdb_id }} ↗
            </a>
          </div>
          <div v-if="r.original_title && r.original_title !== r.title" class="cand-orig-title">
            原名：{{ r.original_title }}
          </div>
          <div class="mr-over">{{ r.overview || '暂无简介' }}</div>
        </div>
        <el-button type="primary" size="small" @click="pickManualResult(r)">选定暂存</el-button>
      </div>
    </div>
  </el-dialog>

  <!-- 本地元数据 NFO / 海报预览弹窗 -->
  <el-dialog v-model="metaDialog.open" title="本地持久化 NFO 与海报详情（零 115 上传）" width="min(760px, 94vw)">
    <div v-if="metaDialog.data" class="meta-view">
      <div class="mb12" style="font-size:12px;color:var(--text-muted)">
        存储位置：<code>{{ metaDialog.data.meta_dir }}</code>
      </div>
      <div class="meta-imgs mb12" v-if="metaDialog.data.has_poster || metaDialog.data.has_fanart">
        <img v-if="metaDialog.data.has_poster" :src="`/api/media/poster/local?task_id=${metaDialog.taskId}&file=poster.jpg`" class="meta-poster" />
        <img v-if="metaDialog.data.has_fanart" :src="`/api/media/poster/local?task_id=${metaDialog.taskId}&file=fanart.jpg`" class="meta-fanart" />
      </div>
      <el-collapse v-if="metaDialog.data.nfos && metaDialog.data.nfos.length">
        <el-collapse-item v-for="n in metaDialog.data.nfos" :key="n.path" :title="n.path">
          <pre class="nfo-code">{{ n.content }}</pre>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-dialog>
  </div>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { api, errMsg, fmtTime, toast } from '../../api'

const {
  status,
  scrapeRules,
  activeScrapeRules,
  isBusy,
  isMobile,
  paginationLayout,
  phaseLabel,
  activeProgressLabel,
  progressPercent,
  stopping,
  stopMedia,
  loadStatus,
  openScrapeRuleDialog,
  toggleRuleEnabled,
  delRule,
  fmtSizeMb,
  tmdbUrl,
} = inject('mediaCtx')

const taskFilter = ref('pending_group')
const taskPage = ref(1)
const taskPageSize = ref(10)
const tasks = ref([])
const startingScrape = ref(false)
const committing = ref(false)

const pagedTasks = computed(() => {
  const start = (taskPage.value - 1) * taskPageSize.value
  return tasks.value.slice(start, start + taskPageSize.value)
})

const scrapeBtnDisabled = computed(() => !status.value.running || isBusy.value || !status.value.tmdb_configured || activeScrapeRules.value.length === 0)
const commitDisabled = computed(() =>
  !status.value.running
  || (status.value.counts.staged || 0) === 0
  || ['organize', 'auto_move', 'dedupe'].includes(status.value.gate.active_mode)
)
const scrapeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (!status.value.tmdb_configured) return '请先在「系统设置」配置 TMDB API Key'
  if (activeScrapeRules.value.length === 0) return '暂无启用的刮削目录规则，请先点击上方添加'
  if (isBusy.value) return '已有独占整理或刮削任务运行中（严格互斥），请等待结束'
  return ''
})

function taskStatusText(t) {
  return {
    need_confirm: '待确认',
    staged: '已选待提交',
    queued: '排队执行中',
    running: '正在落盘',
    done: '已完成',
    skipped: '已跳过',
    failed: '失败',
    ignored: '已忽略',
  }[t.status] || t.status
}

function taskStatusType(s) {
  return {
    need_confirm: 'info',
    staged: 'warning',
    queued: 'primary',
    running: 'primary',
    done: 'success',
    skipped: 'info',
    failed: 'danger',
    ignored: 'info',
  }[s] || 'info'
}

const expandedGroups = ref({})

function groupedItems(t) {
  return Array.isArray(t?.parsed?._grouped_items) ? t.parsed._grouped_items : []
}

function toggleGroupExpand(taskId) {
  expandedGroups.value[taskId] = !expandedGroups.value[taskId]
}

async function loadTasks() {
  try {
    const { data } = await api.get('/api/media/tasks', { params: { status: taskFilter.value, limit: 150 } })
    tasks.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function onTaskFilterChange() {
  taskPage.value = 1
  await loadTasks()
}

async function runScrape() {
  startingScrape.value = true
  try {
    const { data } = await api.post('/api/media/scrape/run', {})
    toast.success(data.msg || '媒体刮削已发起')
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { startingScrape.value = false }
}

async function selectCandidate(task, idx) {
  try {
    const { data } = await api.post(`/api/media/tasks/${task.id}/select`, { cand_idx: idx })
    toast.success(data.msg)
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) {
    toast.error(errMsg(e))
    await loadTasks()
  }
}

async function unstageTask(task) {
  try {
    const { data } = await api.post(`/api/media/tasks/${task.id}/unstage`)
    toast.info(data.msg)
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function ignoreTask(task) {
  try {
    const { data } = await api.post(`/api/media/tasks/${task.id}/ignore`)
    toast.info(data.msg)
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function batchStageTop1() {
  try {
    const { data } = await api.post('/api/media/tasks/batch-stage', { mode: 'auto_top1', min_score: 50 })
    toast.success(data.msg)
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function commitStaged() {
  committing.value = true
  try {
    const { data } = await api.post('/api/media/tasks/commit', {})
    toast.success(data.msg)
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { committing.value = false }
}

async function clearFinishedTasks() {
  try {
    await api.delete('/api/media/tasks/clear')
    toast.success('已清理历史完结记录')
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
}

// ---------- 手动搜索 TMDB ----------
const manualDialog = ref({ open: false, task: null, q: '', kind: 'multi', year: '', loading: false, results: [] })

function openManualSearch(task) {
  manualDialog.value = {
    open: true,
    task,
    q: task.parsed?.title || task.orig_name.replace(/\.[^.]+$/, ''),
    kind: task.media_type === 'tv' ? 'tv' : 'multi',
    year: task.parsed?.year || '',
    loading: false,
    results: [],
  }
  searchManualTmdb()
}

async function searchManualTmdb() {
  if (!manualDialog.value.q.trim()) return
  manualDialog.value.loading = true
  try {
    const { data } = await api.get('/api/media/tmdb/search', {
      params: { q: manualDialog.value.q, kind: manualDialog.value.kind, year: manualDialog.value.year },
    })
    manualDialog.value.results = data
  } catch (e) { toast.error(errMsg(e)) }
  finally { manualDialog.value.loading = false }
}

async function pickManualResult(r) {
  if (!manualDialog.value.task) return
  try {
    const { data } = await api.post(`/api/media/tasks/${manualDialog.value.task.id}/select`, {
      tmdb_id: r.tmdb_id,
      kind: r.kind,
    })
    toast.success(data.msg)
    manualDialog.value.open = false
    await Promise.all([loadStatus(), loadTasks()])
  } catch (e) { toast.error(errMsg(e)) }
}

const metaDialog = ref({ open: false, taskId: 0, data: null })

async function openMetaModal(task) {
  try {
    const { data } = await api.get('/api/media/meta/inspect', { params: { task_id: task.id } })
    metaDialog.value = { open: true, taskId: task.id, data }
  } catch (e) { toast.error(errMsg(e)) }
}

defineExpose({
  loadTasks,
})
</script>
