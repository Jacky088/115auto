<template>
  <el-dialog :model-value="modelValue" :title="title" width="600px" top="6vh"
             :close-on-click-modal="false" @update:model-value="emitVisible" @open="onOpen">
    <!-- 未登录 115 时只提示，不展示目录 -->
    <el-alert v-if="loginErr" type="warning" show-icon :closable="false">
      <template #title>{{ loginErr }}</template>
      <div style="margin-top: 6px">
        <el-button text type="primary" @click="goAccount">去账号管理扫码登录 →</el-button>
      </div>
    </el-alert>

    <template v-else>
      <!-- 面包屑：点击任意层级返回 -->
      <div class="dp-crumbs">
        <el-icon :size="16" class="dp-root-icon"><FolderOpened /></el-icon>
        <button class="dp-crumb" :class="{ current: crumbs.length === 1 }" @click="goTo(0)">根目录</button>
        <template v-for="(p, i) in crumbs.slice(1)" :key="p.id">
          <span class="dp-sep">/</span>
          <button class="dp-crumb" :class="{ current: i + 1 === crumbs.length - 1 }"
                  :title="p.name" @click="goTo(i + 1)">{{ p.name }}</button>
        </template>
      </div>

      <div ref="listEl" v-loading="loading" class="dp-list">
        <el-alert v-if="loadErr" :title="loadErr" type="error" show-icon :closable="false" />
        <div v-else-if="!dirs.length && !loading" class="dp-empty">当前目录下没有文件夹</div>
        <div v-for="d in dirs" :key="rowPath(d)" class="dp-row"
             :class="{ active: selectedPath === rowPath(d) }" @click="select(d)">
          <span class="dp-radio" />
          <el-icon :size="16" class="dp-folder"><Folder /></el-icon>
          <span class="dp-name" :title="d.name">{{ d.name }}</span>
          <el-button text type="primary" @click.stop="enter(d)">进入</el-button>
        </div>
      </div>

      <div class="dp-picked">将选择：<b class="mono">{{ pendingValue }}</b></div>
    </template>

    <template #footer>
      <div class="dp-footer">
        <div class="dp-footer-left" style="gap: 10px">
          <el-button :disabled="needLogin" @click="showMkdir">
            <el-icon><FolderAdd /></el-icon>新建文件夹
          </el-button>
          <el-button v-if="allowSkip" @click="pickSpecial('skip')">跳过（不转存）</el-button>
          <el-button v-if="allowEmpty" @click="pickSpecial('')">清空（跟随分享转存）</el-button>
        </div>
        <div>
          <el-button @click="emitVisible(false)">取消</el-button>
          <el-button type="primary" :disabled="needLogin" @click="confirm">确定</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg } from '../api'

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '选择 115 目录' },
  allowSkip: Boolean,   // 是否提供「跳过（不转存）」选项
  allowEmpty: Boolean,  // 是否提供「清空」选项（如云下载目录留空 = 跟随分享转存）
})
const emit = defineEmits(['update:modelValue', 'pick'])

const router = useRouter()
const loginErr = ref('')
const loading = ref(false)
const loadErr = ref('')
const crumbs = ref([{ id: 0, name: '根目录' }])
const dirs = ref([])
const selectedPath = ref('')

const currentCid = computed(() => crumbs.value[crumbs.value.length - 1].id)
// 根目录表示为 /；其余按面包屑逐级拼接
const currentPath = computed(() =>
  crumbs.value.length <= 1 ? '/' : '/' + crumbs.value.slice(1).map((p) => p.name).join('/'))
// 行的唯一标识 = 该行的完整路径（同目录内文件夹名必不重复，不依赖 115 返回的 id）
const rowPath = (d) => (currentPath.value === '/' ? '' : currentPath.value) + '/' + d.name
// 确定/展示用：勾选了某行用勾选的目录，否则用当前浏览的目录
const pendingValue = computed(() => selectedPath.value || currentPath.value)

const emitVisible = (v) => emit('update:modelValue', v)
const goAccount = () => { emitVisible(false); router.push('/account') }

const onOpen = () => {
  loginErr.value = ''
  loadErr.value = ''
  selectedPath.value = ''
  crumbs.value = [{ id: 0, name: '根目录' }]
  load(0)
}

const _isLoginErr = (msg) => msg.includes('未登录') || msg.includes('Cookie')
const listEl = ref(null)
const load = async (cid = 0) => {
  loading.value = true
  loadErr.value = ''
  // 立即清空旧列表：v-loading 遮罩只覆盖滚动容器顶部区域，
  // 若保留旧行，滚动/超高部分的旧目录会露出来且仍可点击（与新列表混淆）
  dirs.value = []
  try {
    const { data } = await api.get('/api/files/list', { params: { cid, size: 500 } })
    dirs.value = (data.items || []).filter((i) => i.is_dir)
    // 加载成功即清除登录错误：瞬时故障（如 30s 负缓存窗口）不应永久禁用「新建文件夹/确定」
    loginErr.value = ''
    nextTick(() => { if (listEl.value) listEl.value.scrollTop = 0 })  // 新列表从顶部开始展示
  } catch (e) {
    dirs.value = []
    const msg = errMsg(e)
    if (_isLoginErr(msg)) loginErr.value = msg
    else loadErr.value = msg
  } finally { loading.value = false }
}

const _resetPick = () => { selectedPath.value = '' }

const select = (d) => {
  selectedPath.value = rowPath(d)
}
const enter = (d) => {
  crumbs.value = [...crumbs.value, { id: d.id, name: d.name }]
  _resetPick()
  load(d.id)
}
const goTo = (i) => {
  if (i === crumbs.value.length - 1) return
  crumbs.value = crumbs.value.slice(0, i + 1)
  _resetPick()
  load(crumbs.value[i].id)
}

const showMkdir = async () => {
  let name = ''
  try {
    const r = await ElMessageBox.prompt(
      `将在「${currentPath.value}」下创建文件夹`, '新建文件夹',
      { confirmButtonText: '创建', cancelButtonText: '取消', inputPattern: /\S/, inputErrorMessage: '请输入文件夹名称' })
    name = (r.value || '').trim()
  } catch { return } // 取消
  if (!name) return
  try {
    const { data } = await api.post('/api/files/mkdir', { pid: currentCid.value, name })
    ElMessage.success(`「${name}」已创建`)
    await load(currentCid.value)
    // 创建成功后自动勾选新文件夹，方便直接确定
    if (data?.cid != null && String(data.cid) !== '0') {
      selectedPath.value = rowPath({ name })
    }
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const pickSpecial = (val) => { emit('pick', val); emitVisible(false) }
const confirm = () => { emit('pick', pendingValue.value); emitVisible(false) }

// 按钮禁用条件：仅当「当前确实因未登录而看不到任何目录」时才禁用。
// 浏览正常（哪怕曾出现过瞬时登录错误）时一律可点，点击失败会有具体报错提示。
const needLogin = computed(() => !!loginErr.value && !dirs.value.length)
</script>

<style scoped>
.dp-crumbs {
  display: flex; align-items: center; flex-wrap: wrap; gap: 2px;
  padding: 8px 10px; background: #f5f7fa; border-radius: 6px; font-size: var(--fs-body);
}
.dp-root-icon { color: #e6a23c; margin-right: 4px; }
.dp-crumb {
  border: none; background: none; cursor: pointer; color: #409eff;
  padding: 2px 4px; font-size: var(--fs-body); max-width: 180px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.dp-crumb.current { color: #303133; font-weight: 600; cursor: default; }
.dp-sep { color: #999; }
.dp-list {
  margin-top: 10px; max-height: 320px; overflow-y: auto;
  border: 1px solid #ebeef5; border-radius: 6px; min-height: 140px;
}
.dp-row {
  display: flex; align-items: center; gap: 8px; padding: 7px 12px;
  cursor: pointer; border-bottom: 1px solid #f5f7fa; font-size: var(--fs-body);
}
.dp-row:last-child { border-bottom: none; }
.dp-row:hover { background: #f5f7fa; }
.dp-row.active { background: #ecf5ff; }
.dp-radio { width: 14px; height: 14px; border: 1px solid #dcdfe6; border-radius: 50%; flex: none; position: relative; }
.dp-row.active .dp-radio { border-color: #409eff; }
.dp-row.active .dp-radio::after {
  content: ''; position: absolute; inset: 3px; background: #409eff; border-radius: 50%;
}
.dp-folder { color: #e6a23c; flex: none; }
.dp-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dp-empty { padding: 40px 0; text-align: center; color: #999; font-size: var(--fs-body); }
.dp-picked { margin-top: 10px; font-size: var(--fs-body); color: #606266; }
.dp-footer { display: flex; justify-content: space-between; align-items: center; width: 100%; gap: 8px; flex-wrap: wrap; }
</style>
