<template>
  <div class="wash-settings-group">
    <!-- 转存增强 -->
    <div class="section-card">
      <div class="section-title">转存增强</div>
      <el-form-item label="分享追更">
        <div class="switch-row">
          <el-switch v-model="f.share_update_enabled" :disabled="!f.enable_share_transfer" />
          <span class="tip">
            同一分享链接再次出现时不再判重：自动比对目标目录，只转存新增的集数文件，已存过的不重复（适配分享方往同一链接持续加更的追剧场景）
            <span v-if="!f.enable_share_transfer" class="warn-text">（需先开启上方「115 分享转存」总开关）</span>
          </span>
        </div>
      </el-form-item>
      <el-form-item label="按文件名去重">
        <div class="switch-row">
          <el-switch v-model="f.dedup_by_name" :disabled="!f.enable_share_transfer && !f.enable_offline_download" />
          <span class="tip">开启后分享转存前再按文件名查重：网盘同名目录下已存在同名文件时自动跳过</span>
        </div>
      </el-form-item>
      <el-form-item label="整理刮削">
        <div class="switch-row">
          <el-button type="primary" plain size="small" @click="$router.push('/media')">
            前往独立「整理刮削」工作台 →
          </el-button>
          <span class="tip">
            支持电影/剧集标准命名、[tmdbid] 标签、TMDB 精准刮削、本地 NFO/海报与 TG 联动人工确认
          </span>
        </div>
      </el-form-item>
    </div>

    <!-- 订阅质量与洗版 -->
    <div class="section-card">
      <div class="section-title">订阅质量与洗版</div>
      <el-form-item label="质量策略">
        <div class="switch-row">
          <el-switch v-model="f.quality_policy_enabled" />
          <span class="tip">
            开启后按标题识别画质（分辨率/来源/编码）：同一订阅的同一集出现多份分享时自动择优，
            画质相同时不再重复转存。识别不出画质的一律放行，不会误拦正常资源
          </span>
        </div>
      </el-form-item>
      <el-form-item label="最低画质">
        <el-select v-model="f.quality_min_resolution" :disabled="!f.quality_policy_enabled" style="width: 200px" fit-input-width>
          <el-option label="不限" value="" />
          <el-option label="720p 及以上" value="720p" />
          <el-option label="1080p 及以上" value="1080p" />
          <el-option label="2160p (4K)" value="2160p" />
        </el-select>
        <span class="tip">明确识别为低于该画质的分享直接跳过；订阅页可为单个订阅单独设置（优先于此处）</span>
      </el-form-item>
      <el-form-item label="洗版升级">
        <div class="switch-row">
          <el-switch v-model="f.wash_enabled" :disabled="!f.quality_policy_enabled" />
          <span class="tip">同一集出现更高画质时自动转存新版，并把旧版移入下方归档目录（只移动、不删除，可随时取回）</span>
        </div>
      </el-form-item>
      <el-form-item label="旧版归档目录">
        <div class="dir-pick-row">
          <el-button class="dir-btn" @click="washPickerOpen = true">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ f.wash_archive_dir || '点击选择 115 网盘目录' }}</span>
          </el-button>
          <el-button v-if="f.wash_archive_dir" text @click="clearArchiveDir">清空</el-button>
        </div>
        <div class="tip">开启洗版必须选择该目录；升级后旧版本只会被移入此处，不会删除。无法确认旧版位置时放弃移动</div>
      </el-form-item>
    </div>

    <DirPicker v-model="washPickerOpen" title="选择洗版旧版归档目录" @pick="onWashArchivePicked" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened } from '@element-plus/icons-vue'
import DirPicker from '../DirPicker.vue'

const props = defineProps({
  f: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['dirty'])

const washPickerOpen = ref(false)

const onWashArchivePicked = (path) => {
  const p = String(path || '').trim()
  if (!p) return
  if (p === '/') {
    ElMessage.warning('归档目录不能是网盘根目录，请选择或新建一个子目录')
    return
  }
  props.f.wash_archive_dir = p
  emit('dirty')
}

const clearArchiveDir = () => {
  props.f.wash_archive_dir = ''
  emit('dirty')
}
</script>
