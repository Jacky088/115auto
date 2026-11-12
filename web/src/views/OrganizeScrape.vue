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

    <!-- 实时进度与互斥安全中断条 -->
    <div v-if="isMediaRunning" class="card progress-banner mb16">
      <div class="pb-left">
        <el-tag :type="status.gate.active_mode === 'organize' ? 'primary' : 'success'" effect="dark" size="small">
          {{ status.gate.active_mode === 'organize' ? '目录整理执行中' : '媒体刮削执行中' }}
        </el-tag>
        <span class="pb-phase">{{ phaseLabel }}</span>
        <span class="pb-label">{{ activeProgressLabel }}</span>
      </div>
      <div class="pb-right">
        <el-progress
          v-if="progressPercent > 0"
          :percentage="progressPercent"
          :stroke-width="10"
          style="width: 180px"
        />
        <el-button type="danger" size="small" plain :loading="stopping" @click="stopMedia">
          ⏹ 安全中断（处理完当前文件后停止）
        </el-button>
      </div>
    </div>

    <!-- 双模块独立控制区 -->
    <div class="module-grid mb16">
      <div class="card module-card">
        <div class="mc-head">
          <div class="mc-title-wrap">
            <span class="mc-bar mc-bar-blue" />
            <div>
              <div class="mc-title">📁 目录整理（规范目录与命名）</div>
              <div class="mc-sub">按电影 / 剧集标准层级结构重命名、配套字幕随动与原盘保护（独立运行，单线程顺序防风控）</div>
            </div>
          </div>
          <el-tag size="small" type="info" effect="plain">启用规则 {{ status.counts.organize_rules || 0 }} 条</el-tag>
        </div>
        <div class="mc-foot">
          <span class="mc-meta">
            上次运行：{{ status.last_run.organize ? fmtTime(status.last_run.organize) : '尚未执行' }}
          </span>
          <div class="mc-actions">
            <el-button size="default" :disabled="isBusy" :loading="previewing" @click="openPreview">
              预览效果 (Dry-Run)
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
                  ▶ 运行目录整理
                </el-button>
              </span>
            </el-tooltip>
          </div>
        </div>
      </div>

      <div class="card module-card">
        <div class="mc-head">
          <div class="mc-title-wrap">
            <span class="mc-bar mc-bar-purple" />
            <div>
              <div class="mc-title">🎬 媒体刮削（TMDB 核验 + [tmdbid] + 本地元数据）</div>
              <div class="mc-sub">多阶段召回加权打分；模糊条目 0 秒跳过并加入网页「待确认列表」，TG 仅推送纯文本结果；.nfo/海报仅存本地零 115 上传</div>
            </div>
          </div>
          <el-tag v-if="status.tmdb_configured" size="small" type="success" effect="plain">TMDB 已就绪</el-tag>
          <el-button v-else link type="warning" size="small" @click="$router.push('/settings')">去设置 TMDB Key →</el-button>
        </div>
        <div class="mc-foot">
          <span class="mc-meta">
            待确认 {{ status.counts.need_confirm || 0 }} · 已选待提交 {{ status.counts.staged || 0 }}
            <template v-if="status.last_run.scrape"> · 上次：{{ fmtTime(status.last_run.scrape) }}</template>
          </span>
          <div class="mc-actions">
            <el-tooltip :content="scrapeBtnTip" :disabled="!scrapeBtnDisabled" placement="top">
              <span>
                <el-button
                  type="success"
                  size="default"
                  :disabled="scrapeBtnDisabled"
                  :loading="startingScrape || status.gate.scrape_running"
                  @click="runScrape"
                >
                  ▶ 运行媒体刮削
                </el-button>
              </span>
            </el-tooltip>
          </div>
        </div>
      </div>
    </div>

    <!-- 主工作台标签页 -->
    <div class="card">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- Tab 1: 刮削核对与待确认队列（网页统一选片并提交队列） -->
        <el-tab-pane name="tasks">
          <template #label>
            <span>刮削核对与待确认</span>
            <el-badge
              v-if="(status.counts.need_confirm + status.counts.staged) > 0"
              :value="status.counts.need_confirm + status.counts.staged"
              class="tab-badge"
            />
          </template>

          <!-- 统一暂存 & 提交控制台（始终置顶） -->
          <div class="commit-bar">
            <div class="cb-stats">
              <el-radio-group v-model="taskFilter" size="small" @change="loadTasks">
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
              <el-button size="small" @click="clearFinishedTasks">清理已完结</el-button>
              <el-tooltip
                :content="!status.running ? '顶栏系统开关已停止，暂不允许提交执行' : ((status.counts.staged||0)===0 ? '请先在下方至少点选一部影视（状态变为「已选待提交」），再点击此按钮统一入队执行' : '当前有互斥整理任务正在执行，请稍候')"
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
            当前筛选条件下暂无刮削或整理任务记录。新增目录规则并点击上方按钮即可开始扫描。
          </div>

          <div v-else class="task-list">
            <div
              v-for="t in tasks"
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
                  <el-button
                    v-if="t.status === 'need_confirm' || t.status === 'staged'"
                    size="small"
                    plain
                    @click="ignoreTask(t)"
                  >
                    忽略
                  </el-button>
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
        </el-tab-pane>

        <!-- Tab 2: 目录规则配置 -->
        <el-tab-pane label="目录规则与监控路径" name="rules">
          <div class="rules-bar mb12">
            <span class="rules-hint">配置待整理 / 刮削的网盘目录。源与目标相同即原地重命名建季夹；建议指向与「转存目录」不冲突的路径。</span>
            <el-button type="primary" size="small" @click="openRuleDialog()">+ 新增目录规则</el-button>
          </div>
          <el-table :data="rules" stripe size="small">
            <el-table-column prop="name" label="规则名称" min-width="110" />
            <el-table-column prop="source_dir" label="源扫描目录" min-width="170" />
            <el-table-column prop="target_dir" label="目标整理目录" min-width="170">
              <template #default="{ row }">{{ row.target_dir || row.source_dir }}</template>
            </el-table-column>
            <el-table-column label="媒体类型" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.media_type === 'movie' ? '电影' : (row.media_type === 'tv' ? '剧集' : '自动') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="目录整理" width="85" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.organize_enabled ? 'primary' : 'info'">{{ row.organize_enabled ? '参与' : '跳过' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="媒体刮削" width="85" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.scrape_enabled ? 'success' : 'info'">{{ row.scrape_enabled ? '参与' : '跳过' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="75" align="center">
              <template #default="{ row }">
                <el-switch :model-value="!!row.enabled" size="small" @change="(v) => toggleRuleEnabled(row, v)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRuleDialog(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="delRule(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 3: 操作账本与撤销 -->
        <el-tab-pane label="操作账本与安全撤销" name="records">
          <div class="rules-bar mb12">
            <span class="rules-hint">每笔 115 重命名与移动（含配套字幕）均留痕记录，支持单条或整批无损恢复原目录与原文件名。</span>
            <el-button size="small" @click="loadRecords">🔄 刷新账本</el-button>
          </div>
          <el-table :data="records" stripe size="small">
            <el-table-column prop="id" label="#" width="60" />
            <el-table-column prop="orig_name" label="原文件名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="dest_name" label="整理后文件名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="dest_path" label="目标目录" min-width="180" show-overflow-tooltip />
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
                  <el-button link type="warning" size="small" @click="undoOne(row.id)">撤销此条</el-button>
                  <el-button link type="danger" size="small" @click="undoBatch(row.batch_id)">撤销同批</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 4: 命名模板与防风控策略 -->
        <el-tab-pane label="命名规范与防风控策略" name="settings">
          <el-form :model="cfg" label-width="175px" class="cfg-form">
            <div class="cfg-section-title">规范命名与路径模板</div>
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

            <div class="cfg-section-title">刮削精准度与本地元数据</div>
            <el-form-item label="自动锁定置信度阈值">
              <el-slider v-model="cfg.media_scrape_confidence_threshold" :min="60" :max="98" show-input style="max-width:440px" />
              <div class="tip-block">≥ 此分数且领先第 2 候选分差 ≥ {{ cfg.media_scrape_margin_threshold }} 分才全自动改名；否则 0 秒挂起交人工确认</div>
            </el-form-item>
            <el-form-item label="一二名领先分差门禁">
              <el-input-number v-model="cfg.media_scrape_margin_threshold" :min="0" :max="40" />
            </el-form-item>
            <el-form-item label="本地元数据生成">
              <div class="inline-switches">
                <el-checkbox v-model="cfg.media_scrape_write_local_nfo">本地写入 .nfo (绝不上传 115)</el-checkbox>
                <el-checkbox v-model="cfg.media_scrape_download_poster">本地保存 poster / fanart</el-checkbox>
              </div>
            </el-form-item>
            <el-form-item label="本地元数据存储目录">
              <el-input v-model="cfg.media_local_meta_dir" placeholder="留空默认存于 data/media_meta/" />
            </el-form-item>

            <div class="cfg-section-title">115 防风控节流与定时计划</div>
            <el-form-item label="单次写操作随机延时">
              <el-input-number v-model="cfg.media_op_delay_min" :min="0.3" :max="20" :step="0.2" :precision="1" />
              <span class="mx8">至</span>
              <el-input-number v-model="cfg.media_op_delay_max" :min="0.5" :max="40" :step="0.2" :precision="1" />
              <span class="tip-inline">秒（如遇 115 限流提示自动按 pace_factor 退避放大）</span>
            </el-form-item>
            <el-form-item label="定期自动目录整理">
              <el-switch v-model="cfg.media_organize_schedule_enabled" />
              <span class="mx8">每</span>
              <el-input-number v-model="cfg.media_organize_schedule_hours" :min="1" :max="168" :disabled="!cfg.media_organize_schedule_enabled" />
              <span class="tip-inline">小时检查一次（仅在系统运行且门禁空闲时自动触发）</span>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="savingCfg" @click="saveSettings">💾 保存策略设置</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 新增/编辑规则弹窗 -->
    <el-dialog v-model="ruleDialog.open" :title="ruleDialog.id ? '编辑目录规则' : '新增目录规则'" width="520px">
      <el-form :model="ruleDialog" label-width="110px">
        <el-form-item label="规则名称">
          <el-input v-model="ruleDialog.name" placeholder="例如：电影入库整理（留空自动取目录名）" />
        </el-form-item>
        <el-form-item label="源扫描目录" required>
          <div class="picker-row">
            <el-input v-model="ruleDialog.source_dir" placeholder="/电影" />
            <el-button @click="openDirPicker('source_dir')">选网盘目录</el-button>
          </div>
        </el-form-item>
        <el-form-item label="目标规范目录">
          <div class="picker-row">
            <el-input v-model="ruleDialog.target_dir" placeholder="留空表示原地重命名并建规范子目录" />
            <el-button @click="openDirPicker('target_dir')">选网盘目录</el-button>
          </div>
        </el-form-item>
        <el-form-item label="媒体类型约束">
          <el-radio-group v-model="ruleDialog.media_type">
            <el-radio-button value="auto">智能判断</el-radio-button>
            <el-radio-button value="movie">电影</el-radio-button>
            <el-radio-button value="tv">剧集 / 动漫</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="参与模块">
          <el-checkbox v-model="ruleDialog.organize_enabled">参与「目录整理」</el-checkbox>
          <el-checkbox v-model="ruleDialog.scrape_enabled">参与「媒体刮削」</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog.open = false">取消</el-button>
        <el-button type="primary" :loading="savingRule" @click="saveRule">保存规则</el-button>
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
      <div class="mb12" style="color:#64748b;font-size:13px">
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
              <span v-if="r.year" style="color:#64748b">({{ r.year }})</span>
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
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, errMsg, fmtTime, toast } from '../api'
import DirPicker from '../components/DirPicker.vue'

const activeTab = ref('tasks')
const taskFilter = ref('pending_group')

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
  counts: { need_confirm: 0, staged: 0, queued: 0, running: 0, done: 0, organize_rules: 0, scrape_rules: 0 },
  last_run: { organize: 0, scrape: 0 },
})

const tasks = ref([])
const rules = ref([])
const records = ref([])
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
  media_organize_schedule_hours: 12,
})

const startingOrganize = ref(false)
const startingScrape = ref(false)
const stopping = ref(false)
const committing = ref(false)
const previewing = ref(false)
const savingRule = ref(false)
const savingCfg = ref(false)

const isBusy = computed(() => !!(status.value.gate.exclusive_busy ?? status.value.gate.busy))
const isMediaRunning = computed(() => ['organize', 'scrape'].includes(status.value.gate.active_mode))
const organizeBtnDisabled = computed(() => !status.value.running || isBusy.value)
const scrapeBtnDisabled = computed(() => !status.value.running || isBusy.value || !status.value.tmdb_configured)
const commitDisabled = computed(() =>
  !status.value.running
  || (status.value.counts.staged || 0) === 0
  || ['organize', 'auto_move'].includes(status.value.gate.active_mode)
)

const organizeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (isBusy.value) return '已有独占整理或刮削任务运行中（严格互斥），请等待结束'
  return ''
})

const scrapeBtnTip = computed(() => {
  if (!status.value.running) return '顶栏系统处于停止状态，请先启动系统'
  if (!status.value.tmdb_configured) return '请先在「系统设置」配置 TMDB API Key'
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

async function loadRules() {
  try {
    const { data } = await api.get('/api/media/rules')
    rules.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function loadRecords() {
  try {
    const { data } = await api.get('/api/media/records', { params: { limit: 150 } })
    records.value = data
  } catch (e) { toast.error(errMsg(e)) }
}

async function loadSettings() {
  try {
    const { data } = await api.get('/api/media/settings')
    Object.assign(cfg.value, data)
  } catch {}
}

function onTabChange(tab) {
  if (tab === 'tasks') loadTasks()
  else if (tab === 'rules') loadRules()
  else if (tab === 'records') loadRecords()
  else if (tab === 'settings') loadSettings()
}

async function runOrganize() {
  startingOrganize.value = true
  try {
    const { data } = await api.post('/api/media/organize/run', {})
    toast.success(data.msg || '目录整理已发起')
    await loadStatus()
  } catch (e) { toast.error(errMsg(e)) }
  finally { startingOrganize.value = false }
}

async function runScrape() {
  startingScrape.value = true
  try {
    const { data } = await api.post('/api/media/scrape/run', {})
    toast.success(data.msg || '媒体刮削已发起')
    await loadStatus()
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
  open: false, id: 0, name: '', source_dir: '', target_dir: '',
  media_type: 'auto', organize_enabled: true, scrape_enabled: true, enabled: true,
})
const dirPicker = ref({ open: false, field: 'source_dir' })

function openRuleDialog(row = null) {
  if (row) {
    ruleDialog.value = {
      open: true,
      id: row.id,
      name: row.name || '',
      source_dir: row.source_dir || '',
      target_dir: row.target_dir === row.source_dir ? '' : (row.target_dir || ''),
      media_type: row.media_type || 'auto',
      organize_enabled: !!row.organize_enabled,
      scrape_enabled: !!row.scrape_enabled,
      enabled: !!row.enabled,
    }
  } else {
    ruleDialog.value = {
      open: true, id: 0, name: '', source_dir: '', target_dir: '',
      media_type: 'auto', organize_enabled: true, scrape_enabled: true, enabled: true,
    }
  }
}

function openDirPicker(field) {
  dirPicker.value = { open: true, field }
}

function onDirPicked(pickedPath) {
  ruleDialog.value[dirPicker.value.field] = pickedPath
}

async function saveRule() {
  if (!ruleDialog.value.source_dir.trim()) {
    toast.error('请先选择或填写源扫描目录')
    return
  }
  savingRule.value = true
  try {
    const payload = { ...ruleDialog.value }
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
    await api.put('/api/media/settings', cfg.value)
    toast.success('策略设置已保存')
  } catch (e) { toast.error(errMsg(e)) }
  finally { savingCfg.value = false }
}

let pollTimer = null
onMounted(async () => {
  await Promise.all([loadStatus(), loadTasks(), loadRules()])
  pollTimer = setInterval(async () => {
    await loadStatus()
    if (activeTab.value === 'tasks') await loadTasks()
    else if (activeTab.value === 'records' && isMediaRunning.value) await loadRecords()
  }, 2500)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.mb12 { margin-bottom: 12px; }
.mb16 { margin-bottom: 16px; }
.mx8 { margin: 0 8px; }

.progress-banner {
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  border-left: 4px solid #6366f1;
}
.pb-left, .pb-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pb-phase { font-weight: 600; color: #334155; font-size: 13px; }
.pb-label { color: #64748b; font-size: 13px; }

.module-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }
.module-card { display: flex; flex-direction: column; justify-content: space-between; gap: 14px; }
.mc-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.mc-title-wrap { display: flex; gap: 10px; align-items: flex-start; }
.mc-bar { width: 4px; height: 22px; border-radius: 2px; margin-top: 2px; flex-shrink: 0; }
.mc-bar-blue { background: #3b82f6; }
.mc-bar-purple { background: #6366f1; }
.mc-title { font-size: 15px; font-weight: 600; color: #1e293b; }
.mc-sub { font-size: 12.5px; color: #64748b; margin-top: 4px; line-height: 1.45; }
.mc-foot { display: flex; justify-content: space-between; align-items: center; padding-top: 10px; border-top: 1px dashed #e2e8f0; gap: 8px; flex-wrap: wrap; }
.mc-meta { font-size: 12.5px; color: #64748b; }
.mc-actions { display: flex; gap: 8px; }

.tab-badge { margin-left: 6px; vertical-align: middle; }
.commit-bar {
  display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 12px; background: #f8fafc; border-radius: 8px; margin-bottom: 14px;
}
.cb-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

.empty-box { padding: 36px 16px; text-align: center; color: #94a3b8; font-size: 13.5px; }

.task-list { display: flex; flex-direction: column; gap: 12px; }
.task-card {
  border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; background: #fff;
  transition: border-color 0.2s;
}
.task-card.is-staged { border-color: #f59e0b; background: #fffbeb44; }
.task-card.is-queued { border-color: #3b82f6; }
.tc-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.tc-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.tc-id { font-family: monospace; color: #64748b; font-size: 12.5px; }
.tc-orig { font-weight: 600; color: #1e293b; font-size: 14px; word-break: break-all; }
.tc-ops { display: flex; gap: 6px; flex-wrap: wrap; }
.tc-meta { margin-top: 6px; font-size: 12.5px; color: #64748b; line-height: 1.5; word-break: break-all; }
.tc-err { color: #dc2626; }
.group-toggle-btn { margin-left: 8px; vertical-align: baseline; }
.group-files-box {
  margin-top: 8px; padding: 8px 10px; border-radius: 6px;
  background: #f8fafc; border: 1px solid #e2e8f0; display: flex; flex-direction: column; gap: 4px;
}
.gf-row { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #334155; }
.gf-idx { color: #94a3b8; font-family: monospace; min-width: 20px; }
.gf-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gf-size { color: #64748b; font-size: 11.5px; flex-shrink: 0; }
.staged-banner {
  margin-top: 8px; padding: 6px 10px; border-radius: 6px;
  background: #fef3c7; color: #92400e; font-size: 12.5px;
}

.cand-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(275px, 1fr)); gap: 10px; margin-top: 10px; }
.cand-item {
  display: flex; gap: 10px; padding: 9px; border: 1px solid #e2e8f0; border-radius: 8px;
  cursor: pointer; transition: all 0.15s; background: #fff; align-items: flex-start;
}
.cand-item:hover { border-color: #6366f1; box-shadow: 0 2px 8px rgba(99, 102, 241, 0.12); }
.cand-item.selected { border: 2px solid #f59e0b; background: #fffbeb; }
.cand-poster { width: 48px; height: 72px; object-fit: cover; border-radius: 4px; flex-shrink: 0; background: #f1f5f9; }
.cand-no-poster { display: flex; align-items: center; justify-content: center; font-size: 11px; color: #94a3b8; }
.cand-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.cand-top { display: flex; align-items: center; gap: 5px; }
.cand-name { font-weight: 600; font-size: 13px; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cand-yr { font-size: 12px; color: #64748b; flex-shrink: 0; }
.cand-rating { font-size: 11.5px; color: #d97706; font-weight: 600; flex-shrink: 0; }
.cand-orig-title { font-size: 11.5px; color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cand-sub { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #64748b; }
.tmdb-link { color: #4f46e5; text-decoration: none; font-family: monospace; }
.tmdb-link:hover { text-decoration: underline; }
.cand-score { font-weight: 600; }
.cand-score.high { color: #16a34a; }
.cand-over {
  font-size: 11.5px; color: #475569; line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.cand-reasons { font-size: 11px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.no-cand-tip { margin-top: 8px; font-size: 12.5px; color: #b45309; }

.rules-bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.rules-hint { font-size: 12.5px; color: #64748b; }
.picker-row { display: flex; gap: 8px; width: 100%; }

.cfg-form { max-width: 780px; }
.cfg-section-title { font-weight: 600; font-size: 14px; color: #1e293b; margin: 16px 0 12px; padding-left: 8px; border-left: 3px solid #6366f1; }
.tip-inline { margin-left: 10px; font-size: 12.5px; color: #64748b; }
.tip-block { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.inline-switches { display: flex; gap: 18px; flex-wrap: wrap; }

.manual-search-bar { display: flex; gap: 8px; }
.manual-results { max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.mr-row { display: flex; align-items: center; gap: 10px; padding: 8px; border: 1px solid #e2e8f0; border-radius: 8px; }
.mr-info { flex: 1; min-width: 0; }
.mr-over { font-size: 12px; color: #64748b; margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.meta-imgs { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.meta-poster { height: 150px; border-radius: 6px; }
.meta-fanart { height: 150px; border-radius: 6px; max-width: 320px; object-fit: cover; }
.nfo-code { background: #f8fafc; padding: 10px; border-radius: 6px; font-size: 12px; overflow-x: auto; max-height: 300px; }
</style>
