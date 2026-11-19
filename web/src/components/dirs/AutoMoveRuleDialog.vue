<template>
  <el-dialog
    v-model="visible"
    title="添加文件整理规则"
    :width="'min(540px, 94vw)'"
    destroy-on-close
    @open="onOpen"
  >
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
            <span class="info-tip-label">
              <span>按父目录名匹配</span>
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
                <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
              </el-tooltip>
            </span>
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
        <el-button class="dir-btn" size="small" style="width: 100%" @click="openPicker('dialog_source')">
          <el-icon><FolderOpened /></el-icon>
          <span>{{ newRuleForm.source_dir || '点击选择源目录（如 /自动转存）' }}</span>
        </el-button>
      </el-form-item>
      <el-form-item label="目标目录" required>
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-button class="dir-btn" size="small" style="width: 100%" @click="openPicker('dialog_target')">
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
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!newRuleForm.source_dir || !newRuleForm.target_dir" :loading="submitting" @click="confirmAddMoveRule">
        确定添加
      </el-button>
    </template>

    <DirPicker v-model="pickerVisible" :title="pickerTitle" @pick="onPickDir" />
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../../api'
import DirPicker from '../DirPicker.vue'
import { extractKeyword, normPath } from './autoMoveUtils'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  moveSettings: {
    type: Object,
    default: () => ({})
  },
  cloudDirs: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:modelValue', 'created'])

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const submitting = ref(false)
const newRuleForm = ref({
  source_dir: '',
  target_dir: '',
  keyword: '',
  mode: 'default',
  media_type: 'auto',
  dir_match: false,
})

const pickerVisible = ref(false)
const pickerKind = ref('dialog_source')
const pickerTitle = ref('选择源目录')

const onOpen = () => {
  newRuleForm.value = {
    source_dir: '',
    target_dir: '',
    keyword: '',
    mode: 'default',
    media_type: 'auto',
    dir_match: false,
  }
}

const sourceIsCloudDir = computed(() =>
  !!newRuleForm.value.source_dir && (props.cloudDirs || []).includes(normPath(newRuleForm.value.source_dir))
)

const isDialogLibraryMode = computed(() => {
  const m = newRuleForm.value.mode
  if (m === 'library') return true
  if (m === 'exact') return false
  return props.moveSettings.auto_move_mode === 'library'
})

// 选完目录后联动：目标路径预选媒体类型；源目录为云下载目录时自动开启按父目录名匹配
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

const openPicker = (kind) => {
  pickerKind.value = kind
  pickerTitle.value = kind === 'dialog_source' ? '选择源目录' : '选择目标目录'
  pickerVisible.value = true
}

const onPickDir = (val) => {
  if (pickerKind.value === 'dialog_source') {
    newRuleForm.value.source_dir = val
  } else if (pickerKind.value === 'dialog_target') {
    newRuleForm.value.target_dir = val
    newRuleForm.value.keyword = extractKeyword(val)
  }
}

const confirmAddMoveRule = async () => {
  if (!newRuleForm.value.source_dir || !newRuleForm.value.target_dir) return
  submitting.value = true
  try {
    await api.post('/api/auto-move/rules', {
      source_dir: newRuleForm.value.source_dir,
      target_dir: newRuleForm.value.target_dir,
      keyword: newRuleForm.value.keyword || extractKeyword(newRuleForm.value.target_dir),
      mode: newRuleForm.value.mode || 'default',
      media_type: newRuleForm.value.media_type || 'auto',
      dir_match: !!newRuleForm.value.dir_match,
    })
    visible.value = false
    ElMessage.success('整理规则已添加')
    emit('created')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    submitting.value = false
  }
}
</script>
