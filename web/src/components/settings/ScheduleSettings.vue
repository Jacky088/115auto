<template>
  <div class="schedule-settings-group">
    <div class="section-card">
      <div class="section-title">115防风控</div>
      <el-form-item id="setting-schedule-item" label="定时计划">
        <div class="switch-row">
          <el-switch v-model="f.schedule_enabled" />
          <span class="tip">开启后仅在允许时段内执行转存、云下载与定时文件整理（任务排队保留，时段外自动等待，规避深夜异常特征）</span>
        </div>
        <div v-if="f.schedule_enabled" class="range-row" style="margin-top: 8px">
          <el-time-select
            v-model="f.schedule_start"
            start="00:00"
            step="00:30"
            end="23:30"
            placeholder="开始"
            style="width: 110px"
          />
          <span class="sep">~</span>
          <el-time-select
            v-model="f.schedule_end"
            start="00:00"
            step="00:30"
            end="23:30"
            placeholder="结束"
            style="width: 110px"
          />
          <span class="tip">支持跨零点（如 22:00 ~ 08:00）</span>
        </div>
      </el-form-item>

      <el-form-item label="开工随机扰动">
        <div class="range-row">
          <el-input-number
            v-model="f.schedule_jitter_min"
            :min="0"
            :max="30"
            :step="1"
            :step-strictly="true"
            :precision="0"
            :disabled="!f.schedule_enabled"
          />
          <span class="tip range-tip">
            单位：分钟；定时时段到达后随机延迟 0~N 分钟开工，消除整点脚本特征（0 表示不扰动）
            <template v-if="!f.schedule_enabled">
              <span class="warn-text">（需先开启上方「定时计划」开关）</span>
              <el-button link type="primary" size="small" style="margin-left: 6px; font-weight: 500" @click="enableSchedule">
                一键开启
              </el-button>
            </template>
          </span>
        </div>
      </el-form-item>

      <el-form-item label="拟人化连存小憩">
        <div class="switch-row">
          <el-switch v-model="f.batch_rest_enabled" />
          <span class="tip">连续完成多笔转存后自动插入小憩，模拟真人离开电脑、喝水或浏览，打破固定匀速特征</span>
        </div>
        <template v-if="f.batch_rest_enabled">
          <div class="range-row" style="margin-top: 8px">
            <span class="tip">每连续完成</span>
            <el-input-number
              v-model="f.batch_rest_count"
              :min="2"
              :max="20"
              :step="1"
              :step-strictly="true"
              :precision="0"
              style="width: 126px"
            />
            <span class="tip">笔转存，</span>
          </div>
          <div class="range-row" style="margin-top: 8px">
            <span class="tip">随机小憩</span>
            <el-input-number
              v-model="f.batch_rest_min_min"
              :min="1"
              :max="60"
              :step="1"
              :step-strictly="true"
              :precision="0"
              style="width: 126px"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="f.batch_rest_max_min"
              :min="1"
              :max="60"
              :step="1"
              :step-strictly="true"
              :precision="0"
              style="width: 126px"
            />
            <span class="tip row-hint">分钟（区间：最短不能大于最长）</span>
          </div>
        </template>
      </el-form-item>

      <el-form-item label="后台巡检周期">
        <div class="range-row">
          <el-select v-model="f.health_check_interval_min" style="width: 220px" fit-input-width>
            <el-option label="每 5 分钟" :value="5" />
            <el-option label="每 10 分钟（推荐）" :value="10" />
            <el-option label="每 15 分钟" :value="15" />
            <el-option label="每 30 分钟" :value="30" />
          </el-select>
          <span class="tip range-tip">空闲时探测 115 Cookie 存活状态的间隔；适度放宽可削减 80% 的无效背景心跳调用</span>
        </div>
      </el-form-item>

      <el-form-item label="查重缓存">
        <div class="range-row">
          <el-select v-model="f.dedup_cache_ttl_sec" style="width: 220px" fit-input-width>
            <el-option label="不缓存（每次穿透查）" :value="0" />
            <el-option label="30 秒" :value="30" />
            <el-option label="60 秒" :value="60" />
            <el-option label="90 秒（推荐）" :value="90" />
            <el-option label="180 秒" :value="180" />
          </el-select>
          <span class="tip range-tip">同目录多集转存时复用最近读取的目录列表，避免每存一集就重复请求 115 目录接口</span>
        </div>
      </el-form-item>
    </div>
  </div>
</template>

<script setup>
import { nextTick } from 'vue'

const props = defineProps({
  f: {
    type: Object,
    required: true,
  },
})

const enableSchedule = () => {
  props.f.schedule_enabled = true
  nextTick(() => {
    const el = document.getElementById('setting-schedule-item')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('highlight-pulse')
      setTimeout(() => el.classList.remove('highlight-pulse'), 1600)
    }
  })
}
</script>
