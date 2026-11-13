<template>
  <div class="media-page">
    <!-- 顶部系统运行状态联动警告 -->
    <el-alert
      v-if="!status.running"
      type="warning"
      show-icon
      :closable="false"
      class="mb16"
      title="顶栏系统运行开关已停止：目录整理与媒体刮削（含提交队列）均已禁用，请先在右上角点击「运行」开启系统。"
    />
    <el-alert
      v-else-if="status.gate.active_mode === 'auto_move'"
      type="info"
      show-icon
      :closable="false"
      class="mb16"
      title="当前「定时文件整理 (auto_move)」正在独占执行 115 文件操作，互斥保护中，请等待结束后再运行整理或刮削。"
    />
    <el-alert
      v-else-if="isMediaRunning && status.gate.active_mode !== activeTab"
      type="info"
      show-icon
      :closable="false"
      class="mb16"
      :title="`后台正在执行「${status.gate.active_mode === 'organize' ? '目录整理' : '媒体刮削'}」，单线程互斥锁生效中...`"
    />

    <!-- 主工作台：三大独立程序与设置标签页 -->
    <el-card shadow="never" class="page-card">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- ======================================================== -->
        <!-- Tab 1: 📁 目录整理（完全独立的确认目录 -> 整理 -> 结果闭环） -->
        <!-- ======================================================== -->
        <el-tab-pane name="organize">
          <template #label>
            <span>📁 目录整理</span>
            <el-badge
              v-if="(status.counts.records || 0) > 0"
              :value="status.counts.records"
              type="info"
              class="tab-badge"
            />
          </template>

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
                      title="确认删除该条整理规则？删除后无法恢复。"
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
                        title="确认撤销此条整理？将把文件重命名并移回源目录。"
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
                        title="确认撤销同批次全部整理记录？将批量移回源目录。"
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
        </el-tab-pane>

        <!-- ======================================================== -->
        <!-- Tab 2: 🎬 媒体刮削（完全独立的确认目录 -> 刮削 -> 核对闭环） -->
        <!-- ======================================================== -->
        <el-tab-pane name="scrape">
          <template #label>
            <span>🎬 媒体刮削</span>
            <el-badge
              v-if="(status.counts.need_confirm + status.counts.staged) > 0"
              :value="status.counts.need_confirm + status.counts.staged"
              class="tab-badge"
            />
          </template>

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
                      title="确认删除该条刮削目录规则？删除后无法恢复。"
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
                  title="确认清理所有已完结和已忽略的刮削记录？未处理任务将保留。"
                  confirm-button-text="清理"
                  cancel-button-text="取消"
                  confirm-button-type="info"
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
                      title="确认忽略此条目？忽略后本轮及后续刮削将不再处理此视频。"
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
        </el-tab-pane>

        <!-- ======================================================== -->
        <!-- Tab 3: ⚙️ 策略与设置（模板命名、风控与定时计划） -->
        <!-- ======================================================== -->
        <el-tab-pane label="⚙️ 策略与设置" name="settings">
          <el-form
            :model="cfg"
            :label-position="isMobile ? 'top' : 'left'"
            :label-width="isMobile ? 'auto' : '175px'"
            class="cfg-form-full"
          >
            <!-- 板块 1：规范命名与路径模板卡片 -->
            <div class="step-card mb16">
              <div class="step-header">
                <div class="step-title-wrap">
                  <span class="step-badge">板块 1</span>
                  <div>
                    <div class="step-title">规范命名与路径模板</div>
                    <div class="step-desc">自定义电影与剧集的文件重命名规则、季集目录层级结构与 Emby / Jellyfin / Infuse 识别标签。</div>
                  </div>
                </div>
              </div>

              <div class="cfg-card-body">
                <el-form-item label="目录拼接 [tmdbid=xxx]">
                  <el-switch v-model="cfg.media_append_tmdb_tag" />
                  <span class="tip-inline">Emby / Jellyfin / Infuse 零误识核心开关（例：<code>狂飙 (2023) [tmdbid=215403]</code>）</span>
                </el-form-item>

                <el-form-item label="电影目标路径模板">
                  <el-input v-model="cfg.media_organize_template_movie" />
                  <div class="tip-block">可用变量：<code>{title} {year} {tmdb_id} {tmdb_tag} {part_tag} {res_tag} {ext}</code></div>
                </el-form-item>

                <el-form-item label="剧集目标路径模板">
                  <el-input v-model="cfg.media_organize_template_tv" />
                  <div class="tip-block">可用变量：<code>{title} {year} {tmdb_tag} {season} {season_00} {episode} {episode_00} {part_tag} {ext}</code></div>
                </el-form-item>

                <el-form-item label="重名冲突策略">
                  <el-radio-group v-model="cfg.media_conflict_policy">
                    <el-radio-button value="skip">安全跳过</el-radio-button>
                    <el-radio-button value="replace_if_better">高画质洗版（旧版移至 _待清理旧版）</el-radio-button>
                    <el-radio-button value="keep_both">保留双版本</el-radio-button>
                  </el-radio-group>
                </el-form-item>

                <el-form-item label="附件与目录净化">
                  <div class="inline-switches">
                    <el-checkbox v-model="cfg.media_move_subtitles">配套字幕自动重命名随动</el-checkbox>
                    <el-checkbox v-model="cfg.media_skip_extras">过滤预告/样片/花絮</el-checkbox>
                    <el-checkbox v-model="cfg.media_clean_empty_dirs">批末清理已搬空的旧子目录</el-checkbox>
                  </div>
                </el-form-item>

                <el-form-item v-if="cfg.media_skip_extras" label="电影最小正片体积">
                  <el-input-number v-model="cfg.media_extra_min_mb" :min="0" :max="2048" :step="10" />
                  <span class="tip-inline">MB（小于该值的非 ISO 电影视作预告样片跳过；剧集不受此限）</span>
                </el-form-item>
              </div>
            </div>

            <!-- 板块 2：刮削精准度与本地元数据卡片 -->
            <div class="step-card mb16">
              <div class="step-header">
                <div class="step-title-wrap">
                  <span class="step-badge">板块 2</span>
                  <div>
                    <div class="step-title">刮削精准度与本地元数据</div>
                    <div class="step-desc">TMDB 智能匹配打分门禁；影视 NFO 与高清封面完全本地隔离存放，绝对不上传污染 115 网盘。</div>
                  </div>
                </div>
              </div>

              <div class="cfg-card-body">
                <el-form-item label="自动锁定置信度阈值">
                  <div class="slider-row">
                    <el-slider v-model="cfg.media_scrape_confidence_threshold" :min="60" :max="98" show-input />
                  </div>
                  <div class="tip-block">≥ 此分数且领先第 2 候选分差 ≥ {{ cfg.media_scrape_margin_threshold }} 分才全自动改名；否则 0 秒挂起交人工确认</div>
                </el-form-item>

                <el-form-item label="一二名领先分差门禁">
                  <el-input-number v-model="cfg.media_scrape_margin_threshold" :min="0" :max="40" />
                  <span class="tip-inline">分（第 1 候选得分比第 2 候选高出此分差时才自动确认，防止同名翻拍影视误伤）</span>
                </el-form-item>

                <el-form-item label="本地元数据与海报">
                  <div class="meta-desc-box">
                    <div class="mdb-head">
                      <el-tag type="success" effect="plain" size="small">✔ 本地默认常驻开启</el-tag>
                      <span class="mdb-title">自动生成标准 .nfo 元数据与 poster / fanart 高清封面</span>
                    </div>
                    <div class="mdb-text">
                      刮削成功后，系统会在本地持久化生成符合 Kodi / Emby / Jellyfin 规范的影视 <code>.nfo</code> 详情文件与高清海报。元数据及图片文件<b>完全存储在本地，绝不上传或污染 115 网盘</b>。
                    </div>
                  </div>
                </el-form-item>

                <el-form-item label="本地元数据存储目录">
                  <div class="meta-desc-box">
                    <div class="mdb-head">
                      <el-tag type="info" effect="plain" size="small">🔒 系统统一锁定</el-tag>
                      <code class="path-code">data/media_meta/</code>
                    </div>
                    <div class="mdb-text">
                      固定存储于容器主数据卷目录 <code>data/media_meta/</code>，不允许自定义修改。此机制确保所有刮削元数据与海报能够随系统整机一键冷备份与数据迁移无损打包，彻底杜绝因自定义外部路径导致备份遗漏或数据脱落！
                    </div>
                  </div>
                </el-form-item>
              </div>
            </div>

            <!-- 板块 3：115 防风控节流与定时计划卡片 -->
            <div class="step-card mb16">
              <div class="step-header">
                <div class="step-title-wrap">
                  <span class="step-badge">板块 3</span>
                  <div>
                    <div class="step-title">115 防风控节流与定时计划</div>
                    <div class="step-desc">操作延时平滑控制避免触发网盘频控；支持后台无人值守定期自动扫描整理。</div>
                  </div>
                </div>
              </div>

              <div class="cfg-card-body">
                <!-- 单次写操作随机延时 -->
                <el-form-item label="单次写操作随机延时">
                  <div class="delay-inputs">
                    <el-input-number v-model="cfg.media_op_delay_min" :min="0.3" :max="20" :step="0.2" :precision="1" />
                    <span class="mx8">至</span>
                    <el-input-number v-model="cfg.media_op_delay_max" :min="0.5" :max="40" :step="0.2" :precision="1" />
                    <span class="tip-inline">秒（如遇 115 限流提示自动按 pace_factor 退避放大）</span>
                  </div>
                </el-form-item>

                <!-- 定时计划调度（参考转存定时计划样式重构） -->
                <div class="schedule-schedule-card">
                  <div class="schedule-card-header">
                    <div class="schedule-card-title">
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

                  <!-- 总开关 -->
                  <div class="config-form-item mb12">
                    <span class="config-label">定时整理：</span>
                    <div class="config-control">
                      <el-switch v-model="cfg.media_organize_schedule_enabled" @change="saveScheduleQuick" />
                      <span class="tip-inline" style="margin-left: 8px">
                        {{ cfg.media_organize_schedule_enabled ? '已开启后台无人值守自动扫描并执行目录整理' : '已关闭（开启后按设定时段或间隔自动排队整理）' }}
                      </span>
                    </div>
                  </div>

                  <div class="config-form-item mb12">
                    <span class="config-label">定时刮削：</span>
                    <div class="config-control">
                      <el-switch v-model="cfg.media_scrape_schedule_enabled" @change="saveScheduleQuick" />
                      <span class="tip-inline" style="margin-left: 8px">
                        {{ cfg.media_scrape_schedule_enabled ? '已开启后台无人值守自动扫描影视并在线刮削' : '已关闭（开启后按设定时段或间隔自动排队刮削）' }}
                      </span>
                    </div>
                  </div>

                  <template v-if="cfg.media_organize_schedule_enabled || cfg.media_scrape_schedule_enabled">
                    <!-- 排队互斥安全说明条 -->
                    <div v-if="cfg.media_organize_schedule_enabled && cfg.media_scrape_schedule_enabled" class="schedule-mutex-note mb12">
                      💡 <b>排队互斥保护生效中</b>：同时开启时，到达执行时间点将严格按顺序<b>先执行目录整理</b>；待整理完成且前序转存/云下载队列完全消化后，再自动启动<b>媒体刮削</b>，绝不并发。
                    </div>
                    <!-- 调度方式选择 -->
                    <div class="config-form-item mb12">
                      <span class="config-label">调度方式：</span>
                      <div class="config-control">
                        <el-tooltip
                          :content="cfg.media_organize_schedule_type === 'interval'
                            ? '当前为【固定间隔轮询】：按设定的间隔时长（如每 12 小时）周期性排队扫描并整理'
                            : '当前为【每天固定时间点】：在每天设定的指定时间点准时排队扫描并整理'"
                          placement="top"
                        >
                          <div class="capsule-toggle">
                            <div
                              class="capsule-seg"
                              :class="{ 'is-active': cfg.media_organize_schedule_type === 'interval' }"
                              @click="changeScheduleType('interval')"
                            >
                              固定间隔轮询
                            </div>
                            <div class="capsule-divider"></div>
                            <div
                              class="capsule-seg"
                              :class="{ 'is-active': cfg.media_organize_schedule_type === 'fixed_times' }"
                              @click="changeScheduleType('fixed_times')"
                            >
                              每天固定时间点
                            </div>
                          </div>
                        </el-tooltip>
                      </div>
                    </div>

                    <!-- 间隔模式配置 -->
                    <div v-if="cfg.media_organize_schedule_type === 'interval'" class="config-form-item mb12">
                      <span class="config-label">间隔时长：</span>
                      <div class="config-control">
                        <el-select v-model="cfg.media_organize_schedule_hours" style="width: 170px" @change="saveScheduleQuick">
                          <el-option label="每 2 小时" :value="2" />
                          <el-option label="每 4 小时" :value="4" />
                          <el-option label="每 6 小时" :value="6" />
                          <el-option label="每 8 小时" :value="8" />
                          <el-option label="每 12 小时" :value="12" />
                          <el-option label="每 24 小时" :value="24" />
                          <el-option label="每 48 小时" :value="48" />
                        </el-select>
                      </div>
                    </div>

                    <!-- 固定时间点配置 -->
                    <div v-else class="fixed-times-layout mb12">
                      <div class="config-form-item">
                        <span class="config-label">已设时段：</span>
                        <div class="fixed-tags-scroll">
                          <el-tag
                            v-for="t in (cfg.media_organize_fixed_times || [])"
                            :key="t"
                            closable
                            type="primary"
                            effect="light"
                            class="fixed-time-tag"
                            @close="removeFixedTime(t)"
                          >
                            {{ t }}
                          </el-tag>
                          <span v-if="!(cfg.media_organize_fixed_times || []).length" class="empty-tag-tip">
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
                        <span>接下来 2 天（48 小时）预计开启时间点：</span>
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
                  </template>
                </div>
              </div>
            </div>

            <!-- 保存控制台操作栏 -->
            <div class="cfg-actions-card">
              <el-button type="primary" size="large" :loading="savingCfg" @click="saveSettings">
                💾 保存策略与设置
              </el-button>
            </div>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 新增/编辑规则弹窗（针对整理与刮削分别定制，完全独立，绝无“参与模块”复选框） -->
    <el-dialog
      v-model="ruleDialog.open"
      :title="ruleDialog.mode === 'organize'
        ? (ruleDialog.id ? '编辑目录整理规则' : '新增目录整理规则')
        : (ruleDialog.id ? '编辑媒体刮削目录' : '新增媒体刮削目录')"
      width="560px"
    >
      <!-- 必选项明确文字提示横幅 -->
      <div class="rule-dialog-hint">
        <span class="rdh-icon">⚠️</span>
        <span>标有 <b class="red">*</b> 的项目为<b>必选项</b>，必须选取有效网盘目录方可保存规则！</span>
      </div>

      <el-form :model="ruleDialog" label-width="110px">
        <el-form-item label="规则名称">
          <el-input
            v-model="ruleDialog.name"
            :placeholder="ruleDialog.mode === 'organize' ? '例如：电影分类整理（留空自动取目录名）' : '例如：电影库刮削（留空自动取目录名）'"
          />
        </el-form-item>

        <!-- 目录整理：源扫描目录（杂乱文件） + 目标整理目录（规范存放） -->
        <template v-if="ruleDialog.mode === 'organize'">
          <el-form-item label="源扫描目录" required>
            <div class="dir-selector-wrap">
              <div v-if="ruleDialog.source_dir" class="dir-selected-box">
                <span class="dir-icon">📁</span>
                <span class="dir-path-text" :title="ruleDialog.source_dir">{{ ruleDialog.source_dir }}</span>
                <el-button size="small" type="primary" plain @click="openDirPicker('source_dir')">重新选取</el-button>
              </div>
              <div v-else class="dir-unselected-box">
                <el-button type="primary" plain @click="openDirPicker('source_dir')">
                  📁 选取网盘源目录
                </el-button>
                <span class="required-warn">（* 必选：必须选取源目录）</span>
              </div>
            </div>
            <div class="tip-block">包含待分类整理杂乱文件的网盘源目录（必须选取）</div>
          </el-form-item>
          <el-form-item label="目标整理目录">
            <div class="dir-selector-wrap">
              <div v-if="ruleDialog.target_dir" class="dir-selected-box">
                <span class="dir-icon">🎯</span>
                <span class="dir-path-text" :title="ruleDialog.target_dir">{{ ruleDialog.target_dir }}</span>
                <div class="dir-btn-group">
                  <el-button size="small" type="primary" plain @click="openDirPicker('target_dir')">重新选取</el-button>
                  <el-button size="small" text type="info" @click="ruleDialog.target_dir = ''">改回源目录整理</el-button>
                </div>
              </div>
              <div v-else class="dir-unselected-box">
                <el-button plain @click="openDirPicker('target_dir')">
                  📁 选取规范存放目录
                </el-button>
                <el-tag type="info" size="small" effect="plain" class="ml8">未选目标：默认在源目录开展整理</el-tag>
              </div>
            </div>
            <div class="tip-block">整理分类后的规范存放目录（如 /媒体库/电影；留空不选则默认在源目录开展整理）</div>
          </el-form-item>

          <el-form-item label="媒体类型约束">
            <el-radio-group v-model="ruleDialog.media_type">
              <el-radio-button value="auto">全部处理（不限类型）</el-radio-button>
              <el-radio-button value="movie">仅电影</el-radio-button>
              <el-radio-button value="tv">仅剧集 / 动漫</el-radio-button>
            </el-radio-group>
            <div class="tip-block rule-type-tip">
              <template v-if="ruleDialog.media_type === 'auto'">
                ℹ️ <b>全部处理（不限类型）</b>：源目录内的所有视频无论电影或剧集均统一按本规则整理。⚠️ 注意：若源目录混杂了电影与剧集且需要自动分流进不同目录，请不要选此项，请分别创建「仅电影」和「仅剧集」两条独立规则！
              </template>
              <template v-else-if="ruleDialog.media_type === 'tv'">
                ✨ <b>仅剧集 / 动漫</b>：精准互斥处理电视剧、动画番剧（有 SxxExx 或集数），扫描时遇到单部电影会自动跳过不碰，完整留给电影规则处理。
              </template>
              <template v-else>
                ✨ <b>仅电影</b>：精准互斥处理独立电影（无分集），扫描时遇到电视剧集会自动跳过不碰，完整留给剧集规则处理。
              </template>
            </div>
          </el-form-item>
        </template>

        <!-- 媒体刮削：只有刮削媒体目录（已整理好的媒体库，无需媒体类型约束） -->
        <template v-else>
          <el-form-item label="刮削媒体目录" required>
            <div class="dir-selector-wrap">
              <div v-if="ruleDialog.target_dir" class="dir-selected-box">
                <span class="dir-icon">🎬</span>
                <span class="dir-path-text" :title="ruleDialog.target_dir">{{ ruleDialog.target_dir }}</span>
                <el-button size="small" type="success" plain @click="openDirPicker('target_dir')">重新选取</el-button>
              </div>
              <div v-else class="dir-unselected-box">
                <el-button type="success" plain @click="openDirPicker('target_dir')">
                  📁 选取媒体库目录
                </el-button>
                <span class="required-warn">（* 必选：必须选取刮削目录）</span>
              </div>
            </div>
            <div class="tip-block">已整理好的影视库目录，程序将联网 TMDB 匹配并生成本地元数据（必须选取）</div>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog.open = false">取消</el-button>
        <el-tooltip
          :content="ruleDialog.mode === 'organize' ? '必须选取源扫描目录方可保存规则' : '必须选取刮削媒体目录方可保存规则'"
          :disabled="isRuleValid"
          placement="top"
        >
          <span>
            <el-button type="primary" :disabled="!isRuleValid" :loading="savingRule" @click="saveRule">
              保存规则
            </el-button>
          </span>
        </el-tooltip>
      </template>
    </el-dialog>

    <!-- 115 目录选择器 -->
    <DirPicker v-model="dirPicker.open" title="选择 115 网盘目录" @pick="onDirPicked" />

    <!-- Dry-Run 预览抽屉 -->
    <el-drawer v-model="previewDrawer.open" title="目录整理 Dry-Run 预览（只读不改盘）" size="72%">
      <div class="mb12">
        共扫描到 <b>{{ previewDrawer.total }}</b> 个视频/原盘项目，其中
        <el-tag type="warning" size="small">{{ previewDrawer.pending_count }} 项需要调整命名/位置</el-tag>
        ，其余已符合规范结构。
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

    <!-- 手动搜索 TMDB 弹窗 -->
    <el-dialog v-model="manualDialog.open" title="手动搜索 TMDB 并暂存绑定" width="640px">
      <div class="mb12 meta-hint">
        待绑定文件：<b>{{ manualDialog.task?.orig_name }}</b>（选定后置为「已选待提交」，需点「提交队列」才执行）
      </div>
      <div class="manual-search-bar mb12">
        <el-input v-model="manualDialog.q" placeholder="输入片名或剧名关键字，按回车搜索..." @keyup.enter="searchManualTmdb" />
        <el-select v-model="manualDialog.kind" style="width:105px">
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
    <el-dialog v-model="metaDialog.open" title="本地持久化 NFO 与海报详情（零 115 上传）" width="760px">
      <div v-if="metaDialog.data" class="meta-view">
        <div class="mb12" style="font-size:12px;color:#64748b">
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, errMsg, fmtTime, toast } from '../api'
import DirPicker from '../components/DirPicker.vue'
import { Timer, Lock, Calendar, Plus } from '@element-plus/icons-vue'

const activeTab = ref('organize')
const taskFilter = ref('pending_group')
const recordFilter = ref('all')

// 移动端自适应与分页栏布局
const isMobile = ref(typeof window !== 'undefined' && window.innerWidth <= 768)
function onResize() {
  isMobile.value = typeof window !== 'undefined' && window.innerWidth <= 768
}
const paginationLayout = computed(() => (isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next'))

// 整理结果分页
const recordPage = ref(1)
const recordPageSize = ref(15)

// 刮削任务分页
const taskPage = ref(1)
const taskPageSize = ref(10)

const status = ref({
  running: false,
  tmdb_configured: false,
  gate: {
    active_mode: 'none',
    gate_state: 'idle',
    exclusive_busy: false,
    organize_running: false,
    scrape_running: false,
    auto_move_running: false,
    drain_progress: {},
    media_progress: {},
  },
  counts: { need_confirm: 0, staged: 0, queued: 0, running: 0, done: 0, organize_rules: 0, scrape_rules: 0, records: 0 },
  last_run: { organize: 0, scrape: 0 },
})

const tasks = ref([])
const rules = ref([])
const rawRecords = ref([])
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

// 分页切片计算
const pagedRecords = computed(() => {
  const start = (recordPage.value - 1) * recordPageSize.value
  return records.value.slice(start, start + recordPageSize.value)
})

const pagedTasks = computed(() => {
  const start = (taskPage.value - 1) * taskPageSize.value
  return tasks.value.slice(start, start + taskPageSize.value)
})

// 筛选切换时重置到第一页
watch(recordFilter, () => {
  recordPage.value = 1
})
const cfg = ref({
  media_append_tmdb_tag: true,
  media_organize_template_movie: '{title} ({year}){tmdb_tag}/{title} ({year}){part_tag}{res_tag}{ext}',
  media_organize_template_tv: '{title} ({year}){tmdb_tag}/Season {season_00}/{title} - S{season_00}E{episode_00}{part_tag}{ext}',
  media_conflict_policy: 'skip',
  media_move_subtitles: true,
  media_skip_extras: true,
  media_extra_min_mb: 50,
  media_clean_empty_dirs: true,
  media_scrape_confidence_threshold: 85,
  media_scrape_margin_threshold: 12,
  media_scrape_write_local_nfo: true,
  media_scrape_download_poster: true,
  media_local_meta_dir: '',
  media_op_delay_min: 0.8,
  media_op_delay_max: 2.0,
  media_organize_schedule_enabled: false,
  media_scrape_schedule_enabled: false,
  media_organize_schedule_type: 'interval',
  media_organize_schedule_hours: 12,
  media_organize_fixed_times: ['06:00', '12:00', '18:00'],
})

const newFixedTime = ref('')
const nextRuns = ref([])

async function saveScheduleQuick() {
  try {
    const { data } = await api.put('/api/media/settings', {
      media_organize_schedule_enabled: cfg.value.media_organize_schedule_enabled,
      media_scrape_schedule_enabled: cfg.value.media_scrape_schedule_enabled,
      media_organize_schedule_type: cfg.value.media_organize_schedule_type,
      media_organize_schedule_hours: cfg.value.media_organize_schedule_hours,
      media_organize_fixed_times: cfg.value.media_organize_fixed_times,
    })
    if (data && data.next_runs) {
      nextRuns.value = data.next_runs
    }
  } catch (e) {
    toast.error(errMsg(e))
  }
}

function changeScheduleType(type) {
  if (cfg.value.media_organize_schedule_type === type) return
  cfg.value.media_organize_schedule_type = type
  if (type === 'fixed_times' && (!cfg.value.media_organize_fixed_times || !cfg.value.media_organize_fixed_times.length)) {
    cfg.value.media_organize_fixed_times = ['06:00', '12:00', '18:00']
    toast.info('已为您预置常用时间点 [06:00, 12:00, 18:00]，可自由增删')
  }
  saveScheduleQuick()
}

function addFixedTime() {
  if (!newFixedTime.value) return
  const t = newFixedTime.value.trim()
  if (!/^([01]?\d|2[0-3]):([0-5]\d)$/.test(t)) {
    toast.warning('时间格式须为 HH:MM（如 06:00）')
    return
  }
  const cur = Array.isArray(cfg.value.media_organize_fixed_times) ? [...cfg.value.media_organize_fixed_times] : []
  if (cur.includes(t)) {
    toast.info('该时间点已存在')
    return
  }
  cur.push(t)
  cur.sort()
  cfg.value.media_organize_fixed_times = cur
  newFixedTime.value = ''
  saveScheduleQuick()
}

function removeFixedTime(t) {
  const cur = (cfg.value.media_organize_fixed_times || []).filter(x => x !== t)
  const isScheduleActive = (cfg.value.media_organize_schedule_enabled || cfg.value.media_scrape_schedule_enabled) && cfg.value.media_organize_schedule_type === 'fixed_times'
  if (isScheduleActive && cur.length === 0) {
    toast.warning('定时整理或定时刮削处于开启状态，固定时间点至少需保留一个！若要清空，请先关闭开关')
    return
  }
  cfg.value.media_organize_fixed_times = cur
  saveScheduleQuick()
}

const startingOrganize = ref(false)
const startingScrape = ref(false)
const stopping = ref(false)
const committing = ref(false)
const previewing = ref(false)
const savingRule = ref(false)
const savingCfg = ref(false)

const organizeRules = computed(() => rules.value.filter(r => !!r.organize_enabled))
const scrapeRules = computed(() => rules.value.filter(r => !!r.scrape_enabled))
const activeOrganizeRules = computed(() => organizeRules.value.filter(r => !!r.enabled))
const activeScrapeRules = computed(() => scrapeRules.value.filter(r => !!r.enabled))

const isBusy = computed(() => !!(status.value.gate.exclusive_busy ?? status.value.gate.busy))
const isMediaRunning = computed(() => ['organize', 'scrape'].includes(status.value.gate.active_mode))
const organizeBtnDisabled = computed(() => !status.value.running || isBusy.value || activeOrganizeRules.value.length === 0)
const scrapeBtnDisabled = computed(() => !status.value.running || isBusy.value || !status.value.tmdb_configured || activeScrapeRules.value.length === 0)
const commitDisabled = computed(() =>
  !status.value.running
  || (status.value.counts.staged || 0) === 0
  || ['organize', 'auto_move'].includes(status.value.gate.active_mode)
)

const organizeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (activeOrganizeRules.value.length === 0) return '暂无启用的整理目录规则，请先点击上方添加'
  if (isBusy.value) return '已有独占整理或刮削任务运行中（严格互斥），请等待结束'
  return ''
})

const scrapeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (!status.value.tmdb_configured) return '请先在「系统设置」配置 TMDB API Key'
  if (activeScrapeRules.value.length === 0) return '暂无启用的刮削目录规则，请先点击上方添加'
  if (isBusy.value) return '已有独占整理或刮削任务运行中（严格互斥），请等待结束'
  return ''
})

const phaseLabel = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') return '正在暂停转存并消化现有队列'
  if (mp.phase === 'scanning') return '正在礼貌扫描 115 目录树'
  return '单队列顺序处理中'
})

const activeProgressLabel = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  const dp = status.value.gate.drain_progress || status.value.gate.drain_status || {}
  if (status.value.gate.gate_state === 'draining' || mp.phase === 'draining') {
    const rem = dp.remaining_total ?? dp.unfinished ?? '?'
    return `排队消化剩余转存（待消化 ${rem} 个，已等 ${dp.elapsed_sec ?? 0}s）`
  }
  return mp.label || '准备中...'
})

const progressPercent = computed(() => {
  const mp = status.value.gate.media_progress || status.value.gate.progress || {}
  if (!mp.total || mp.total <= 0) return 0
  return Math.min(100, Math.round(((mp.current || 0) / mp.total) * 100))
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

function fmtSizeMb(bytes) {
  const n = Number(bytes || 0)
  if (!n || n <= 0) return ''
  return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

function tmdbUrl(kind, tmdbId) {
  const safeKind = kind === 'tv' ? 'tv' : 'movie'
  const idNum = Number(tmdbId)
  if (!Number.isFinite(idNum) || idNum <= 0) return 'https://www.themoviedb.org/'
  return `https://www.themoviedb.org/${safeKind}/${Math.trunc(idNum)}`
}

async function loadStatus() {
  try {
    const { data } = await api.get('/api/media/status')
    status.value = data
  } catch {}
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

async function loadRules() {
  try {
    const { data } = await api.get('/api/media/rules')
    rules.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function loadRecords() {
  try {
    const { data } = await api.get('/api/media/records', { params: { limit: 200 } })
    rawRecords.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function loadSettings() {
  try {
    const { data } = await api.get('/api/media/settings')
    Object.assign(cfg.value, data)
    if (data && data.next_runs) {
      nextRuns.value = data.next_runs
    }
  } catch {}
}

function onTabChange(tab) {
  if (tab === 'organize') {
    loadRules()
    loadRecords()
    loadStatus()
  } else if (tab === 'scrape') {
    loadRules()
    loadTasks()
    loadStatus()
  } else if (tab === 'settings') {
    loadSettings()
    loadStatus()
  }
}

async function runOrganize() {
  startingOrganize.value = true
  try {
    const { data } = await api.post('/api/media/organize/run', {})
    toast.success(data.msg || '目录整理已发起')
    await Promise.all([loadStatus(), loadRecords()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { startingOrganize.value = false }
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

async function stopMedia() {
  stopping.value = true
  try {
    const { data } = await api.post('/api/media/stop')
    toast.info(data.msg)
    await loadStatus()
  } catch (e) { toast.error(errMsg(e)) }
  finally { stopping.value = false }
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

// ---------- 规则弹窗 & 目录选择器 ----------
const ruleDialog = ref({
  open: false, mode: 'organize', id: 0, name: '', source_dir: '', target_dir: '',
  media_type: 'auto', enabled: true,
})
const dirPicker = ref({ open: false, field: 'source_dir' })

function openOrganizeRuleDialog(row = null) {
  if (row) {
    ruleDialog.value = {
      open: true,
      mode: 'organize',
      id: row.id,
      name: row.name || '',
      source_dir: row.source_dir || '',
      target_dir: row.target_dir === row.source_dir ? '' : (row.target_dir || ''),
      media_type: row.media_type || 'auto',
      enabled: !!row.enabled,
    }
  } else {
    ruleDialog.value = {
      open: true,
      mode: 'organize',
      id: 0,
      name: '',
      source_dir: '',
      target_dir: '',
      media_type: 'auto',
      enabled: true,
    }
  }
}

function openScrapeRuleDialog(row = null) {
  if (row) {
    ruleDialog.value = {
      open: true,
      mode: 'scrape',
      id: row.id,
      name: row.name || '',
      source_dir: row.target_dir || row.source_dir || '',
      target_dir: row.target_dir || row.source_dir || '',
      media_type: row.media_type || 'auto',
      enabled: !!row.enabled,
    }
  } else {
    ruleDialog.value = {
      open: true,
      mode: 'scrape',
      id: 0,
      name: '',
      source_dir: '',
      target_dir: '',
      media_type: 'auto',
      enabled: true,
    }
  }
}

function openDirPicker(field) {
  dirPicker.value = { open: true, field }
}

function onDirPicked(pickedPath) {
  ruleDialog.value[dirPicker.value.field] = pickedPath
  if (ruleDialog.value.mode === 'scrape') {
    ruleDialog.value.source_dir = pickedPath
    ruleDialog.value.target_dir = pickedPath
  }
  // 若未填写规则名称，自动填入选取目录名作为友好默认值
  if (!ruleDialog.value.name.trim() && pickedPath) {
    const segs = pickedPath.split('/').filter(Boolean)
    if (segs.length) {
      const last = segs[segs.length - 1]
      ruleDialog.value.name = ruleDialog.value.mode === 'organize' ? `${last}整理` : `${last}刮削`
    }
  }
}

const isRuleValid = computed(() => {
  if (ruleDialog.value.mode === 'organize') {
    return !!(ruleDialog.value.source_dir && ruleDialog.value.source_dir.trim())
  }
  return !!(ruleDialog.value.target_dir && ruleDialog.value.target_dir.trim())
})

async function saveRule() {
  const isOrg = ruleDialog.value.mode === 'organize'
  if (isOrg) {
    if (!ruleDialog.value.source_dir.trim()) {
      toast.error('源扫描目录为必选项，必须先选取网盘源目录')
      return
    }
  } else {
    if (!ruleDialog.value.target_dir.trim()) {
      toast.error('刮削媒体目录为必选项，必须先选取媒体库目录')
      return
    }
  }

  savingRule.value = true
  try {
    const payload = {
      name: (ruleDialog.value.name || '').trim(),
      source_dir: isOrg ? ruleDialog.value.source_dir : ruleDialog.value.target_dir,
      target_dir: isOrg ? ruleDialog.value.target_dir : ruleDialog.value.target_dir,
      media_type: isOrg ? ruleDialog.value.media_type : 'auto',
      organize_enabled: isOrg,
      scrape_enabled: !isOrg,
      enabled: ruleDialog.value.enabled,
    }
    if (ruleDialog.value.id) await api.put(`/api/media/rules/${ruleDialog.value.id}`, payload)
    else await api.post('/api/media/rules', payload)
    ruleDialog.value.open = false
    toast.success('规则保存成功')
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
  finally { savingRule.value = false }
}

async function toggleRuleEnabled(row, val) {
  try {
    await api.put(`/api/media/rules/${row.id}`, {
      ...row,
      organize_enabled: !!row.organize_enabled,
      scrape_enabled: !!row.scrape_enabled,
      enabled: !!val,
    })
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

async function delRule(id) {
  try {
    await api.delete(`/api/media/rules/${id}`)
    toast.success('已删除规则')
    await Promise.all([loadRules(), loadStatus()])
  } catch (e) { toast.error(errMsg(e)) }
}

// ---------- Dry-Run 预览 ----------
const previewDrawer = ref({ open: false, total: 0, pending_count: 0, items: [] })

async function openPreview() {
  previewing.value = true
  try {
    const { data } = await api.post('/api/media/organize/preview', {})
    if (!data.ok) {
      toast.error(data.msg || '预览失败')
      return
    }
    previewDrawer.value = { open: true, total: data.total, pending_count: data.pending_count, items: data.items || [] }
  } catch (e) { toast.error(errMsg(e)) }
  finally { previewing.value = false }
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

const metaDialog = ref({ open: false, taskId: 0, data: null })

async function openMetaModal(task) {
  try {
    const { data } = await api.get('/api/media/meta/inspect', { params: { task_id: task.id } })
    metaDialog.value = { open: true, taskId: task.id, data }
  } catch (e) { toast.error(errMsg(e)) }
}

async function saveSettings() {
  savingCfg.value = true
  try {
    cfg.value.media_scrape_write_local_nfo = true
    cfg.value.media_scrape_download_poster = true
    cfg.value.media_local_meta_dir = ''
    const { data } = await api.put('/api/media/settings', cfg.value)
    if (data && data.next_runs) {
      nextRuns.value = data.next_runs
    }
    toast.success('策略与设置已保存')
  } catch (e) { toast.error(errMsg(e)) }
  finally { savingCfg.value = false }
}

let pollTimer = null
onMounted(async () => {
  onResize()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', onResize)
  }
  await Promise.all([loadStatus(), loadRules(), loadRecords(), loadTasks(), loadSettings()])
  pollTimer = setInterval(async () => {
    await loadStatus()
    if (activeTab.value === 'organize' && (status.value.gate.active_mode === 'organize' || records.value.length === 0)) {
      await loadRecords()
    } else if (activeTab.value === 'scrape') {
      await loadTasks()
    }
  }, 2500)
})

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onResize)
  }
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.mb12 { margin-bottom: 12px; }
.mb16 { margin-bottom: 16px; }
.mb20 { margin-bottom: 20px; }
.mx8 { margin: 0 8px; }

/* ---- 分步卡片容器 ---- */
.step-card {
  border: 1px solid var(--saas-line);
  border-radius: 12px;
  padding: 16px 18px;
  background: var(--subcard-bg);
  transition: border-color 0.2s;
}
.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.step-title-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}
.step-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--grad-brand);
  color: #fff;
  font-size: var(--fs-caption);
  font-weight: 600;
  letter-spacing: .5px;
  flex-shrink: 0;
}
.step-title {
  font-size: var(--fs-title);
  font-weight: 600;
  color: var(--saas-text-main);
}
.step-desc {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
  margin-top: 2px;
}
.step-actions, .step-ops {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.step-meta {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
}

/* ---- 执行中进度条与中断 ---- */
.running-banner {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--glass-bg-strong);
  border: 1px solid var(--saas-line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.rb-left, .rb-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.rb-phase { font-weight: 600; color: var(--saas-text-main); font-size: var(--fs-body); }
.rb-label { color: var(--saas-text-sub); font-size: var(--fs-body); }

/* ---- 规则清单与空提示 ---- */
.active-rules-table { margin-top: 4px; }
.empty-rules-box {
  padding: 20px 14px;
  text-align: center;
  color: var(--saas-text-sub);
  font-size: var(--fs-caption);
  background: var(--glass-bg-strong);
  border-radius: 8px;
  border: 1px dashed var(--saas-line);
}
.text-muted { color: var(--saas-text-sub); opacity: .75; font-style: italic; }

/* ---- 标签页徽章 ---- */
.tab-badge { margin-left: 6px; vertical-align: middle; }

/* ---- 提交控制台 ---- */
.commit-bar {
  display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 12px; background: var(--subcard-bg); border: 1px solid var(--saas-line);
  border-radius: 10px; margin-bottom: 14px;
}
.cb-stats { display: flex; align-items: center; flex-wrap: wrap; }
.cb-stats :deep(.el-radio-group) { display: flex; flex-wrap: wrap; gap: 8px; }
.cb-stats :deep(.el-radio-button__inner) {
  height: 30px; line-height: 28px; padding: 0 14px;
  border-radius: 999px !important;
  border: 1px solid var(--saas-line) !important;
  box-shadow: none !important;
  background: var(--glass-bg-strong);
  color: var(--saas-text-sub);
  font-size: var(--fs-caption);
  font-weight: 500;
  white-space: nowrap;
  transition: all .18s cubic-bezier(0.4, 0, 0.2, 1);
}
.cb-stats :deep(.el-radio-button__inner:hover) {
  border-color: var(--el-color-primary) !important;
  background: rgba(79, 107, 255, .12) !important;
  color: var(--el-color-primary) !important;
}
.cb-stats :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--grad-brand) !important;
  border-color: transparent !important;
  color: #ffffff !important;
  font-weight: 600;
  box-shadow: 0 3px 12px rgba(79, 107, 255, .38) !important;
}
html.dark .cb-stats :deep(.el-radio-button__inner) {
  background: rgba(22, 30, 49, .7);
  color: #9ab0d6;
}
html.dark .cb-stats :deep(.el-radio-button__inner:hover) {
  border-color: rgba(125, 144, 255, .45) !important;
  background: rgba(79, 107, 255, .22) !important;
  color: #d1dcff !important;
}
html.dark .cb-stats :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: #ffffff !important;
}
.cb-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.cb-actions > .el-button + .el-button { margin-left: 0; }

.empty-box { padding: 36px 16px; text-align: center; color: var(--saas-text-sub); font-size: var(--fs-body); }

/* ---- 任务卡片列表 ---- */
.task-list { display: flex; flex-direction: column; gap: 12px; }
.task-card {
  border: 1px solid var(--saas-line); border-radius: 10px; padding: 12px 14px;
  background: var(--subcard-bg); transition: border-color 0.2s;
}
.task-card.is-staged { border-color: rgba(245, 158, 11, .55); background: rgba(245, 158, 11, .07); }
.task-card.is-queued { border-color: rgba(79, 107, 255, .55); }
.tc-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.tc-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.tc-id { font-family: var(--font-family-mono); color: var(--saas-text-sub); font-size: var(--fs-caption); }
.tc-orig { font-weight: 600; color: var(--saas-text-main); font-size: var(--fs-body); word-break: break-all; }
.tc-ops { display: flex; gap: 6px; flex-wrap: wrap; }
.tc-meta { margin-top: 6px; font-size: var(--fs-caption); color: var(--saas-text-sub); line-height: 1.5; word-break: break-all; }
.tc-err { color: var(--el-color-danger); }
.group-toggle-btn { margin-left: 8px; vertical-align: baseline; }
.group-files-box {
  margin-top: 8px; padding: 8px 10px; border-radius: 8px;
  background: var(--glass-bg-strong); border: 1px solid var(--saas-line);
  display: flex; flex-direction: column; gap: 4px;
}
.gf-row { display: flex; align-items: center; gap: 8px; font-size: var(--fs-caption); color: var(--saas-text-main); }
.gf-idx { color: var(--saas-text-sub); font-family: var(--font-family-mono); min-width: 20px; }
.gf-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gf-size { color: var(--saas-text-sub); font-size: var(--fs-caption); flex-shrink: 0; }
.staged-banner {
  margin-top: 8px; padding: 6px 10px; border-radius: 8px;
  background: rgba(245, 158, 11, .14); border: 1px solid rgba(245, 158, 11, .3);
  color: #b45309; font-size: var(--fs-caption);
}
html.dark .staged-banner { color: #fbbf24; }

/* ---- TMDB 候选卡片 ---- */
.cand-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(275px, 1fr)); gap: 10px; margin-top: 10px; }
.cand-item {
  display: flex; gap: 10px; padding: 9px; border: 1px solid var(--saas-line); border-radius: 10px;
  cursor: pointer; transition: all 0.15s; background: var(--glass-bg-strong); align-items: flex-start;
}
.cand-item:hover { border-color: var(--el-color-primary); box-shadow: 0 2px 10px rgba(79, 107, 255, .18); }
.cand-item.selected { border: 2px solid #f59e0b; background: rgba(245, 158, 11, .08); }
.cand-poster { width: 48px; height: 72px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: var(--saas-hover); }
.cand-no-poster { display: flex; align-items: center; justify-content: center; font-size: var(--fs-caption); color: var(--saas-text-sub); }
.cand-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.cand-top { display: flex; align-items: center; gap: 5px; }
.cand-name { font-weight: 600; font-size: var(--fs-body); color: var(--saas-text-main); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cand-yr { font-size: var(--fs-caption); color: var(--saas-text-sub); flex-shrink: 0; }
.cand-rating { font-size: var(--fs-caption); color: #d97706; font-weight: 600; flex-shrink: 0; }
html.dark .cand-rating { color: #fbbf24; }
.cand-orig-title { font-size: var(--fs-caption); color: var(--saas-text-sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cand-sub { display: flex; justify-content: space-between; align-items: center; font-size: var(--fs-caption); color: var(--saas-text-sub); }
.tmdb-link { color: var(--el-color-primary); text-decoration: none; font-family: var(--font-family-mono); }
.tmdb-link:hover { text-decoration: underline; }
.cand-score { font-weight: 600; }
.cand-score.high { color: var(--el-color-success); }
.cand-over {
  font-size: var(--fs-caption); color: var(--saas-text-sub); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.cand-reasons { font-size: var(--fs-caption); color: var(--saas-text-sub); opacity: .8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.no-cand-tip { margin-top: 8px; font-size: var(--fs-caption); color: #b45309; }
html.dark .no-cand-tip { color: #fbbf24; }

/* ---- 规则 / 账本工具条 ---- */
.rules-bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.rules-hint { font-size: var(--fs-caption); color: var(--saas-text-sub); }
.picker-row { display: flex; gap: 8px; width: 100%; }

/* ---- 弹窗目录选取器展示卡片 ---- */
.rule-dialog-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 16px;
  font-size: var(--fs-caption);
  color: #d97706;
  line-height: 1.4;
}
html.dark .rule-dialog-hint {
  background: rgba(245, 158, 11, 0.15);
  border-color: rgba(245, 158, 11, 0.35);
  color: #fbbf24;
}
.rdh-icon { font-size: 16px; flex-shrink: 0; }
.red { color: var(--el-color-danger, #ef4444); font-weight: 700; }
.required-warn {
  font-size: var(--fs-caption);
  color: var(--el-color-danger, #ef4444);
  font-weight: 500;
}
.rule-type-tip {
  margin-top: 8px;
  line-height: 1.5;
  background: var(--glass-bg-strong);
  border: 1px solid var(--saas-line);
  border-radius: 6px;
  padding: 8px 10px;
}

.dir-selector-wrap { width: 100%; }
.dir-selected-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--glass-bg-strong);
  border: 1px solid var(--saas-line);
  border-radius: 8px;
  padding: 6px 12px;
  width: 100%;
  min-height: 40px;
}
.dir-icon { font-size: 16px; flex-shrink: 0; }
.dir-path-text {
  flex: 1;
  min-width: 0;
  font-family: var(--font-family-mono);
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--saas-text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-btn-group { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.dir-unselected-box { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ml8 { margin-left: 8px; }

/* ---- 策略配置全宽卡片 ---- */
.cfg-form-full { width: 100%; }
.cfg-form-full :deep(.el-form-item) {
  margin-bottom: 18px;
}
.cfg-form-full :deep(.el-form-item__label) {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--saas-text-main);
  line-height: 1.4;
  height: auto;
  margin-bottom: 7px;
  padding-right: 0;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
}
.cfg-form-full :deep(.el-form-item__label)::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary, #4f6bff);
  margin-right: 7px;
  flex-shrink: 0;
  box-shadow: 0 0 0 2px rgba(79, 107, 255, 0.20);
}
.cfg-card-body { padding-top: 6px; }
.tip-inline { margin-left: 10px; font-size: var(--fs-caption); color: var(--saas-text-sub); }
.tip-block { font-size: var(--fs-caption); color: var(--saas-text-sub); opacity: .85; margin-top: 4px; line-height: 1.4; }
.inline-switches { display: flex; gap: 18px; flex-wrap: wrap; }

.slider-row { width: 100%; max-width: 480px; }
.delay-inputs, .schedule-inputs { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }

/* 本地元数据说明展示卡片 */
.meta-desc-box {
  background: var(--glass-bg-strong);
  border: 1px solid var(--saas-line);
  border-radius: 8px;
  padding: 12px 14px;
  width: 100%;
}
.mdb-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.mdb-title {
  font-weight: 600;
  font-size: var(--fs-caption);
  color: var(--saas-text-main);
}
.path-code {
  font-family: var(--font-family-mono);
  font-size: var(--fs-caption);
  font-weight: 700;
  color: var(--el-color-primary);
  background: rgba(99, 102, 241, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}
.mdb-text {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
  line-height: 1.5;
}

/* 底部保存卡片条 */
.cfg-actions-card {
  display: flex;
  justify-content: flex-end;
  padding: 8px 0 16px;
}

/* ---- 手动搜索弹窗 ---- */
.manual-search-bar { display: flex; gap: 8px; }
.meta-hint { font-size: var(--fs-caption); color: var(--saas-text-sub); }
.manual-results { max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.mr-row {
  display: flex; align-items: center; gap: 10px; padding: 8px;
  border: 1px solid var(--saas-line); border-radius: 10px; background: var(--glass-bg-strong);
}
.mr-info { flex: 1; min-width: 0; }
.mr-over {
  font-size: var(--fs-caption); color: var(--saas-text-sub); margin-top: 4px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

/* ---- 本地元数据弹窗 ---- */
.meta-imgs { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.meta-poster { height: 150px; border-radius: 8px; }
.meta-fanart { height: 150px; border-radius: 8px; max-width: 320px; object-fit: cover; }
.nfo-code {
  background: var(--glass-bg-strong); border: 1px solid var(--saas-line);
  padding: 10px; border-radius: 8px; font-family: var(--font-family-mono);
  font-size: var(--fs-caption); overflow-x: auto; max-height: 300px;
}

/* ---- 统一分页栏 ---- */
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--saas-line);
}

/* ---- 小窗口 / 移动端自适应 ---- */
@media (max-width: 768px) {
  .step-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .step-actions, .step-ops {
    width: 100%;
    justify-content: flex-start;
  }
  .commit-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .cb-stats, .cb-actions { width: 100%; }
  .cb-stats :deep(.el-radio-group) { gap: 6px; }
  .cb-stats :deep(.el-radio-button__inner) { padding: 0 10px; }
  .cb-actions { flex-direction: column; align-items: stretch; }
  .cb-actions .el-button {
    width: 100%;
    margin-left: 0;
    flex: none;
    white-space: normal;
    word-break: keep-all;
  }
  .pagination-bar {
    justify-content: center;
    padding: 10px 0;
  }
  .pagination-bar :deep(.el-pagination) {
    justify-content: center;
    flex-wrap: wrap;
    gap: 4px;
  }
  .cfg-actions-card {
    justify-content: stretch;
  }
  .cfg-actions-card .el-button {
    width: 100%;
  }
  .slider-row {
    max-width: 100%;
  }
  .delay-inputs, .schedule-inputs {
    width: 100%;
  }
  .delay-inputs .el-input-number, .schedule-inputs .el-input-number {
    width: 100%;
  }
  .cfg-form-full :deep(.el-radio-group) {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    width: 100%;
  }
  .cfg-form-full :deep(.el-radio-button) {
    width: 100%;
  }
  .cfg-form-full :deep(.el-radio-button__inner) {
    width: 100%;
  }
  .cfg-form-full :deep(.el-form-item) {
    margin-bottom: 18px;
  }
  .dir-selected-box {
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 10px;
  }
  .dir-path-text {
    width: 100%;
    white-space: normal;
    word-break: break-all;
  }
  .dir-btn-group {
    width: 100%;
    justify-content: flex-end;
  }
  .capsule-toggle {
    width: 100%;
  }
}

/* 定时计划调度卡片（参考 Dirs.vue 统一规范） */
.schedule-schedule-card {
  background: var(--el-bg-color, #ffffff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 14px 16px;
  margin-top: 10px;
}
.schedule-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.schedule-card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
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
  padding: 6px 10px;
  margin-top: 4px;
  margin-bottom: 12px;
  line-height: 1.4;
}
.schedule-mutex-note {
  font-size: 11.5px;
  color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 6px;
  padding: 6px 10px;
  line-height: 1.45;
}
html.dark .schedule-mutex-note {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.12);
  border-color: rgba(59, 130, 246, 0.28);
}
.config-form-item {
  display: flex;
  align-items: center;
  gap: 10px;
}
.config-label {
  font-size: 13px;
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
.capsule-seg {
  flex: 1;
  height: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--saas-text-main, #303133);
  white-space: nowrap;
  transition: all .25s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}
html.dark .capsule-seg {
  color: #b7c4de;
}
.capsule-seg:not(.is-active):hover {
  background: rgba(79, 107, 255, 0.14);
  color: var(--el-color-primary);
}
html.dark .capsule-seg:not(.is-active):hover {
  background: rgba(79, 107, 255, 0.22);
  color: #fff;
}
.capsule-seg.is-active {
  background: var(--grad-brand, linear-gradient(135deg, #4f6bff 0%, #704fff 100%));
  color: #ffffff !important;
  font-weight: 600;
}
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
.prediction-box {
  background: var(--el-bg-color, #ffffff);
  border: 1px dashed rgba(64, 158, 255, 0.35);
  border-radius: 8px;
  padding: 10px 12px;
  margin-top: 10px;
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
</style>
