<template>
  <div>
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>分类 → 目录 映射</span>
          <el-input v-model="newCat" placeholder="新分类名" style="width: 140px" @keyup.enter="addRoute" />
          <el-button @click="addRoute">添加分类</el-button>
          <el-tag v-if="dirtyRoutes" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
          <el-tooltip content="无法识别分类的资源：跳过，或输入任意目录统一存放（如 /待整理），输入后回车确认">
            <span style="color:#999; font-size:12px; margin-left: auto">
              未分类动作：
              <el-select v-model="unclassifiedAction" style="width: 170px" filterable allow-create
                         default-first-option @change="dirtyRoutes = true">
                <el-option label="跳过（不转存）" value="skip" />
                <el-option label="/待整理" value="/待整理" />
              </el-select>
            </span>
          </el-tooltip>
        </div>
      </template>
      <p class="page-tip">
        本页设置各分类的转存位置（分享转存 / 云下载可分目录）。资源如何被判定为某个分类，
        由「匹配规则」页的正则规则与黑名单决定。
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
        <el-table-column label="操作" width="90">
          <template #default="{ $index }">
            <el-popconfirm title="确认删除该分类映射？保存后生效。" confirm-button-text="删除"
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

    <!-- 115 目录选择器（分享转存 / 云下载共用） -->
    <DirPicker v-model="pickerVisible" :title="pickerTitle" :allow-skip="pickerKind === 'target'"
               :allow-empty="pickerKind === 'offline'" @pick="onPickDir" />
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import DirPicker from '../components/DirPicker.vue'

const routes = ref([])
const unclassifiedAction = ref('skip')
const newCat = ref('')
const dirtyRoutes = ref(false)
// 目录选择器状态：pickerKind = target 分享转存 | offline 云下载
const pickerVisible = ref(false)
const pickerKind = ref('target')
const pickerTitle = ref('选择 115 目录')
let pickerRow = null
let suppressWatch = false

watch([routes, unclassifiedAction], () => {
  if (!suppressWatch) dirtyRoutes.value = true
}, { deep: true })

const load = async () => {
  const { data } = await api.get('/api/rules')
  suppressWatch = true
  routes.value = data.routes
  unclassifiedAction.value = data.unclassified_action
  await nextTick()
  suppressWatch = false
  dirtyRoutes.value = false
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
const onPickDir = (val) => {
  if (!pickerRow) return
  if (pickerKind.value === 'target') pickerRow.target_dir = val
  else pickerRow.offline_dir = val
  dirtyRoutes.value = true
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
    saveOk('目录映射已保存', '分类目录更改已生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
useUnsavedGuard(dirtyRoutes)  // 有未保存更改时，离开页面弹窗确认
onMounted(load)
</script>

<style scoped>
.tip-text { color: #e6a23c; font-size: 12px; }
.page-tip { color: #909399; font-size: 12px; margin: 0 0 12px; }
.dir-btn { width: 100%; justify-content: flex-start; font-weight: normal;
           height: auto; min-height: 32px; padding: 5px 12px; white-space: normal; }
.dir-btn .el-icon { margin-right: 6px; color: #e6a23c; flex: none; align-self: flex-start; margin-top: 3px; }
.dir-btn :deep(span) {
  white-space: normal; word-break: break-all; text-align: left; line-height: 1.5;
}
.dir-btn.dir-skip { color: #909399; }
.dir-btn.dir-skip .el-icon { color: #909399; }
.dir-btn.dir-empty { color: #909399; font-style: italic; }
.dir-btn.dir-empty .el-icon { color: #c0c4cc; }
</style>
