<template>
  <div>
  <!-- 定时文件整理（按目录映射移动中文文件） -->
  <el-card shadow="never" class="page-card" style="margin-top: 16px">
    <template #header>
      <div class="organize-header-toolbar">
        <div class="header-left">
          <span class="organize-main-title">定时文件整理（自动归档与分流）</span>
          <el-tooltip
            :disabled="systemRunning"
            content="系统未运行，请先点击顶栏「开始运行」"
            placement="top"
          >
            <span>
              <el-switch
                v-model="computedAutoMoveEnabled"
                :disabled="!systemRunning"
                class="organize-master-switch"
                active-text="功能已开启"
                inactive-text="功能已关闭"
                inline-prompt
                @change="onToggleAutoMove"
              />
            </span>
          </el-tooltip>
        </div>
        <div class="header-actions">
          <el-tooltip
            :disabled="systemRunning && computedAutoMoveEnabled && !isMediaMutexActive"
            :content="!systemRunning ? '系统处于停止状态，请先在顶栏点击「开始运行」启动系统' : (isMediaMutexActive ? `当前正在执行「${activeMode === 'organize' ? '目录整理' : '媒体刮削'}」，请稍后再试` : '定时文件整理功能当前为关闭状态，请先开启左侧总开关')"
            placement="top"
          >
            <span>
              <el-button
                type="primary"
                :disabled="!systemRunning || !computedAutoMoveEnabled || isMediaMutexActive"
                :loading="runningMove"
                @click="runAutoMoveNow"
              >
                <el-icon><CaretRight /></el-icon>
                <span>立即排队执行</span>
              </el-button>
            </span>
          </el-tooltip>
        </div>
      </div>
    </template>

    <el-alert
      v-if="isMediaMutexActive"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 14px"
      :title="`互斥锁定中：当前正在执行「${activeMode === 'organize' ? '目录整理' : '媒体刮削'}」`"
      description="整理或刮削执行中，结束后自动解除"
    />

    <!-- 规则说明与风控隔离保护 -->
    <div class="organize-notice-banner">
      <div class="notice-icon"><el-icon><InfoFilled /></el-icon></div>
      <div class="notice-body">
        <div class="notice-title">自动整理规则说明与风控隔离保护</div>
        <div class="notice-desc">
          系统按规则排队执行，<strong>只移动正片文件，文件夹本身绝不移动</strong>。仅当文件名满足「<strong>包含汉字</strong>」且「<strong>匹配剧集名称</strong>」时才会移动（如：<code>折月亮.S01E12.mkv</code> 移动，<code>1.mp4</code> 绝不移动）。执行期间系统将<strong>自动暂停转存工作器</strong>，完成后自动恢复，彻底杜绝 115 并发风控与目录并发写入冲突。
        </div>
      </div>
    </div>

    <!-- 整理参数控制面板：2栏响应式卡片布局 -->
    <div class="organize-config-grid">
      <!-- 卡片 A：分流模式与匹配规则 -->
      <div class="config-card">
        <div class="config-card-header">
          <div class="config-card-title">
            <el-icon><Operation /></el-icon>
            <span>整理分流策略</span>
          </div>
          <span v-if="moveSettings.auto_move_last_run" class="last-run-chip">
            上次执行：{{ formatTime(moveSettings.auto_move_last_run) }}
          </span>
        </div>

        <div class="config-form-item">
          <span class="config-label">默认模式：</span>
          <div class="config-control">
            <el-tooltip
              :content="moveSettings.auto_move_mode === 'library'
                ? '总库智能分流（推荐）：自动分发到库内同名剧目文件夹'
                : '精确匹配：仅移动匹配此剧名的文件'"
              placement="top"
            >
              <div class="capsule-toggle">
                <div
                  class="capsule-seg"
                  :class="{ 'is-active': moveSettings.auto_move_mode === 'library' }"
                  @click="changeMoveMode('library')"
                >
                  总库智能分流
                </div>
                <div class="capsule-divider"></div>
                <div
                  class="capsule-seg"
                  :class="{ 'is-active': moveSettings.auto_move_mode === 'exact' }"
                  @click="changeMoveMode('exact')"
                >
                  一对一精确匹配
                </div>
              </div>
            </el-tooltip>
          </div>
        </div>

        <!-- 功能特性开关网格 -->
        <div class="feature-switches-grid">
          <div class="feature-switch-pill" :class="{ 'is-active': moveSettings.auto_move_season_folder }">
            <div class="feature-meta">
              <span class="feature-name">按季入库</span>
              <span class="feature-hint">归入对应 Season/第一季 文件夹</span>
            </div>
            <el-switch v-model="moveSettings.auto_move_season_folder" @change="saveMoveSettings" />
          </div>

          <div class="feature-switch-pill" :class="{ 'is-active': moveSettings.auto_move_fuzzy_t2s }">
            <div class="feature-meta">
              <span class="feature-name">简繁互通</span>
              <span class="feature-hint">繁体与简体剧名智能互通命中</span>
            </div>
            <el-switch v-model="moveSettings.auto_move_fuzzy_t2s" @change="saveMoveSettings" />
          </div>

          <div class="feature-switch-pill" :class="{ 'is-active': moveSettings.auto_move_scan_subdirs }">
            <div class="feature-meta">
              <span class="feature-name">穿透子文件夹</span>
              <span class="feature-hint">检索源目录下最多 2 层子文件夹（兼容 剧名/Season 目录）</span>
            </div>
            <el-switch v-model="moveSettings.auto_move_scan_subdirs" @change="saveMoveSettings" />
          </div>

          <div class="feature-switch-pill" :class="{ 'is-active': moveSettings.auto_move_dedup_by_episode }">
            <div class="feature-meta">
              <span class="feature-name">按季集查重</span>
              <span class="feature-hint">同剧目标已有同季同集视频时跳过，防多版本重复</span>
            </div>
            <el-switch v-model="moveSettings.auto_move_dedup_by_episode" @change="saveMoveSettings" />
          </div>

          <div class="feature-switch-pill" :class="{ 'is-active': moveSettings.auto_move_notify }">
            <div class="feature-meta">
              <span class="feature-name">推送通知</span>
              <span class="feature-hint">整理完成后推送汇总明细</span>
            </div>
            <el-switch v-model="moveSettings.auto_move_notify" @change="saveMoveSettings" />
          </div>
        </div>
      </div>

      <!-- 卡片 B：调度计划与时间预测 -->
      <div class="config-card">
        <div class="config-card-header">
          <div class="config-card-title">
            <el-icon><Timer /></el-icon>
            <span>定时计划调度</span>
          </div>
          <span class="schedule-risk-badge" title="受系统设置防风控定时计划保护">
            <el-icon><Lock /></el-icon> 受防风控时段保护
          </span>
        </div>

        <div class="schedule-risk-note">
          执行周期同时受防风控允许时段管辖，时段外触发将自动顺延，避免深夜高频写操作。
        </div>

        <div class="config-form-item">
          <span class="config-label">调度方式：</span>
          <div class="config-control">
            <el-tooltip
              :content="moveSettings.auto_move_schedule_type === 'interval'
                ? '固定间隔：按设定间隔周期性整理'
                : '当前为【每天固定时间点】：在每天设定的指定整点准时排队扫描并整理'"
              placement="top"
            >
              <div class="capsule-toggle">
                <div
                  class="capsule-seg"
                  :class="{ 'is-active': moveSettings.auto_move_schedule_type === 'interval' }"
                  @click="changeScheduleType('interval')"
                >
                  固定间隔轮询
                </div>
                <div class="capsule-divider"></div>
                <div
                  class="capsule-seg"
                  :class="{ 'is-active': moveSettings.auto_move_schedule_type === 'fixed_times' }"
                  @click="changeScheduleType('fixed_times')"
                >
                  每天固定时间点
                </div>
              </div>
            </el-tooltip>
          </div>
        </div>

        <!-- 间隔模式配置 -->
        <div v-if="moveSettings.auto_move_schedule_type === 'interval'" class="config-form-item">
          <span class="config-label">间隔时长：</span>
          <div class="config-control">
            <el-select v-model="moveSettings.auto_move_interval_hours" style="width: 170px" fit-input-width @change="saveMoveSettings">
              <el-option label="每 2 小时" :value="2" />
              <el-option label="每 4 小时" :value="4" />
              <el-option label="每 6 小时" :value="6" />
              <el-option label="每 8 小时" :value="8" />
              <el-option label="每 12 小时" :value="12" />
              <el-option label="每 24 小时" :value="24" />
            </el-select>
          </div>
        </div>

        <!-- 固定时间点配置 -->
        <div v-else class="fixed-times-layout">
          <div class="config-form-item">
            <span class="config-label">已设时段：</span>
            <div class="fixed-tags-scroll">
              <el-tag
                v-for="t in (moveSettings.auto_move_fixed_times || [])"
                :key="t"
                closable
                type="primary"
                effect="light"
                class="fixed-time-tag"
                @close="removeFixedTime(t)"
              >
                {{ t }}
              </el-tag>
              <span v-if="!(moveSettings.auto_move_fixed_times || []).length" class="empty-tag-tip">
                暂未配置时间点，请在下方选择并添加
              </span>
            </div>
          </div>

          <div class="config-form-item">
            <span class="config-label">添加时段：</span>
            <div class="time-select-row">
              <el-time-select
                v-model="newFixedTime"
                start="00:00"
                step="01:00"
                end="23:00"
                placeholder="选择时间点"
                style="width: 140px"
              />
              <el-button type="primary" plain :disabled="!newFixedTime" @click="addFixedTime">
                <el-icon><Plus /></el-icon>
                <span>添加时段</span>
              </el-button>
            </div>
          </div>
        </div>

        <!-- 未来 48 小时执行时间预测嵌入 -->
        <div v-if="nextRuns && nextRuns.length" class="prediction-box">
          <div class="prediction-header">
            <el-icon style="color: var(--el-color-primary)"><Calendar /></el-icon>
            <span>{{ moveSettings.auto_move_enabled ? '接下来 2 天（48 小时）预计开启时间点：' : '按当前设定（总开关开启后生效）预计时间点：' }}</span>
          </div>
          <div class="prediction-tags">
            <el-tag
              v-for="(timeStr, idx) in nextRuns"
              :key="idx"
              size="small"
              :type="idx === 0 ? 'success' : 'info'"
              :effect="idx === 0 ? 'light' : 'plain'"
              class="prediction-chip"
            >
              {{ timeStr }}
            </el-tag>
          </div>
        </div>
      </div>
    </div>

    <!-- 动态排空/整理状态条（强点或定时触发时实时跟踪队列终点） -->
    <div v-if="gateState === 'draining'" class="drain-banner">
      <el-icon class="is-loading" style="color: #e6a23c; font-size: var(--fs-title); flex-shrink: 0"><Loading /></el-icon>
      <span>
        <strong>转存与云下载队列排空中（正在等待所有队列任务到达终点）：</strong>
        当前剩余 <strong>{{ drainStatus.unfinished }}</strong> 个任务处理中
        <template v-if="drainStatus.running">（{{ drainStatus.running }} 个转存中）</template>
        <template v-if="drainStatus.submitted">（{{ drainStatus.submitted }} 个云下载中）</template>
        <template v-if="drainStatus.retrying">（{{ drainStatus.retrying }} 个等待重试）</template>
        <template v-if="drainStatus.queued">（{{ drainStatus.queued }} 个排队中）</template>
        ，系统需等待队列全部到达终态（成功、云下载落盘、跳过或重试结束）后，自动独占开启移动归档！
      </span>
    </div>

    <div v-else-if="gateState === 'organizing'" class="organizing-banner">
      <el-icon class="is-loading" style="color: #409eff; font-size: var(--fs-title); flex-shrink: 0"><Loading /></el-icon>
      <span>
        <strong>队列已全部终态，正在独占执行文件整理移动中：</strong>转存工作器已挂起（零并发保护 115 账号安全），正在扫描各源目录并智能归类...
      </span>
    </div>

    <!-- 规则表格子组件 -->
    <AutoMoveRulesTable
      :rules="moveRules"
      :move-settings="moveSettings"
      :cloud-dirs="cloudDirs"
      @add="openAddMoveRule"
      @pick-dir="onTablePickDir"
      @save-rule="saveMoveRule"
      @delete-rule="deleteMoveRule"
      @toggle-dir-match="toggleDirMatch"
    />
  </el-card>

  <!-- 目录选择器（供表格行路径修改使用） -->
  <DirPicker v-model="pickerVisible" :title="pickerTitle" @pick="onPickDir" />

  <!-- 添加整理规则弹窗子组件 -->
  <AutoMoveRuleDialog
    v-model="dialogVisible"
    :move-settings="moveSettings"
    :cloud-dirs="cloudDirs"
    @created="loadAutoMove"
  />

  <AutoMovePreviewDialog ref="previewDlgRef" @started="onPreviewStarted" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'
import { usePolling } from '../../composables/usePolling'
import { confirmAction } from '../../composables/confirmDialog'
import DirPicker from '../DirPicker.vue'
import AutoMoveRulesTable from './AutoMoveRulesTable.vue'
import AutoMoveRuleDialog from './AutoMoveRuleDialog.vue'
import AutoMovePreviewDialog from './AutoMovePreviewDialog.vue'
import { extractKeyword, formatTime, normPath } from './autoMoveUtils'

const previewDlgRef = ref(null)
const pickerVisible = ref(false)
const pickerKind = ref('move_source')
const pickerTitle = ref('选择 115 目录')
let pickerRow = null

// ---------- 定时文件整理（按目录映射移动中文文件） ----------
const moveRules = ref([])
const moveSettings = ref({
  auto_move_enabled: false,
  auto_move_schedule_type: 'interval',
  auto_move_interval_hours: 6,
  auto_move_fixed_times: ['06:00', '12:00', '18:00'],
  auto_move_scan_subdirs: true,
  auto_move_dedup_by_episode: true,
  auto_move_notify: true,
  auto_move_last_run: 0,
  auto_move_mode: 'library',
  auto_move_season_folder: true,
  auto_move_fuzzy_t2s: true,
})
const gateState = ref('idle')
const activeMode = ref('none')
const exclusiveBusy = ref(false)
const isMediaMutexActive = computed(() => exclusiveBusy.value && ['organize', 'scrape'].includes(activeMode.value))
const drainStatus = ref({ unfinished: 0, queued: 0, retrying: 0, running: 0, submitted: 0 })
const nextRuns = ref([])
const newFixedTime = ref('')
const systemRunning = ref(false)
const runningMove = ref(false)
const dialogVisible = ref(false)
const cloudDirs = ref([])

const computedAutoMoveEnabled = computed({
  get() {
    return systemRunning.value ? !!moveSettings.value.auto_move_enabled : false
  },
  set(val) {
    if (!systemRunning.value) return
    moveSettings.value.auto_move_enabled = val
  }
})

const onToggleAutoMove = () => {
  if (!systemRunning.value) {
    ElMessage.warning('系统当前处于停止状态，请先在顶栏点击「开始运行」开启系统！')
    moveSettings.value.auto_move_enabled = false
    return
  }
  if (moveSettings.value.auto_move_enabled) {
    if (moveSettings.value.auto_move_schedule_type === 'fixed_times') {
      const ft = moveSettings.value.auto_move_fixed_times || []
      if (!ft.length) {
        moveSettings.value.auto_move_enabled = false
        ElMessage.warning('调度方式已设为每天固定时间点，但尚未配置执行时段，请先在下方添加时段后再开启！')
        return
      }
    }
    const enabledRules = (moveRules.value || []).filter(r => r.enabled !== false && r.enabled !== 0)
    if (!enabledRules.length) {
      moveSettings.value.auto_move_enabled = false
      ElMessage.warning('当前暂无启用的整理规则，请先在下方添加并启用至少一条整理规则后再开启！')
      return
    }
  }
  saveMoveSettings()
}

const checkSysState = async () => {
  try {
    const { data } = await api.get('/api/system/state')
    const running = !!data.running
    systemRunning.value = running
    if (!running) {
      moveSettings.value.auto_move_enabled = false
    }
  } catch {}
}

// 系统运行状态的轻量轮询（3 秒），页面不可见时自动暂停
const sysPoll = usePolling(checkSysState, 3000)

watch(systemRunning, (isRunning) => {
  if (!isRunning) {
    moveSettings.value.auto_move_enabled = false
  }
})

const loadAutoMove = async () => {
  try {
    const { data } = await api.get('/api/auto-move/rules')
    if (data.system_running !== undefined) {
      systemRunning.value = !!data.system_running
      if (!systemRunning.value) {
        moveSettings.value.auto_move_enabled = false
      }
    }
    moveRules.value = data.rules || []
    cloudDirs.value = data.cloud_dirs || []
    if (data.settings) {
      moveSettings.value = { ...moveSettings.value, ...data.settings }
    }
    gateState.value = data.gate_state || 'idle'
    activeMode.value = data.active_mode || 'none'
    exclusiveBusy.value = !!data.exclusive_busy
    drainStatus.value = data.drain_status || { unfinished: 0, queued: 0, retrying: 0, running: 0, submitted: 0 }
    nextRuns.value = data.next_runs || []

    if (gateState.value !== 'idle') {
      runningMove.value = !['organize', 'scrape'].includes(activeMode.value)
      startAutoMovePolling()
    } else if (!runningMove.value) {
      stopAutoMovePolling()
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

// 排队整理进度轮询（2.5 秒），页面不可见时自动暂停；gate 回到 idle 后自动停止
const movePoll = usePolling(async () => {
  try {
    const wasRunningMove = runningMove.value
    const { data } = await api.get('/api/auto-move/rules')
    gateState.value = data.gate_state || 'idle'
    activeMode.value = data.active_mode || 'none'
    exclusiveBusy.value = !!data.exclusive_busy
    drainStatus.value = data.drain_status || { unfinished: 0, queued: 0, retrying: 0, running: 0, submitted: 0 }
    nextRuns.value = data.next_runs || []
    if (gateState.value === 'idle') {
      stopAutoMovePolling()
      runningMove.value = false
      moveRules.value = data.rules || []
      if (data.settings) moveSettings.value = { ...moveSettings.value, ...data.settings }
      if (wasRunningMove) {
        if (data.last_result && data.last_result.total_moved !== undefined) {
          ElMessage.success(`排队整理完成：共处理 ${data.last_result.total_rules} 条规则，移动 ${data.last_result.total_moved} 个文件（转存已恢复）`)
        } else {
          ElMessage.success('排队整理流程已全部完成（转存已恢复）')
        }
      }
    } else {
      runningMove.value = !['organize', 'scrape'].includes(activeMode.value)
    }
  } catch (e) {
    console.debug('[auto-move]', e)
  }
}, 2500)

const startAutoMovePolling = () => {
  movePoll.start()
}

const stopAutoMovePolling = () => {
  movePoll.stop()
}

const onTablePickDir = ({ row, kind }) => {
  pickerRow = row
  pickerKind.value = kind
  pickerTitle.value = kind === 'move_source'
    ? '选择整理监控的源目录'
    : (moveSettings.value.auto_move_mode === 'library' ? '选择媒体总库目标目录' : '选择剧集目标目录')
  pickerVisible.value = true
}

const onPickDir = (val) => {
  if (pickerKind.value === 'move_source') {
    if (pickerRow) {
      const oldSource = pickerRow.source_dir
      pickerRow.source_dir = val
      if (pickerRow.dir_match && !(cloudDirs.value || []).includes(normPath(val))) {
        ElMessageBox.confirm(
          '新源目录不是云下载目录，云下载模式将自动关闭，' +
          '回落为按文件名匹配的基本规则并保持启用。',
          '修改源目录？',
          { type: 'warning', confirmButtonText: '修改并关闭', cancelButtonText: '取消' }
        ).then(() => {
          pickerRow.dir_match = 0
          saveMoveRule(pickerRow)
        }).catch(() => {
          pickerRow.source_dir = oldSource
        })
      } else {
        saveMoveRule(pickerRow)
      }
    }
  } else if (pickerKind.value === 'move_target') {
    if (pickerRow) {
      pickerRow.target_dir = val
      pickerRow.keyword = extractKeyword(val)
      saveMoveRule(pickerRow)
    }
  }
}

const changeMoveMode = (mode) => {
  if (moveSettings.value.auto_move_mode === mode) return
  moveSettings.value.auto_move_mode = mode
  saveMoveSettings()
}

const changeScheduleType = (type) => {
  if (moveSettings.value.auto_move_schedule_type === type) return
  moveSettings.value.auto_move_schedule_type = type
  if (type === 'fixed_times' && (!moveSettings.value.auto_move_fixed_times || !moveSettings.value.auto_move_fixed_times.length)) {
    moveSettings.value.auto_move_fixed_times = ['06:00', '12:00', '18:00']
    ElMessage.info('已为您预置常用时间点 [06:00, 12:00, 18:00]，可自由增删')
  }
  saveMoveSettings()
}

const saveMoveSettings = async () => {
  try {
    const payload = { ...moveSettings.value }
    // 系统停止时避免传入 auto_move_enabled: true 触发后端拒绝
    if (!systemRunning.value && payload.auto_move_enabled) {
      delete payload.auto_move_enabled
    }
    const { data } = await api.put('/api/auto-move/settings', payload)
    if (data && data.next_runs) {
      nextRuns.value = data.next_runs
    }
    saveOk('整理设置已更新')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const addFixedTime = () => {
  if (!newFixedTime.value) return
  const t = newFixedTime.value.trim()
  if (!/^([01]?\d|2[0-3]):([0-5]\d)$/.test(t)) {
    ElMessage.warning('时间格式须为 HH:MM（如 06:00）')
    return
  }
  const cur = Array.isArray(moveSettings.value.auto_move_fixed_times) ? [...moveSettings.value.auto_move_fixed_times] : []
  if (cur.includes(t)) {
    ElMessage.info('该时间点已存在')
    return
  }
  cur.push(t)
  cur.sort()
  moveSettings.value.auto_move_fixed_times = cur
  newFixedTime.value = ''
  saveMoveSettings()
}

const removeFixedTime = (t) => {
  const cur = (moveSettings.value.auto_move_fixed_times || []).filter(x => x !== t)
  if (moveSettings.value.auto_move_enabled && moveSettings.value.auto_move_schedule_type === 'fixed_times' && cur.length === 0) {
    ElMessage.warning('总开关处于开启状态，固定时间点至少需保留一个！若要清空，请先关闭总开关')
    return
  }
  moveSettings.value.auto_move_fixed_times = cur
  saveMoveSettings()
}

const saveMoveRule = async (rule) => {
  try {
    await api.post('/api/auto-move/rules', rule)
    saveOk('整理规则已更新')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
    await loadAutoMove()  // 保存失败：以服务端为准还原本地行状态（防止 UI 与库不一致）
  }
}

// 云下载模式内联开关（非对称）：开 = 校验源目录 + 确认弹窗；关 = 直接生效
const toggleDirMatch = async (row) => {
  if (!row.dir_match) {
    await saveMoveRule(row)
    return
  }
  try {
    await ElMessageBox.confirm(
      '本规则将按任务子目录名匹配（子目录内文件可为纯英文名），不再按文件名匹配；' +
      '源目录根部散落的文件不参与整理；目录内广告/微小体积文件自动过滤。',
      '开启云下载模式？',
      { type: 'warning', confirmButtonText: '开启', cancelButtonText: '取消' })
  } catch {
    row.dir_match = 0
    return
  }
  await saveMoveRule(row)
}

const deleteMoveRule = async (id) => {
  if (!await confirmAction({
    title: '删除这条整理映射？',
    message: '删除后该规则立即失效。',
    confirmText: '删除',
    danger: true,
  })) return
  try {
    await api.delete(`/api/auto-move/rules/${id}`)
    ElMessage.success('整理规则已删除')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const openAddMoveRule = () => {
  dialogVisible.value = true
}

const runAutoMoveNow = async () => {
  if (runningMove.value) return
  if (!moveSettings.value.auto_move_enabled) {
    ElMessage.warning('定时文件整理功能尚未开启，请先打开功能总开关！')
    return
  }
  runningMove.value = true
  try {
    await previewDlgRef.value?.runPreview()
  } finally {
    runningMove.value = false
  }
}

const onPreviewStarted = ({ gate_state, drain_status }) => {
  gateState.value = gate_state || 'draining'
  if (drain_status) drainStatus.value = drain_status
  startAutoMovePolling()
}

onMounted(() => {
  checkSysState()
  sysPoll.start()
  window.addEventListener('focus', checkSysState)
})

onUnmounted(() => {
  stopAutoMovePolling()
  sysPoll.stop()
  window.removeEventListener('focus', checkSysState)
})

defineExpose({ loadAutoMove })
</script>
