<template>
  <div>
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

    <DirPicker v-model="pickerVisible" :title="pickerTitle" :allow-skip="pickerKind === 'target'"
               :allow-empty="pickerKind === 'offline'" @pick="onPickDir" />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'
import { useUnsavedGuard } from '../../composables/unsavedGuard'
import DirPicker from '../DirPicker.vue'

const emit = defineEmits(['saved'])

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

watch([routes, unclassifiedAction], () => {
  if (!suppressWatch) dirtyRoutes.value = true
}, { deep: true })

const load = async () => {
  try {
    const { data } = await api.get('/api/rules')
    suppressWatch = true
    routes.value = data.routes || []
    unclassifiedAction.value = data.unclassified_action || 'skip'
    unclassifiedSaved = unclassifiedAction.value
    await nextTick()
    suppressWatch = false
    dirtyRoutes.value = false
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const isSkip = (d) => d === 'skip'

const openPicker = (row, kind) => {
  pickerRow = row
  pickerKind.value = kind
  pickerTitle.value = kind === 'offline'
    ? `为「${row.category}」选择云下载目录`
    : `为「${row.category}」选择分享转存目录`
  pickerVisible.value = true
}

const onPickDir = (val) => {
  if (!pickerRow) return
  if (pickerKind.value === 'target') pickerRow.target_dir = val
  else if (pickerKind.value === 'offline') pickerRow.offline_dir = val
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
    emit('saved')
  } catch (e) { ElMessage.error(errMsg(e)) }
}

useUnsavedGuard(dirtyRoutes)

defineExpose({ load })
</script>
