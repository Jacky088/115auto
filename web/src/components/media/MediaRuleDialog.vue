<template>
  <div>
  <!-- 新增/编辑规则弹窗（针对整理与刮削分别定制，完全独立，绝无“参与模块”复选框） -->
  <el-dialog
    v-model="ruleDialog.open"
    :title="ruleDialog.mode === 'organize'
      ? (ruleDialog.id ? '编辑目录整理规则' : '新增目录整理规则')
      : (ruleDialog.id ? '编辑媒体刮削目录' : '新增媒体刮削目录')"
    width="min(560px, 94vw)"
    align-center
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

    <DirPicker v-model="dirPicker.open" title="选择 115 网盘目录" @pick="onDirPicked" />
  </div>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { api, errMsg, toast } from '../../api'
import DirPicker from '../DirPicker.vue'

const { loadRules, loadStatus } = inject('mediaCtx')

const savingRule = ref(false)
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
  dirPicker.value = { open: true, field, target: 'organize' }
}

function onDirPicked(pickedPath) {
  ruleDialog.value[dirPicker.value.field] = pickedPath
  if (ruleDialog.value.mode === 'scrape') {
    ruleDialog.value.source_dir = pickedPath
    ruleDialog.value.target_dir = pickedPath
  }
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

defineExpose({
  openOrganizeRuleDialog,
  openScrapeRuleDialog,
})
</script>
