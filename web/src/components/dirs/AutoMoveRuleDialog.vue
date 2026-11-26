<template>
  <el-dialog
    v-model="visible"
    title="添加归档分流规则"
    :width="'min(540px, calc(100vw - 36px))'"
    align-center
    destroy-on-close
    @open="onOpen"
  >
    <el-form class="add-rule-form" :model="newRuleForm" label-width="100px" size="small">
      <el-form-item label="整理模式">
        <el-radio-group v-model="newRuleForm.mode">
          <el-radio value="default">跟随默认 ({{ moveSettings.auto_move_mode === 'library' ? '总库分流' : '一对一精确' }})</el-radio>
          <el-radio value="library">总库智能分流</el-radio>
          <el-radio value="exact">一对一精确匹配</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="媒体类型">
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-radio-group v-model="newRuleForm.media_type">
            <el-radio value="auto">自动</el-radio>
            <el-radio value="movie">电影库</el-radio>
            <el-radio value="tv">剧集库</el-radio>
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
                  云下载专用：按任务子目录名匹配（保留纯英文正片），自动过滤广告与 &lt;10MB 小文件，散落单文件与字幕不移动；仅限源目录为云下载目录时可用。
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
            ⚠ 已开启按任务子目录名匹配：散落单文件与字幕不移动，广告/小文件自动过滤；若源目录不再是云下载目录将自动失效。
          </span>
        </div>
      </el-form-item>
      <el-form-item label="英文片名">
        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%">
          <el-checkbox v-model="newRuleForm.allow_english">
            <span class="info-tip-label">
              <span>允许匹配纯英文剧集/电影</span>
              <el-tooltip placement="top" effect="dark">
                <template #content>
                  默认仅移动文件名含汉字的资源以防误动系统或杂项文件。<br />
                  开启后允许纯英文文件名及英文子库目录（如 Breaking Bad）参与整理，仍受媒体扩展名白名单与预告/花絮过滤保护。
                </template>
                <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
              </el-tooltip>
            </span>
          </el-checkbox>
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
  allow_english: false,
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
    allow_english: false,
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
      allow_english: !!newRuleForm.value.allow_english,
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
