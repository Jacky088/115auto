<template>
  <div>
    <!-- 分类 → 目录 映射 -->
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar routes-header-toolbar" style="margin-bottom: 0">
          <div style="display: flex; align-items: center; gap: 8px">
            <span>分类 → 目录 映射</span>
            <el-tag size="small" class="count-tag">共 {{ routes.length }} 个分类</el-tag>
          </div>
          <div class="add-cat-row">
            <el-input v-model="newCat" placeholder="新分类名" class="new-cat-input" @keyup.enter="addRoute" />
            <el-button class="add-cat-btn" @click="addRoute">添加分类</el-button>
          </div>
          <div v-if="dirtyRoutes" class="routes-dirty-row">
            <el-tag type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
            <el-button type="primary" size="small" @click="saveRoutes">保存映射</el-button>
          </div>
          <el-tooltip content="无法识别分类的资源：跳过，或输入任意目录统一存放（如 /待整理），输入后回车确认">
            <div class="unclassified-row">
              <span class="unclassified-label">未分类动作：</span>
              <el-select v-model="unclassifiedAction" class="unclassified-select" filterable allow-create
                         default-first-option @change="dirtyRoutes = true">
                <el-option label="跳过（不转存）" value="skip" />
                <el-option label="/待整理" value="/待整理" />
              </el-select>
            </div>
          </el-tooltip>
        </div>
      </template>
      <p class="page-tip">
        本页设置各分类的转存位置（分享转存 / 云下载可分目录）。资源如何被判定为某个分类，
        由「匹配规则」页的正则规则与黑名单决定。
      </p>
      <el-table :data="routes" size="small">
        <el-table-column prop="category" label="分类" width="120" />
        <el-table-column label="分享转存目录" min-width="220">
          <template #default="{ row }">
            <el-button class="dir-btn" :class="{ 'dir-skip': isSkip(row.target_dir) }"
                       @click="openPicker(row, 'target')">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ isSkip(row.target_dir) ? '跳过（不转存）' : (row.target_dir || '请选择目录') }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="云下载目录" min-width="220">
          <template #default="{ row }">
            <el-button class="dir-btn" :class="{ 'dir-empty': !row.offline_dir }"
                       @click="openPicker(row, 'offline')">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.offline_dir || '留空 = 跟随分享转存' }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right">
          <template #default="{ $index }">
            <el-popconfirm title="确认删除该分类映射？保存后生效。" confirm-button-text="删除"
                           cancel-button-text="取消" confirm-button-type="danger" width="230"
                           @confirm="routes.splice($index, 1); dirtyRoutes = true">
              <template #reference><el-button type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div class="btn-row end" style="margin-top: 14px">
        <span v-if="dirtyRoutes" class="tip-text">修改需点击「保存目录映射」后生效</span>
        <el-button type="primary" :disabled="!dirtyRoutes" @click="saveRoutes">保存目录映射</el-button>
      </div>
    </el-card>

    <!-- 定时文件整理（按目录映射移动中文文件） -->
    <el-card shadow="never" class="page-card" style="margin-top: 16px">
      <template #header>
        <div class="organize-header-toolbar">
          <div class="header-left">
            <span class="organize-main-title">定时文件整理（自动归档与分流）</span>
            <el-tooltip
              :disabled="systemRunning"
              content="系统当前处于停止状态（不可操作）。请先在顶栏点击「运行」启动系统后，方可开启自动归档！"
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
              :content="!systemRunning ? '系统处于停止状态，请先在顶栏点击「运行」启动系统' : (isMediaMutexActive ? `当前正在执行「${activeMode === 'organize' ? '目录整理' : '媒体刮削'}」，已开启写锁保护，请稍后再试` : '定时整理功能当前为关闭状态，请先开启左侧总开关')"
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
        description="为防止多个 115 目录写任务并发冲突，定时分流归档暂不可同时运行；待整理/刮削任务完成或安全中断后将自动解除锁定。"
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
                  ? '当前为【总库智能分流（推荐）】：目标目录指定为媒体总库（如 /电视剧），系统自动识别库内所有剧集文件夹并分发'
                  : '当前为【一对一精确匹配】：目标目录作为指定剧目（如 /电视剧/折月亮 (2026)），仅移动匹配此剧名的文件'"
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
                <span class="feature-name">按季集防重</span>
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
                  ? '当前为【固定间隔轮询】：按设定的间隔时长（如每 12 小时）周期性排队扫描并整理'
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
              <el-select v-model="moveSettings.auto_move_interval_hours" style="width: 170px" @change="saveMoveSettings">
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
        <el-icon class="is-loading" style="color: #e6a23c; font-size: 16px; flex-shrink: 0"><Loading /></el-icon>
        <span>
          <strong>转存与云下载队列排空中（正在等待所有队列任务到达终点）：</strong>
          当前剩余 <strong>{{ drainStatus.unfinished }}</strong> 个任务处理中
          <template v-if="drainStatus.running">（{{ drainStatus.running }} 个转存中）</template>
          <template v-if="drainStatus.submitted">（{{ drainStatus.submitted }} 个云下载中）</template>
          <template v-if="drainStatus.retrying">（{{ drainStatus.retrying }} 个等待重试）</template>
          <template v-if="drainStatus.queued">（{{ drainStatus.queued }} 个排队中）</template>
          ，系统必须等待全部成功、云下载落盘或重试彻底结束后，自动独占开启移动归档！
        </span>
      </div>

      <div v-else-if="gateState === 'organizing'" class="organizing-banner">
        <el-icon class="is-loading" style="color: #409eff; font-size: 16px; flex-shrink: 0"><Loading /></el-icon>
        <span>
          <strong>队列已全部终态，正在独占执行文件整理移动中：</strong>转存工作器已挂起（零并发保护 115 账号安全），正在扫描各源目录并智能归类...
        </span>
      </div>

      <!-- 规则表格工具栏：紧贴列表上方，便于直观管理与添加 -->
      <div class="rules-table-toolbar">
        <div class="rules-toolbar-left">
          <span class="rules-toolbar-title">整理规则映射列表</span>
          <el-tag v-if="moveRules.length" size="small" class="count-tag">共 {{ moveRules.length }} 条规则</el-tag>
          <span class="rules-toolbar-tip">按由上至下顺序依次排队扫描与移动（只移动正片文件，文件夹本身不移动）</span>
        </div>
        <div class="rules-toolbar-right">
          <el-button type="primary" size="small" @click="openAddMoveRule">
            <el-icon><Plus /></el-icon>
            <span>添加整理规则</span>
          </el-button>
        </div>
      </div>

      <!-- 规则表格 -->
      <div class="table-responsive-wrapper">
      <el-table :data="moveRules" size="small">
        <template #empty>
          <div class="rules-empty-box">
            <p>暂无整理规则，点击下方或上方「添加整理规则」配置对应关系</p>
            <el-button type="primary" plain size="small" @click="openAddMoveRule" style="margin-top: 6px">
              <el-icon><Plus /></el-icon>
              <span>添加整理规则</span>
            </el-button>
          </div>
        </template>
        <el-table-column label="整理模式" width="115">
          <template #default="{ row }">
            <el-tag
              :type="(row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library') ? 'success' : 'primary'"
              effect="plain"
              size="small"
            >
              {{ (row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library') ? '总库分流' : '精确匹配' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="源目录（扫描位置）" min-width="190">
          <template #default="{ row }">
            <el-button class="dir-btn" @click="openMovePicker(row, 'move_source')">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.source_dir || '请选择源目录' }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="目标目录（移入位置）" min-width="190">
          <template #default="{ row }">
            <el-button class="dir-btn" @click="openMovePicker(row, 'move_target')">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.target_dir || '请选择目标目录' }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="匹配关键词" width="130">
          <template #default="{ row }">
            <span v-if="(row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library')">
              <el-tag type="info" size="small">自动识别总库子剧</el-tag>
            </span>
            <span v-else>
              <el-tag type="primary" effect="plain" size="small">
                {{ row.keyword || extractKeyword(row.target_dir) || '未设定' }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="上次整理状态" width="150">
          <template #default="{ row }">
            <span v-if="row.last_run_at" style="font-size: var(--fs-caption); color: var(--saas-text-main)">
              {{ row.last_moved_count > 0 ? `移动 ${row.last_moved_count} 个文件` : '无新匹配文件' }}
            </span>
            <span v-else style="color: var(--saas-text-sub); font-size: var(--fs-caption)">尚未执行</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch
              v-model="row.enabled"
              :active-value="1"
              :inactive-value="0"
              @change="saveMoveRule(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right">
          <template #default="{ row }">
            <el-popconfirm title="确认删除此整理映射？" confirm-button-text="删除" cancel-button-text="取消"
                           confirm-button-type="danger" width="200" @confirm="deleteMoveRule(row.id)">
              <template #reference><el-button type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      </div>
    </el-card>

    <!-- 115 目录选择器（全页复用） -->
    <DirPicker v-model="pickerVisible" :title="pickerTitle" :allow-skip="pickerKind === 'target'"
               :allow-empty="pickerKind === 'offline'" @pick="onPickDir" />

    <!-- 添加整理规则弹窗 -->
    <el-dialog v-model="dialogVisible" title="添加文件整理规则" :width="'min(540px, 94vw)'" destroy-on-close>
      <el-form :model="newRuleForm" label-width="100px" size="small">
        <el-form-item label="整理模式">
          <el-radio-group v-model="newRuleForm.mode">
            <el-radio label="default">跟随默认 ({{ moveSettings.auto_move_mode === 'library' ? '总库分流' : '一对一精确' }})</el-radio>
            <el-radio label="library">总库智能分流</el-radio>
            <el-radio label="exact">一对一精确匹配</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="源目录" required>
          <el-button class="dir-btn" style="width: 100%" @click="openDialogPicker('dialog_source')">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ newRuleForm.source_dir || '点击选择源目录（如 /自动转存）' }}</span>
          </el-button>
        </el-form-item>
        <el-form-item label="目标目录" required>
          <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
            <el-button class="dir-btn" style="width: 100%" @click="openDialogPicker('dialog_target')">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ newRuleForm.target_dir || (isDialogLibraryMode ? '选择影视总库（如 /电视剧）' : '选择具体剧集目录（如 /电视剧/折月亮 (2026)）') }}</span>
            </el-button>
            <span style="color: var(--saas-text-sub); font-size: var(--fs-caption); line-height: 1.4">
              {{ isDialogLibraryMode ? '总库模式：目标选电视剧总库，系统自动识别库内各剧集文件夹并精准分流。' : '精确模式：目标选具体某部剧的文件夹，仅移动匹配该剧的文件。' }}
            </span>
          </div>
        </el-form-item>
        <el-form-item v-if="!isDialogLibraryMode" label="匹配关键词">
          <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
            <el-input v-model="newRuleForm.keyword" placeholder="根据目标目录自动提取（如 庆余年、折月亮）" />
            <span style="color: var(--saas-text-sub); font-size: var(--fs-caption); line-height: 1.4">
              自动剥离 (2026) 年份与季级目录，精确提取纯净剧名。
            </span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!newRuleForm.source_dir || !newRuleForm.target_dir" @click="confirmAddMoveRule">
          确定添加
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import DirPicker from '../components/DirPicker.vue'

// ---------- 分类转存目录 ----------
const routes = ref([])
const unclassifiedAction = ref('skip')
const newCat = ref('')
const dirtyRoutes = ref(false)

// 目录选择器公共状态
const pickerVisible = ref(false)
const pickerKind = ref('target')
const pickerTitle = ref('选择 115 目录')
let pickerRow = null
let suppressWatch = false

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
let sysTimer = null

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
    ElMessage.warning('系统当前处于停止状态，请先在顶栏点击「运行」开启系统！')
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

watch(systemRunning, (isRunning) => {
  if (!isRunning) {
    moveSettings.value.auto_move_enabled = false
  }
})
let pollTimer = null
const runningMove = ref(false)
const dialogVisible = ref(false)
const newRuleForm = ref({
  source_dir: '',
  target_dir: '',
  keyword: '',
  mode: 'default',
})

const isDialogLibraryMode = computed(() => {
  const m = newRuleForm.value.mode
  if (m === 'library') return true
  if (m === 'exact') return false
  return moveSettings.value.auto_move_mode === 'library'
})

watch([routes, unclassifiedAction], () => {
  if (!suppressWatch) dirtyRoutes.value = true
}, { deep: true })

const extractKeyword = (path) => {
  if (!path) return ''
  const parts = path.trim().replace(/^\/+|\/+$/g, '').split('/').filter(p => p.trim())
  if (!parts.length) return ''
  let idx = parts.length - 1
  const seasonRe = /^(?:season\s*\d+|s\d{1,2}|第\s*[一二三四五六七八九十\d]+\s*季|特别篇|sp|specials?)$/i
  if (idx > 0 && seasonRe.test(parts[idx])) {
    idx -= 1
  }
  const candidate = parts[idx]
  const cleaned = candidate.replace(/[\(\[\{（【]?\s*(?:19\d{2}|20\d{2})\s*[\)\]\}）】]?/g, '').trim()
  return cleaned || candidate
}

const formatTime = (ts) => {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
}

const load = async () => {
  try {
    const { data } = await api.get('/api/rules')
    suppressWatch = true
    routes.value = data.routes
    unclassifiedAction.value = data.unclassified_action
    await nextTick()
    suppressWatch = false
    dirtyRoutes.value = false
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

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

const startAutoMovePolling = () => {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
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
    } catch (_) {}
  }, 2500)
}

const stopAutoMovePolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const isSkip = (d) => d === 'skip'

const openPicker = (row, kind) => {
  pickerRow = row
  pickerKind.value = kind
  pickerTitle.value = kind === 'target'
    ? `选择「${row.category}」的分享转存目录`
    : `选择「${row.category}」的云下载目录`
  pickerVisible.value = true
}

const openMovePicker = (row, kind) => {
  pickerRow = row
  pickerKind.value = kind
  pickerTitle.value = kind === 'move_source'
    ? '选择整理规则的源目录（扫描位置）'
    : '选择整理规则的目标目录（移入位置）'
  pickerVisible.value = true
}

const openDialogPicker = (kind) => {
  pickerKind.value = kind
  pickerTitle.value = kind === 'dialog_source'
    ? '选择整理规则的源目录（扫描位置）'
    : '选择整理规则的目标目录（移入位置）'
  pickerVisible.value = true
}

const onPickDir = (val) => {
  if (pickerKind.value === 'target') {
    if (pickerRow) pickerRow.target_dir = val
    dirtyRoutes.value = true
  } else if (pickerKind.value === 'offline') {
    if (pickerRow) pickerRow.offline_dir = val
    dirtyRoutes.value = true
  } else if (pickerKind.value === 'move_source') {
    if (pickerRow) {
      pickerRow.source_dir = val
      saveMoveRule(pickerRow)
    }
  } else if (pickerKind.value === 'move_target') {
    if (pickerRow) {
      pickerRow.target_dir = val
      pickerRow.keyword = extractKeyword(val)
      saveMoveRule(pickerRow)
    }
  } else if (pickerKind.value === 'dialog_source') {
    newRuleForm.value.source_dir = val
  } else if (pickerKind.value === 'dialog_target') {
    newRuleForm.value.target_dir = val
    newRuleForm.value.keyword = extractKeyword(val)
  }
}

const addRoute = () => {
  const cat = newCat.value.trim()
  if (!cat) return
  routes.value.push({ category: cat, target_dir: `/媒体库/${cat}`, offline_dir: '' })
  newCat.value = ''
  dirtyRoutes.value = true
}

const saveRoutes = async () => {
  try {
    await api.put('/api/rules', {
      routes: routes.value,
      unclassified_action: unclassifiedAction.value,
    })
    dirtyRoutes.value = false
    saveOk('目录映射已保存', '分类目录更改已生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
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
  }
}

const deleteMoveRule = async (id) => {
  try {
    await api.delete(`/api/auto-move/rules/${id}`)
    ElMessage.success('整理规则已删除')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const openAddMoveRule = () => {
  newRuleForm.value = { source_dir: '', target_dir: '', keyword: '', mode: 'default' }
  dialogVisible.value = true
}

const confirmAddMoveRule = async () => {
  if (!newRuleForm.value.source_dir || !newRuleForm.value.target_dir) return
  try {
    await api.post('/api/auto-move/rules', {
      source_dir: newRuleForm.value.source_dir,
      target_dir: newRuleForm.value.target_dir,
      keyword: newRuleForm.value.keyword || extractKeyword(newRuleForm.value.target_dir),
      mode: newRuleForm.value.mode || 'default',
    })
    dialogVisible.value = false
    ElMessage.success('整理规则已添加')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const runAutoMoveNow = async () => {
  if (!moveSettings.value.auto_move_enabled) {
    ElMessage.warning('定时文件整理功能尚未开启，请先打开功能总开关！')
    return
  }
  runningMove.value = true
  try {
    const { data } = await api.post('/api/auto-move/run')
    if (data.ok) {
      ElMessage.info(data.msg || '已发起排队整理，正在等待转存/云下载队列终点...')
      gateState.value = data.gate_state || 'draining'
      if (data.drain_status) drainStatus.value = data.drain_status
      startAutoMovePolling()
    } else {
      ElMessage.warning(data.msg || '执行未完成')
      runningMove.value = false
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
    runningMove.value = false
  }
}

useUnsavedGuard(dirtyRoutes)  // 有未保存更改时，离开页面弹窗确认

onMounted(() => {
  load()
  loadAutoMove()
  checkSysState()
  sysTimer = setInterval(checkSysState, 3000)
  window.addEventListener('focus', checkSysState)
})

onUnmounted(() => {
  stopAutoMovePolling()
  if (sysTimer) clearInterval(sysTimer)
  window.removeEventListener('focus', checkSysState)
})
</script>

<style scoped>
.page-tip {
  margin: 0 0 12px;
  color: var(--saas-text-sub);
  line-height: 1.6;
  font-size: 13px;
}
.dir-btn {
  width: 100%;
  justify-content: flex-start;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-btn .el-icon {
  margin-right: 0;
  color: var(--el-color-primary);
  flex-shrink: 0;
}
.dir-btn.dir-skip { color: #e6a23c; }
.dir-btn.dir-skip .el-icon { color: #e6a23c; }
.dir-btn.dir-empty { color: #909399; font-style: italic; }
.dir-btn.dir-empty .el-icon { color: #c0c4cc; }
.dir-btn:hover,
.dir-btn:active {
  background-color: var(--el-color-primary) !important;
  border-color: var(--el-color-primary) !important;
  color: #ffffff !important;
}
.dir-btn:hover *,
.dir-btn:active * {
  color: #ffffff !important;
}

/* ---- 定时文件整理：全新现代响应式布局 ---- */
.organize-header-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.organize-main-title {
  font-weight: 700;
  font-size: var(--fs-title);
  color: var(--saas-text-main);
}
/* 主总开关：加高圆润饱满，消除扁平感 */
.organize-master-switch {
  height: 32px !important;
}
.organize-master-switch :deep(.el-switch__core) {
  min-width: 98px !important;
  height: 30px !important;
  border-radius: 15px !important;
  padding: 0 8px !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
}
.organize-master-switch :deep(.el-switch__core .el-switch__inner) {
  font-size: 13px !important;
  font-weight: 600 !important;
  height: 30px !important;
  line-height: 30px !important;
}
.organize-master-switch :deep(.el-switch__core .el-switch__action) {
  width: 22px !important;
  height: 22px !important;
  top: 3px !important;
  left: 4px !important;
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2) !important;
}
.organize-master-switch.is-checked :deep(.el-switch__core .el-switch__action) {
  left: calc(100% - 26px) !important;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.organize-notice-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: rgba(64, 158, 255, 0.06);
  border: 1px solid rgba(64, 158, 255, 0.18);
  border-radius: var(--radius-sm, 8px);
  padding: 12px 14px;
  margin-bottom: 14px;
}
.notice-icon {
  color: var(--el-color-primary);
  font-size: 18px;
  margin-top: 1px;
  flex-shrink: 0;
}
.notice-body {
  flex: 1;
  min-width: 0;
}
.notice-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-color-primary-dark-2, #2b73af);
  margin-bottom: 4px;
}
.notice-desc {
  font-size: var(--fs-caption, 12px);
  color: var(--el-text-color-regular, #606266);
  line-height: 1.6;
}

/* 2 栏并列面板 */
.organize-config-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 14px;
}
.config-card {
  background: var(--subcard-bg);
  border: 1px solid var(--saas-line);
  border-radius: 12px;
  padding: 0 16px 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.config-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 -16px 2px;
  padding: 11px 16px;
  background: var(--subcard-head-bg);
  border-bottom: 1px solid var(--saas-line);
}
.config-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  line-height: 1.35;
  color: var(--saas-text-main);
}
.config-card-title .el-icon {
  color: var(--el-color-primary);
}
.last-run-chip {
  font-size: 11px;
  color: var(--el-text-color-secondary, #909399);
}
.schedule-risk-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 500;
  color: #d97706;
  background: rgba(217, 119, 6, 0.08);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid rgba(217, 119, 6, 0.2);
}
.schedule-risk-note {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: rgba(217, 119, 6, 0.04);
  border-radius: 6px;
  padding: 5px 8px;
  margin-top: 4px;
  margin-bottom: 8px;
  line-height: 1.4;
}

.config-form-item {
  display: flex;
  align-items: center;
  gap: 10px;
}
.config-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--label-secondary);
  min-width: 72px;
  flex-shrink: 0;
}
.config-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 胶囊单选按钮统一视觉规范 */
.capsule-radio-group :deep(.el-radio-button__inner) {
  border-radius: 6px;
  padding: 7px 14px;
  font-size: 12px;
  height: 32px;
  line-height: 16px;
}
.capsule-radio-group :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-top-left-radius: 6px;
  border-bottom-left-radius: 6px;
}
.capsule-radio-group :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-top-right-radius: 6px;
  border-bottom-right-radius: 6px;
}

/* 功能特性 2x2 开关卡片 */
.feature-switches-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-top: 2px;
}
.feature-switch-pill {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: var(--el-bg-color, #ffffff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  padding: 8px 12px;
  transition: all 0.2s ease;
}
.feature-switch-pill.is-active {
  border-color: rgba(64, 158, 255, 0.45);
  background: rgba(64, 158, 255, 0.04);
}
.feature-switch-pill.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
  background: var(--el-fill-color-lighter, #f5f7fa);
}
.feature-switch-pill :deep(.el-switch) {
  height: 26px;
}
.feature-switch-pill :deep(.el-switch__core) {
  height: 24px;
  min-width: 44px;
  border-radius: 12px;
}
.feature-switch-pill :deep(.el-switch__action) {
  width: 18px;
  height: 18px;
  top: 2px;
  left: 2px;
}
.feature-switch-pill :deep(.el-switch.is-checked .el-switch__action) {
  left: calc(100% - 20px);
}
.feature-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.feature-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.feature-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary, #909399);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 固定时间点区域 */
.fixed-times-layout {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fixed-tags-scroll {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.fixed-time-tag {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  height: 26px;
}
.empty-tag-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.time-select-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/* 48 小时执行时间预测嵌入卡片 */
.prediction-box {
  background: var(--el-bg-color, #ffffff);
  border: 1px dashed rgba(64, 158, 255, 0.35);
  border-radius: 8px;
  padding: 10px 12px;
  margin-top: auto;
}
.prediction-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  margin-bottom: 8px;
}
.prediction-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.prediction-chip {
  font-size: 11px;
  font-weight: 500;
}

/* 排空 / 整理中状态条 */
.drain-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(230, 162, 60, 0.1);
  border: 1px solid rgba(230, 162, 60, 0.3);
  border-radius: var(--radius-sm, 6px);
  padding: 10px 14px;
  margin-bottom: 12px;
  color: #b88230;
  font-size: 13px;
  line-height: 1.5;
}

.organizing-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(64, 158, 255, 0.1);
  border: 1px solid rgba(64, 158, 255, 0.3);
  border-radius: var(--radius-sm, 6px);
  padding: 10px 14px;
  margin-bottom: 12px;
  color: #2b73af;
  font-size: 13px;
  line-height: 1.5;
}

/* 规则列表独立工具栏：紧贴表格上方，消除割裂感 */
.rules-table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
  margin-bottom: 10px;
  padding: 10px 14px;
  background: var(--subcard-head-bg);
  border: 1px solid var(--saas-line);
  border-radius: 10px;
}
.rules-toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rules-toolbar-title {
  font-weight: 700;
  font-size: 15px;
  color: var(--saas-text-main);
}
.rules-toolbar-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.rules-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rules-empty-box {
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--el-text-color-secondary, #909399);
  font-size: 13px;
}

/* 顶部「分类 → 目录 映射」工具栏分组 */
.add-cat-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.new-cat-input {
  width: 150px;
}
.add-cat-btn {
  flex: none;
}
.routes-dirty-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
}
.unclassified-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #999;
  font-size: var(--fs-caption);
  margin-left: auto;
}
.unclassified-label {
  white-space: nowrap;
  flex: none;
}
.unclassified-select {
  width: 190px;
}

/* 表格响应式容器 */
.table-responsive-wrapper {
  width: 100%;
  overflow-x: auto;
  border-radius: var(--radius-sm, 6px);
  -webkit-overflow-scrolling: touch;
}

/* ---- 移动端与窄屏适配响应式 (@media) ---- */
@media (max-width: 900px) {
  .organize-config-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 768px) {
  .rules-table-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .rules-toolbar-right {
    justify-content: flex-end;
  }
  .routes-header-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .add-cat-row {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .routes-header-toolbar .new-cat-input {
    flex: 1 1 0%;
    min-width: 0;
    width: auto;
  }
  .routes-dirty-row {
    width: 100%;
    flex-wrap: wrap;
  }
  .unclassified-row {
    margin-left: 0;
    width: 100%;
  }
  .unclassified-select {
    flex: 1;
    min-width: 0;
    width: auto;
  }
  .organize-header-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .header-left {
    justify-content: space-between;
    width: 100%;
  }
  .header-actions {
    width: 100%;
    display: flex;
  }
  .header-actions .el-button {
    flex: 1;
  }
  .feature-switches-grid {
    grid-template-columns: 1fr;
  }
  .config-form-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }
  .capsule-radio-group {
    display: flex;
    width: 100%;
  }
  .capsule-radio-group :deep(.el-radio-button) {
    flex: 1;
  }
  .capsule-radio-group :deep(.el-radio-button__inner) {
    width: 100%;
    text-align: center;
    padding: 7px 8px;
  }
}

/* 长胶囊双选分段开关（长胶囊，左右文字，中间分隔符，选左高亮左，选右高亮右） */
.capsule-toggle {
  display: inline-flex;
  align-items: center;
  height: 32px;
  border-radius: 999px;
  border: 1px solid rgba(126, 146, 196, 0.40);
  background: rgba(255, 255, 255, 0.70);
  box-shadow: 0 1px 3px rgba(31, 45, 90, 0.05);
  overflow: hidden;
  user-select: none;
  cursor: pointer;
  transition: all .2s ease;
}
html.dark .capsule-toggle {
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(20, 28, 48, 0.75);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
}
.capsule-toggle.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
  pointer-events: none;
}

.capsule-seg {
  flex: 1;
  height: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--saas-text-main);
  white-space: nowrap;
  transition: all .25s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}
html.dark .capsule-seg {
  color: #b7c4de;
}

/* 未选中时悬停 */
.capsule-seg:not(.is-active):hover {
  background: rgba(79, 107, 255, 0.14);
  color: var(--el-color-primary);
}
html.dark .capsule-seg:not(.is-active):hover {
  background: rgba(79, 107, 255, 0.22);
  color: #fff;
}

/* 选中高亮（左右选中对应侧完全填充渐变色与清晰白字） */
.capsule-seg.is-active {
  background: var(--grad-brand);
  color: #ffffff !important;
  font-weight: 600;
}
html.dark .capsule-seg.is-active {
  background: var(--grad-brand);
  color: #ffffff !important;
}

/* 中间分隔符 */
.capsule-divider {
  width: 1px;
  height: 18px;
  background: rgba(126, 146, 196, 0.40);
  flex-shrink: 0;
  transition: opacity .2s ease;
}
html.dark .capsule-divider {
  background: rgba(255, 255, 255, 0.20);
}

@media (max-width: 768px) {
  .capsule-toggle {
    width: 100%;
  }
}

</style>