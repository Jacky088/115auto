<template>
  <div v-loading="pageLoading">
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
          <div class="unclassified-block">
            <div class="unclassified-row">
              <span class="unclassified-label">未分类动作：</span>
              <el-tooltip trigger="click" effect="light" placement="bottom-end" popper-class="unclassified-tip-popper">
                <template #content>
                  <div class="unclassified-tip-text">{{ unclassifiedTip }}</div>
                </template>
                <button type="button" class="unclassified-help" aria-label="查看未分类动作说明">?</button>
              </el-tooltip>
              <el-select v-model="unclassifiedAction" class="unclassified-select" filterable allow-create
                         fit-input-width default-first-option @change="onUnclassifiedChange">
                <el-option label="跳过（不转存、不云下载）" value="skip" />
                <el-option label="/待整理" value="/待整理" />
              </el-select>
            </div>
          </div>
        </div>
      </template>
      <p class="page-tip">
        本页设置各分类的转存位置（分享转存 / 云下载可分目录）。资源如何被判定为某个分类，
        由频道消息自带的标签（#电影 / #剧集 等）与「匹配规则」页的正则规则共同决定；黑名单只负责拦截，不参与分类。
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
            <el-popconfirm title="删除这条分类映射？保存后立即生效。" confirm-button-text="删除"
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

    <!-- 115 目录选择器（全页复用） -->
    <DirPicker v-model="pickerVisible" :title="pickerTitle" :allow-skip="pickerKind === 'target'"
               :allow-empty="pickerKind === 'offline'" @pick="onPickDir" />

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

    <!-- 整理预览弹窗（两段式执行第一步）：确认后才真正排队执行 -->
    <el-dialog v-model="previewVisible" title="整理预览 — 将移动以下文件" :width="'min(860px, 96vw)'" top="6vh">
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
      <el-table :data="previewRows" size="small" max-height="46vh">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="name" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="from_path" label="源位置" width="150" show-overflow-tooltip />
        <el-table-column prop="dest_path" label="目标位置" min-width="220" show-overflow-tooltip />
        <el-table-column prop="size" label="大小" width="90">
          <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="matched" label="命中词" width="120" show-overflow-tooltip />
        <el-table-column prop="rule_label" label="规则" width="170" show-overflow-tooltip />
      </el-table>
      <div v-if="previewData?.rules?.some((r) => r.skip_ep > 0 || r.skip_movie > 0 || r.skip_ambiguous > 0 || r.skip_junk > 0 || r.error)" style="margin-top: 10px">
        <div v-for="r in previewData.rules" :key="r.rule_id" style="font-size: var(--fs-caption); color: var(--saas-text-sub); line-height: 1.7">
          <span v-if="r.skip_ep > 0">「{{ r.target_dir }}」按季集防重将跳过 {{ r.skip_ep }} 个文件{{ r.skip_ep_samples?.length ? `（如 ${r.skip_ep_samples.slice(0, 2).join('、')}）` : '' }}；</span>
          <span v-if="r.skip_movie > 0">「{{ r.target_dir }}」电影库规则跳过 {{ r.skip_movie }} 个带集数标识的文件（留给剧集规则）；</span>
          <span v-if="r.skip_ambiguous > 0" style="color: var(--el-color-warning)">
            「{{ r.target_dir }}」同名歧义跳过 {{ r.skip_ambiguous }} 个文件（多库同名且无集数标识，留源目录待手动处理）{{ r.skip_ambiguous_samples?.length ? `（如 ${r.skip_ambiguous_samples.slice(0, 2).join('、')}）` : '' }}；
          </span>
          <span v-if="r.skip_junk > 0">「{{ r.target_dir }}」云下载垃圾过滤跳过 {{ r.skip_junk }} 个文件（广告域名/微小体积）；</span>
          <span v-if="r.error" style="color: var(--el-color-danger)">「{{ r.target_dir }}」{{ r.error }}；</span>
        </div>
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button v-if="previewRows.length" type="primary" :loading="previewConfirming" @click="confirmRunAfterPreview">
          确认执行（共 {{ previewRows.length }} 个文件）
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { showScanLoading, closeScanLoading } from '../composables/scanLoading'
import { usePolling } from '../composables/usePolling'
import { api, errMsg, fmtSize, saveOk, LONG_SCAN_TIMEOUT } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import DirPicker from '../components/DirPicker.vue'

// 首屏数据加载态：分类映射 + 整理规则都到位后再撤掉遮罩，避免表格先空后跳
const pageLoading = ref(true)

// ---------- 分类转存目录 ----------
const routes = ref([])
const unclassifiedAction = ref('skip')
let unclassifiedSaved = 'skip'

// 说明文案跟随当前选择：跳过 / 待整理 / 用户自定义目录分别给出对应描述
const unclassifiedTip = computed(() => {
  if (unclassifiedAction.value === 'skip') {
    return '当前规则：跳过（不转存、不云下载）。说明：规则仅适用于 TG 频道，' +
           '对转存和云下载同时生效！'
  }
  const dir = unclassifiedAction.value || '/待整理'
  return `当前规则：未分类资源将转存到 ${dir}（目录不存在时程序会自动创建）。` +
         '说明：规则仅适用于 TG 频道，对转存和云下载同时生效！' +
         '如需更换目录，请手动输入目录名称，如：/未整理，回车确认，点击保存后生效！'
})

// 手动输入的未分类目录校验：必须是 / 开头的 115 网盘子目录（与后端校验一致）
const dirError = (p) => {
  if (!p || p === 'skip') return ''
  if (!p.startsWith('/')) return '目录需以 / 开头，填写 115 网盘路径，例如 /未整理'
  if (p === '/') return '不能使用网盘根目录 /，请填写一个子目录，例如 /未整理'
  if (p.split('/').some((seg) => seg === '..')) return '目录不能包含 ..'
  if (/\s/.test(p)) return '目录不能包含空格，请去掉空格后重试'
  if (p.length > 200) return '目录过长，请控制在 200 个字符以内'
  return ''
}

const onUnclassifiedChange = (val) => {
  const err = dirError(val)
  if (err) {
    ElMessageBox.alert(err, '目录格式有误', {
      type: 'warning',
      confirmButtonText: '重新输入',
      callback: () => { unclassifiedAction.value = unclassifiedSaved },
    })
    return
  }
  dirtyRoutes.value = true
}
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
watch(() => newRuleForm.value.source_dir, (t) => {
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
    unclassifiedSaved = data.unclassified_action
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
      const oldSource = pickerRow.source_dir
      pickerRow.source_dir = val
      // 云下载模式规则改到非云下载目录：确认后自动关闭云下载模式，回落按文件名匹配的基本规则
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
          pickerRow.source_dir = oldSource  // 取消：还原目录，不保存
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
    unclassifiedSaved = unclassifiedAction.value
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
  if (!moveSettings.value.auto_move_enabled) {
    ElMessage.warning('定时文件整理功能尚未开启，请先打开功能总开关！')
    return
  }
  // 缓存命中：10 分钟内的预览结果直接拉起，不重复消耗 115 扫描请求
  if (previewCache.value && Date.now() - previewCache.value.ts < PREVIEW_CACHE_TTL) {
    openPreviewDialog(previewCache.value.data, true)
    return
  }
  // 两段式执行：扫描期间全屏冻结页面，防止误操作
  runningMove.value = true
  showScanLoading()
  let pv = null
  try {
    const { data } = await api.post('/api/auto-move/preview', null, { timeout: LONG_SCAN_TIMEOUT })
    pv = data
  } catch (e) {
    closeScanLoading()
    ElMessage.error(errMsg(e))
    return
  } finally {
    closeScanLoading()
    runningMove.value = false
  }
  if (!pv || !pv.ok) {
    ElMessage.warning((pv && pv.msg) || '预览失败')
    return
  }
  previewCache.value = { ts: Date.now(), data: pv }
  if (!pv.total_planned) {
    // 0 计划但存在拦截/跳过时仍然弹窗展示统计——帮助定位“为什么没分流”
    const hasSkips = (pv.rules || []).some((r) =>
      r.skip_ep > 0 || r.skip_movie > 0 || r.skip_ambiguous > 0 || r.skip_junk > 0 || r.skip_dedup > 0)
    if (hasSkips) {
      openPreviewDialog(pv, false)
    } else {
      ElMessage.info('当前没有需要移动的文件')
    }
    return
  }
  openPreviewDialog(pv, false)
}

// 预览弹窗数据：按规则分组的计划明细与跳过统计
const previewVisible = ref(false)
const previewData = ref(null)
const previewConfirming = ref(false)

// 预览缓存与自动暂存：扫描结果缓存 10 分钟，期内再次点击直接调出（确认执行仍会重新扫描，文件有变动按最新状态整理）
const PREVIEW_CACHE_TTL = 10 * 60 * 1000
const previewCache = ref(null)      // { ts, data }
const previewFromCache = ref(false)
const previewCountdown = ref(0)     // 弹窗自动暂存倒计时（秒）= 缓存剩余寿命
let previewTimer = null

const openPreviewDialog = (data, fromCache) => {
  previewData.value = data
  previewFromCache.value = !!fromCache
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
      // 10 分钟未响应：自动暂存关闭（结果已在缓存，期内再次点击可直接调出）
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
const previewRescan = async () => {
  previewVisible.value = false
  previewCache.value = null
  stopPreviewCountdown()
  await runAutoMoveNow()
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

const confirmRunAfterPreview = async () => {
  previewConfirming.value = true
  try {
    const { data } = await api.post('/api/auto-move/run')
    if (data.ok) {
      previewVisible.value = false
      previewCache.value = null
      stopPreviewCountdown()
      ElMessage.info(data.msg || '已发起排队整理，正在等待转存/云下载队列终点...')
      gateState.value = data.gate_state || 'draining'
      if (data.drain_status) drainStatus.value = data.drain_status
      startAutoMovePolling()
    } else {
      ElMessage.warning(data.msg || '执行未完成')
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    previewConfirming.value = false
  }
}

useUnsavedGuard(dirtyRoutes)  // 有未保存更改时，离开页面弹窗确认

onUnmounted(stopPreviewCountdown)
onMounted(async () => {
  try {
    await Promise.all([load(), loadAutoMove()])
  } finally {
    pageLoading.value = false
  }
  checkSysState()
  sysPoll.start()
  window.addEventListener('focus', checkSysState)
})

onUnmounted(() => {
  stopAutoMovePolling()
  sysPoll.stop()
  window.removeEventListener('focus', checkSysState)
})
</script>

<style scoped>
.page-tip {
  margin: 0 0 12px;
  color: var(--saas-text-sub);
  line-height: 1.6;
  font-size: var(--fs-caption);
}
.q-icon { color: var(--saas-text-sub); cursor: help; margin-left: 2px; font-size: var(--fs-caption); }
/* 弹窗内目录选择按钮：与表单输入框同高同圆角，保持整体主题一致（覆盖主题对 small 按钮的统一加高） */
.dir-btn .el-icon {
  margin-right: 0;
  color: var(--el-color-primary);
  flex-shrink: 0;
}
.dir-btn.dir-skip { color: #e6a23c; }
.dir-btn.dir-skip .el-icon { color: #e6a23c; }
.dir-btn.dir-empty { color: var(--el-text-color-secondary); font-style: italic; }
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
  font-size: var(--fs-caption) !important;
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
  font-size: var(--fs-page);
  margin-top: 1px;
  flex-shrink: 0;
}
.notice-body {
  flex: 1;
  min-width: 0;
}
.notice-title {
  font-weight: 600;
  font-size: var(--fs-caption);
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
  font-size: var(--fs-body);
  line-height: 1.35;
  color: var(--saas-text-main);
}
.config-card-title .el-icon {
  color: var(--el-color-primary);
}
.last-run-chip {
  font-size: var(--fs-caption);
  color: var(--el-text-color-secondary, #909399);
}
.schedule-risk-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-caption);
  font-weight: 500;
  color: #d97706;
  background: rgba(217, 119, 6, 0.08);
  padding: 2px 8px;
  border-radius: var(--r-md);
  border: 1px solid rgba(217, 119, 6, 0.2);
}
.schedule-risk-note {
  font-size: var(--fs-caption);
  color: var(--el-text-color-secondary);
  background: rgba(217, 119, 6, 0.04);
  border-radius: var(--r-sm);
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
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--saas-text-main);
  min-width: 78px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
}
.config-label::before {
  content: "";
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--el-color-primary, #4f6bff);
  margin-right: 6px;
  flex-shrink: 0;
  box-shadow: 0 0 0 1.5px rgba(79, 107, 255, 0.18);
}
.config-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 胶囊单选按钮统一视觉规范 */
.capsule-radio-group :deep(.el-radio-button__inner) {
  border-radius: var(--r-sm);
  padding: 7px 14px;
  font-size: var(--fs-caption);
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
  min-width: 0; /* 网格项可收缩：长说明触发省略号而不是撑爆容器（移动端裁切根因） */
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
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.feature-hint {
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
  font-weight: 600;
  padding: 2px 8px;
  height: 26px;
}
.empty-tag-tip {
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
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
  border-radius: var(--r-md);
}
.rules-toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rules-toolbar-title {
  font-weight: 700;
  font-size: var(--fs-body);
  color: var(--saas-text-main);
}
.rules-toolbar-tip {
  font-size: var(--fs-caption);
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
  font-size: var(--fs-caption);
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
.unclassified-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  margin-left: auto;
  min-width: 0;
}
.unclassified-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: var(--fs-caption);
}
.unclassified-label {
  white-space: nowrap;
  flex: none;
  font-size: var(--el-font-size-base);
}
.unclassified-help {
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 50%;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
html.dark .unclassified-help {
  border-color: rgba(129, 152, 255, .65);
  background: rgba(94, 124, 255, .22);
  color: #b8c4ff;
}
.unclassified-help:hover,
.unclassified-help:focus-visible {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
  color: #fff;
  outline: none;
}
:global(.unclassified-tip-popper) {
  max-width: calc(100vw - 32px);
}
.unclassified-tip-text {
  width: 420px;
  max-width: calc(100vw - 48px);
  white-space: normal;
  color: var(--el-text-color-regular);
  font-size: var(--el-font-size-base);
  line-height: 1.7;
}
.unclassified-select {
  width: 220px;
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
  .unclassified-block {
    width: 100%;
    align-items: stretch;
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
  border-radius: var(--r-pill);
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
  font-size: var(--fs-caption);
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