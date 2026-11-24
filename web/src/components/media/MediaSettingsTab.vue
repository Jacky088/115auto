<template>
  <div>
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
          <div class="switch-row">
            <el-switch v-model="cfg.media_append_tmdb_tag" />
            <span class="tip-inline">Emby / Jellyfin / Infuse 零误识核心开关（例：<code>狂飙 (2023) [tmdbid=215403]</code>）</span>
          </div>
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
            <el-radio-button value="replace_if_better">高画质洗版（旧版移至归档目录）</el-radio-button>
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
          <div class="tip-block">≥ 此分数且领先第 2 候选分差 ≥ {{ cfg.media_scrape_margin_threshold }} 分才全自动改名；否则先挂起该条并等待人工确认</div>
        </el-form-item>

        <el-form-item label="一二名领先分差门禁">
          <el-input-number v-model="cfg.media_scrape_margin_threshold" :min="0" :max="40" />
          <span class="tip-inline">分（第 1 候选得分比第 2 候选高出此分差时才自动确认，防止同名翻拍影视误伤）</span>
        </el-form-item>

        <el-form-item label="本地元数据与海报">
          <div class="meta-desc-box">
            <div class="mdb-head">
              <el-tag type="success" effect="plain" size="small">✔ 本地默认常驻开启</el-tag>
              <span class="mdb-title">自动生成标准 .nfo 元数据与海报/背景高清封面</span>
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
            <span class="tip-inline">秒（如遇 115 限流提示会自动退避拉长间隔）</span>
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
          <div class="config-form-item sched-toggle-item mb12">
            <span class="config-label">定时整理：</span>
            <div class="config-control">
              <el-switch v-model="cfg.media_organize_schedule_enabled" @change="saveScheduleQuick" />
              <span class="tip-inline" style="margin-left: 8px">
                {{ cfg.media_organize_schedule_enabled ? '已开启后台无人值守自动扫描并执行目录整理' : '已关闭，开启后自动定时整理' }}
              </span>
            </div>
          </div>

          <div class="config-form-item sched-toggle-item mb12">
            <span class="config-label">定时刮削：</span>
            <div class="config-control">
              <el-switch v-model="cfg.media_scrape_schedule_enabled" @change="saveScheduleQuick" />
              <span class="tip-inline" style="margin-left: 8px">
                {{ cfg.media_scrape_schedule_enabled ? '已开启后台无人值守自动扫描影视并在线刮削' : '已关闭，开启后自动定时刮削' }}
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
                    ? '固定间隔：按设定间隔周期性整理'
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
                <el-select v-model="cfg.media_organize_schedule_hours" style="width: 170px" fit-input-width @change="saveScheduleQuick">
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
  </div>
</template>

<script setup>
import { inject, ref } from 'vue'
import { Timer, Lock, Calendar, Plus } from '@element-plus/icons-vue'
import { api, errMsg, toast } from '../../api'

const { isMobile } = inject('mediaCtx')

const savingCfg = ref(false)
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

async function loadSettings() {
  try {
    const { data } = await api.get('/api/media/settings')
    Object.assign(cfg.value, data)
    if (data && data.next_runs) {
      nextRuns.value = data.next_runs
    }
  } catch {}
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

defineExpose({
  loadSettings,
})
</script>
