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
            content="系统未运行，请先点击顶栏「运行」"
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
      <el-table-column width="120">
        <template #header>
          <span>媒体类型
            <el-tooltip placement="top" effect="dark">
              <template #content>
                按标题匹配时的文件归属判别（配合两库同名场景）：<br />
                · 电影库：跳过带集数标识（S01E01/第1集）的文件，剧集交给剧集规则；<br />
                · 剧集库：正常认领（含剧场版/特别篇）；<br />
                · 自动：不区分，与历史行为一致。<br />
                与规则顺序无关，无论哪条规则先执行结果一致。
              </template>
              <el-icon class="q-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </span>
        </template>
        <template #default="{ row }">
          <el-tooltip placement="top" :disabled="row.media_type !== 'movie'"
                      content="电影库规则会跳过带集数标识（S01E01/第1集）的文件，避免重名影片错位归档">
            <el-select v-model="row.media_type" size="small" style="width: 104px" fit-input-width
                       @change="saveMoveRule(row)">
              <el-option label="自动" value="auto" />
              <el-option label="电影库" value="movie" />
              <el-option label="剧集库" value="tv" />
            </el-select>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column width="88">
        <template #header>
          <span>云下载
            <el-tooltip placement="top" effect="dark">
              <template #content>
                云下载目录专用模式：<br />
                115 云下载的中文标题只在任务目录名上，目录内文件多为纯英文名——开启后按<b>任务子目录名</b>匹配（自动剥除含网址的广告括号段），目录内文件继承归属。<br />
                电影/剧集按集数标识自动判别；广告域名/微小体积文件自动过滤不入库；<br />
                仅当源目录是分类路由配置的云下载目录时可开启，配置变更后失效会自动拦下并告警。
              </template>
              <el-icon class="q-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </span>
        </template>
        <template #default="{ row }">
          <el-tooltip placement="top" :disabled="!(row.dir_match && !cloudValid(row))"
                      content="源目录已不是当前配置的云下载目录，云下载模式不会生效，请修改源目录或关闭此开关">
            <div style="display: flex; align-items: center; gap: 4px">
              <el-switch v-model="row.dir_match" :active-value="1" :inactive-value="0"
                         :disabled="!row.dir_match && !cloudValid(row)"
                         @change="toggleDirMatch(row)" />
              <el-icon v-if="row.dir_match && !cloudValid(row)" style="color: var(--el-color-warning)">
                <WarningFilled />
              </el-icon>
            </div>
          </el-tooltip>
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
          <el-tooltip placement="top" :disabled="!(row.dir_match && !cloudValid(row))"
                      content="云下载模式未生效（源目录已不是云下载目录）：请改回云下载目录，或关闭云下载开关回落基本规则后再启用">
            <span>
              <el-switch
                v-model="row.enabled"
                :active-value="1"
                :inactive-value="0"
                :disabled="!!(row.dir_match && !cloudValid(row))"
                @change="saveMoveRule(row)"
              />
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="84" fixed="right">
        <template #default="{ row }">
          <el-popconfirm title="删除这条整理映射？删除后该规则立即失效。" confirm-button-text="删除" cancel-button-text="取消"
                         confirm-button-type="danger" width="200" @confirm="deleteMoveRule(row.id)">
            <template #reference><el-button type="danger" text>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
    </div>
  </el-card>

    <DirPicker v-model="pickerVisible" :title="pickerTitle" @pick="onPickDir" />

  <!-- 添加整理规则弹窗 -->
  <el-dialog v-model="dialogVisible" title="添加文件整理规则" :width="'min(540px, 94vw)'" destroy-on-close>
    <el-form class="add-rule-form" :model="newRuleForm" label-width="100px" size="small">
      <el-form-item label="整理模式">
        <el-radio-group v-model="newRuleForm.mode">
          <el-radio label="default">跟随默认 ({{ moveSettings.auto_move_mode === 'library' ? '总库分流' : '一对一精确' }})</el-radio>
          <el-radio label="library">总库智能分流</el-radio>
          <el-radio label="exact">一对一精确匹配</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="媒体类型">
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-radio-group v-model="newRuleForm.media_type">
            <el-radio label="auto">自动</el-radio>
            <el-radio label="movie">电影库</el-radio>
            <el-radio label="tv">剧集库</el-radio>
          </el-radio-group>
          <span style="color: var(--saas-text-sub); font-size: var(--fs-caption); line-height: 1.4">
            电影库跳过带集数标识的文件，避免两库同名错位
          </span>
        </div>
      </el-form-item>
      <el-form-item label="云下载模式">
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-checkbox v-model="newRuleForm.dir_match" :disabled="!sourceIsCloudDir">
            按父目录名匹配
            <el-tooltip placement="top" effect="dark">
              <template #content>
                适用场景：115 云下载的中文标题只在任务目录名上（如「【广告www..】奥德赛[简繁英字幕].The.Odyssey.2026…」），<br />
                目录内文件多为纯英文名（The.Odyssey.2026….mkv）。<br /><br />
                开启后的行为变化：<br />
                · 匹配对象从文件名换成任务子目录名（自动剥除含网址的广告括号段）；<br />
                · 目录内纯英文文件继承目录归属，电影/剧集按集数标识自动判别；<br />
                · 广告域名/微小体积（&lt;10MB）文件自动过滤，不入媒体库；<br />
                · 源目录根部散落的单文件（如 a.mp4）不参与，留待手动处理；<br />
                · 字幕不跟移（留给整理刮削配对）。<br /><br />
                仅当源目录 = 分类路由配置的云下载目录时可开启；配置变更后自动失效并告警。
              </template>
              <el-icon class="q-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </el-checkbox>
          <span v-if="sourceIsCloudDir" style="color: var(--el-color-success); font-size: var(--fs-caption)">
            已匹配云下载目录 {{ normPath(newRuleForm.source_dir) }}
          </span>
          <span v-else style="color: var(--el-color-warning); font-size: var(--fs-caption); line-height: 1.4">
            源目录不是云下载目录 {{ cloudDirs.join('、') || '尚未配置' }}，此模式不可用
          </span>
          <span v-if="newRuleForm.dir_match" class="hint-line" style="color: var(--el-color-warning)">
            ⚠ 开启后本规则按<b>任务子目录名</b>匹配（子目录内文件可为纯英文名），不再按文件名匹配；
            源目录根部散落的文件不参与整理；目录内广告/微小体积文件自动过滤。
            若日后源目录不再是云下载目录，本模式会自动失效并告警
          </span>
        </div>
      </el-form-item>
      <el-form-item label="源目录" required>
        <el-button class="dir-btn" size="small" style="width: 100%" @click="openDialogPicker('dialog_source')">
          <el-icon><FolderOpened /></el-icon>
          <span>{{ newRuleForm.source_dir || '点击选择源目录（如 /自动转存）' }}</span>
        </el-button>
      </el-form-item>
      <el-form-item label="目标目录" required>
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-button class="dir-btn" size="small" style="width: 100%" @click="openDialogPicker('dialog_target')">
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

    <AutoMovePreviewDialog ref="previewDlgRef" @started="onPreviewStarted" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'
import { usePolling } from '../../composables/usePolling'
import DirPicker from '../DirPicker.vue'
import AutoMovePreviewDialog from './AutoMovePreviewDialog.vue'

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

// 系统运行状态的轻量轮询（3 秒），页面不可见时自动暂停
const sysPoll = usePolling(checkSysState, 3000)

watch(systemRunning, (isRunning) => {
  if (!isRunning) {
    moveSettings.value.auto_move_enabled = false
  }
})
const runningMove = ref(false)
const dialogVisible = ref(false)
const newRuleForm = ref({
  source_dir: '',
  target_dir: '',
  keyword: '',
  mode: 'default',
  media_type: 'auto',
  dir_match: false,
})

// 云下载目录集合与归一化比对（后端在 rules 接口下发 cloud_dirs）
const cloudDirs = ref([])
const normPath = (p) => {
  const t = String(p || '').trim()
  return t ? '/' + t.replace(/^\/+|\/+$/g, '') : ''
}
const sourceIsCloudDir = computed(() =>
  !!newRuleForm.value.source_dir && cloudDirs.value.includes(normPath(newRuleForm.value.source_dir)))

// 选完目录后联动：目标路径预选媒体类型；源目录为云下载目录时自动开启按父目录名匹配（均可手动改）
watch(() => newRuleForm.value.target_dir, (t) => {
  if (!t) return
  if (/电影|影片/.test(t)) newRuleForm.value.media_type = 'movie'
  else if (/电视剧|剧集|短剧|动漫|综艺|纪录片/.test(t)) newRuleForm.value.media_type = 'tv'
})
watch(() => newRuleForm.value.source_dir, () => {
  if (sourceIsCloudDir.value) {
    newRuleForm.value.dir_match = true
  } else {
    newRuleForm.value.dir_match = false
  }
})

const isDialogLibraryMode = computed(() => {
  const m = newRuleForm.value.mode
  if (m === 'library') return true
  if (m === 'exact') return false
  return moveSettings.value.auto_move_mode === 'library'
})


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
  const cleaned = candidate.replace(/[([{（【]?\s*(?:19\d{2}|20\d{2})\s*[)\]}）】]?/g, '').trim()
  return cleaned || candidate
}

const formatTime = (ts) => {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
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


const openMovePicker = (row, kind) => {
  pickerRow = row
  pickerKind.value = kind
  pickerTitle.value = kind === 'move_source'
    ? '选择整理监控的源目录'
    : (moveSettings.value.auto_move_mode === 'library' ? '选择媒体总库目标目录' : '选择剧集目标目录')
  pickerVisible.value = true
}

const openDialogPicker = (kind) => {
  pickerRow = null
  pickerKind.value = kind
  pickerTitle.value = kind === 'dialog_source' ? '选择源目录' : '选择目标目录'
  pickerVisible.value = true
}

const onPickDir = (val) => {
  if (pickerKind.value === 'move_source') {
    if (pickerRow) {
      const oldSource = pickerRow.source_dir
      pickerRow.source_dir = val
      if (pickerRow.dir_match && !cloudDirs.value.includes(normPath(val))) {
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
  } else if (pickerKind.value === 'dialog_source') {
    newRuleForm.value.source_dir = val
  } else if (pickerKind.value === 'dialog_target') {
    newRuleForm.value.target_dir = val
    newRuleForm.value.keyword = extractKeyword(val)
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
const cloudValid = (row) => cloudDirs.value.includes(normPath(row.source_dir))
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
  try {
    await api.delete(`/api/auto-move/rules/${id}`)
    ElMessage.success('整理规则已删除')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const openAddMoveRule = () => {
  newRuleForm.value = { source_dir: '', target_dir: '', keyword: '', mode: 'default', media_type: 'auto', dir_match: false }
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
      media_type: newRuleForm.value.media_type || 'auto',
      dir_match: !!newRuleForm.value.dir_match,
    })
    dialogVisible.value = false
    ElMessage.success('整理规则已添加')
    await loadAutoMove()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
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
