<template>
  <div>
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="files-head">
          <div class="crumbs">
            <el-tooltip :content="isSearching ? '退出搜索' : '返回根目录'">
              <el-button text class="home-btn" :disabled="!isSearching && cid === '0'" @click="goHome">
                <el-icon :size="17"><FolderOpened /></el-icon>
              </el-button>
            </el-tooltip>
            <template v-if="!isSearching">
              <template v-for="(p, i) in path" :key="p.id">
                <span class="crumb-sep">/</span>
                <button class="crumb" :class="{ current: i === path.length - 1 }" :title="p.name"
                        @click="goPath(i)">{{ p.name }}</button>
              </template>
            </template>
            <template v-else>
              <span class="crumb current search-title">搜索「{{ searchTerm }}」</span>
              <el-button text type="primary" size="small" @click="clearSearch">返回目录</el-button>
            </template>
          </div>
          <div class="head-actions">
            <el-input v-model="searchInput" size="small" class="search-input" clearable
                      placeholder="搜索网盘中的全部文件" @keyup.enter="doSearch" @clear="clearSearch">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-button size="small" @click="doSearch" :loading="loading">搜索</el-button>
            <el-button size="small" @click="load" :loading="loading">刷新</el-button>
          </div>
        </div>
      </template>

      <div class="files-toolbar">
        <el-button type="primary" plain size="small" @click="mkdirVisible = true">
          <el-icon><FolderAdd /></el-icon>新建文件夹
        </el-button>
        <template v-if="selected.length">
          <el-divider direction="vertical" />
          <span class="selected-count">已选 {{ selected.length }} 项</span>
          <el-button size="small" @click="copySelected">
            <el-icon><CopyDocument /></el-icon>复制
          </el-button>
          <el-button size="small" @click="cutSelected">
            <el-icon><Scissor /></el-icon>剪切
          </el-button>
          <el-button size="small" :disabled="selected.length !== 1" @click="startRename(selected[0])">
            <el-icon><EditPen /></el-icon>重命名
          </el-button>
          <el-button size="small" type="danger" plain @click="deleteSelected">
            <el-icon><Delete /></el-icon>删除
          </el-button>
        </template>
        <template v-if="clipboard">
          <el-divider direction="vertical" />
          <el-tag type="info" effect="plain" closable class="clip-tag" @close="clipboard = null">
            剪贴板：{{ clipboard.mode === 'copy' ? '复制' : '移动' }} {{ clipboard.items.length }} 项
          </el-tag>
          <el-button type="primary" size="small" :loading="busy"
                    :disabled="isSearching || !clipboard.items.length" @click="paste">
            <el-icon><DocumentCopy /></el-icon>
            粘贴到「{{ isSearching ? '请先退出搜索' : currentName }}」
          </el-button>
        </template>
      </div>

      <el-alert v-if="loadError" :title="loadError" type="error" show-icon :closable="false"
                class="load-alert">
        <template #default>
          <el-button text type="primary" size="small" @click="load">重试</el-button>
          <el-button v-if="needLogin" text type="primary" size="small"
                     @click="$router.push('/account')">去账号管理 →</el-button>
        </template>
      </el-alert>

      <el-table :data="items" size="small" v-loading="loading" row-key="id"
                :empty-text="isSearching ? '没有找到匹配的文件' : '当前目录为空'"
                @selection-change="onSelection" @row-dblclick="onRowDblclick">
        <el-table-column type="selection" width="44" />
        <el-table-column label="名称" min-width="280">
          <template #default="{ row }">
            <div class="file-name" @click="onNameClick(row)">
              <el-icon :size="17" :class="row.is_dir ? 'folder-icon' : 'file-icon'">
                <Folder v-if="row.is_dir" />
                <Document v-else />
              </el-icon>
              <span class="file-text" :title="row.name">{{ row.name }}</span>
              <el-tag v-if="row.is_dir" size="small" type="warning" effect="plain" class="dir-tag">目录</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ row.is_dir ? '-' : fmtSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="修改时间" width="155">
          <template #default="{ row }">{{ row.mtime ? fmtTime(row.mtime) : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="185" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.is_dir" size="small" text type="primary" @click="openDir(row)">打开</el-button>
            <el-button size="small" text @click="showDetail(row)">详情</el-button>
            <el-dropdown trigger="click" @command="onRowCommand($event, row)">
              <el-button size="small" text class="more-btn"><el-icon><MoreFilled /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="copy">复制</el-dropdown-item>
                  <el-dropdown-item command="cut">剪切</el-dropdown-item>
                  <el-dropdown-item command="rename">重命名</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <span class="hint">
          {{ isSearching ? `共搜索到 ${total} 项` : `当前目录共 ${total} 项` }}
          <template v-if="clipboard"> · 剪贴板 {{ clipboard.items.length }} 项待{{ clipboard.mode === 'copy' ? '复制' : '移动' }}</template>
        </span>
        <el-pagination layout="prev, pager, next" :total="total" :page-size="size"
                       v-model:current-page="page" @current-change="load" />
      </div>
    </el-card>

    <!-- 新建文件夹 -->
    <el-dialog v-model="mkdirVisible" title="新建文件夹" width="420px">
      <el-form label-width="72px" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="mkdirName" maxlength="255" placeholder="请输入文件夹名称"
                    @keyup.enter="saveMkdir" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mkdirVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveMkdir">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重命名 -->
    <el-dialog v-model="renameVisible" title="重命名" width="420px">
      <el-form label-width="72px" @submit.prevent>
        <el-form-item label="新名称">
          <el-input v-model="renameForm.name" maxlength="255" placeholder="请输入新名称"
                    @keyup.enter="saveRename" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveRename">保存</el-button>
      </template>
    </el-dialog>

    <!-- 文件详情 -->
    <el-dialog v-model="detailVisible" title="文件详情" width="560px">
      <template v-if="detailRow">
        <div class="detail-head">
          <div class="detail-icon" :class="detailRow.is_dir ? 'folder-bg' : 'file-bg'">
            <el-icon :size="22">
              <Folder v-if="detailRow.is_dir" />
              <Document v-else />
            </el-icon>
          </div>
          <div class="detail-title">
            <div class="detail-name">{{ detailRow.name }}</div>
            <div class="detail-sub">{{ detailRow.is_dir ? '文件夹' : '文件' }}</div>
          </div>
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="文件 ID">
            <span class="mono">{{ detailRow.id }}</span>
            <el-button text type="primary" size="small" @click="copyText(detailRow.id)">复制</el-button>
          </el-descriptions-item>
          <el-descriptions-item v-if="detailRow.parent_id" label="上级目录 ID">
            <span class="mono">{{ detailRow.parent_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="!detailRow.is_dir" label="大小">{{ fmtSize(detailRow.size) }}</el-descriptions-item>
          <el-descriptions-item label="修改时间">{{ detailRow.mtime ? fmtTime(detailRow.mtime) : '-' }}</el-descriptions-item>
          <el-descriptions-item v-if="detailRow.pick_code" label="提取码">
            <span class="mono">{{ detailRow.pick_code }}</span>
            <el-button text type="primary" size="small" @click="copyText(detailRow.pick_code)">复制</el-button>
          </el-descriptions-item>
          <el-descriptions-item v-if="detailRow.sha1" label="SHA1">
            <span class="mono break-all">{{ detailRow.sha1 }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk } from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 100
const cid = ref('0')
const path = ref([{ id: '0', name: '根目录' }])
const loading = ref(false)
const busy = ref(false)
const loadError = ref('')
const selected = ref([])

const searchInput = ref('')
const searchTerm = ref('')
const isSearching = computed(() => !!searchTerm.value)
const currentName = computed(() => path.value[path.value.length - 1]?.name || '根目录')
const needLogin = computed(() => /未登录|Cookie|登录/.test(loadError.value))

const clipboard = ref(null)

const onSelection = (rows) => { selected.value = rows }

const normalizePath = (raw, fallbackName = '') => {
  const list = (raw || []).map((p) => {
    const id = String(p.id ?? '')
    const name = String(p.name ?? '')
    // 115 没返回目录名时后端会填 id，这里留空，稍后用当前已知名称补上
    return { id, name: !name || name === id ? '' : name }
  })
  if (!list.length || list[0].id !== '0') {
    list.unshift({ id: '0', name: '根目录' })
  }
  const last = list[list.length - 1]
  if (!last || String(last.id) !== String(cid.value)) {
    list.push({ id: String(cid.value), name: fallbackName || currentName.value })
  } else if (!last.name) {
    last.name = fallbackName || String(last.id)
  }
  return list
}

const load = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.get('/api/files/list', {
      params: { cid: cid.value, search: searchTerm.value, page: page.value, size },
    })
    items.value = data.items || []
    total.value = data.total || 0
    selected.value = []
    if (!isSearching.value) {
      const fallbackName = path.value[path.value.length - 1]?.name || '根目录'
      path.value = normalizePath(data.path, fallbackName)
    }
  } catch (e) {
    items.value = []
    total.value = 0
    loadError.value = errMsg(e)
  } finally {
    loading.value = false
  }
}

const goRoot = () => {
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  cid.value = '0'
  path.value = [{ id: '0', name: '根目录' }]
  load()
}

const goHome = () => {
  if (isSearching.value) clearSearch()
  else goRoot()
}

const goPath = (index) => {
  if (index === path.value.length - 1) return
  const node = path.value[index]
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  cid.value = String(node.id)
  path.value = path.value.slice(0, index + 1)
  load()
}

const openDir = (row) => {
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  cid.value = String(row.id)
  const existing = path.value.findIndex((p) => String(p.id) === String(row.id))
  path.value = existing >= 0
    ? path.value.slice(0, existing + 1)
    : [{ id: '0', name: '根目录' }, { id: String(row.id), name: row.name }]
  load()
}

const onNameClick = (row) => {
  if (row.is_dir) openDir(row)
  else showDetail(row)
}

const onRowDblclick = (row) => {
  if (row.is_dir) openDir(row)
}

const doSearch = () => {
  const keyword = searchInput.value.trim()
  if (!keyword) { clearSearch(); return }
  searchTerm.value = keyword
  page.value = 1
  selected.value = []
  load()
}

const clearSearch = () => {
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  load()
}

const copyRows = (rows) => {
  if (!rows.length) return
  clipboard.value = {
    mode: 'copy',
    items: rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir })),
  }
  ElMessage.success(`已复制 ${rows.length} 项，请选择目标目录后粘贴`)
}

const cutRows = (rows) => {
  if (!rows.length) return
  clipboard.value = {
    mode: 'move',
    items: rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir })),
  }
  ElMessage.success(`已剪切 ${rows.length} 项，请选择目标目录后粘贴`)
}

const copySelected = () => copyRows(selected.value)
const cutSelected = () => cutRows(selected.value)

const paste = async () => {
  if (!clipboard.value || isSearching.value) return
  const ids = clipboard.value.items.map((i) => i.id).filter(Boolean)
  if (!ids.length) return
  if (clipboard.value.items.some((i) => i.is_dir && String(i.id) === String(cid.value))) {
    ElMessage.warning('不能粘贴到被操作的目录自身')
    return
  }
  busy.value = true
  const moving = clipboard.value.mode === 'move'
  try {
    await api.post(moving ? '/api/files/move' : '/api/files/copy', {
      ids, target_cid: cid.value,
    })
    saveOk(moving ? '移动完成' : '复制完成', `${ids.length} 项已${moving ? '移动' : '复制'}到「${currentName.value}」`)
    if (moving) clipboard.value = null
    selected.value = []
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const deleteRows = async (rows) => {
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${rows.length} 项？删除后可在 115 回收站中找回。`,
      '删除文件', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  const ids = rows.map((r) => String(r.id))
  busy.value = true
  try {
    const { data } = await api.post('/api/files/delete', { ids })
    saveOk('已移入回收站', `${data.count || rows.length} 项已删除`)
    if (clipboard.value && clipboard.value.items.some((i) => ids.includes(String(i.id)))) {
      clipboard.value = null
    }
    selected.value = []
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const deleteSelected = () => deleteRows(selected.value)

const mkdirVisible = ref(false)
const mkdirName = ref('')
const saveMkdir = async () => {
  const name = mkdirName.value.trim()
  if (!name) { ElMessage.warning('请输入文件夹名称'); return }
  busy.value = true
  try {
    await api.post('/api/files/mkdir', { pid: cid.value, name })
    saveOk('文件夹已创建', `「${name}」已创建在当前目录`)
    mkdirVisible.value = false
    mkdirName.value = ''
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const renameVisible = ref(false)
const renameForm = reactive({ id: '', name: '' })
const startRename = (row) => {
  if (!row) return
  renameForm.id = String(row.id)
  renameForm.name = row.name
  renameVisible.value = true
}
const saveRename = async () => {
  const name = renameForm.name.trim()
  if (!name) { ElMessage.warning('请输入新名称'); return }
  busy.value = true
  try {
    await api.post('/api/files/rename', { id: renameForm.id, name })
    saveOk('重命名成功', `已重命名为「${name}」`)
    renameVisible.value = false
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const onRowCommand = (cmd, row) => {
  if (cmd === 'copy') copyRows([row])
  else if (cmd === 'cut') cutRows([row])
  else if (cmd === 'rename') startRename(row)
  else if (cmd === 'delete') deleteRows([row])
}

const detailVisible = ref(false)
const detailRow = ref(null)
const showDetail = (row) => {
  detailRow.value = row
  detailVisible.value = true
}
const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(String(text || ''))
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('当前浏览器不支持自动复制，请手动选择复制')
  }
}

onMounted(load)
</script>

<style scoped>
.files-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.crumbs { display: flex; align-items: center; gap: 2px; flex-wrap: wrap; min-width: 0; flex: 1; }
.home-btn { padding: 4px 6px; height: 28px; color: var(--el-color-primary); }
.crumb {
  border: 0; background: none; color: var(--saas-text-sub); font-size: 13px;
  padding: 4px 7px; border-radius: 6px; cursor: pointer; max-width: 220px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.crumb:hover { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.crumb.current { color: var(--saas-text-main); font-weight: 600; cursor: default; }
.search-title { max-width: 360px; }
.crumb-sep { color: #c0cbe3; font-size: 12px; }
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.search-input { width: 240px; }
.files-toolbar {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 14px;
  padding: 10px 12px; background: #f6f9ff; border: 1px solid var(--saas-line); border-radius: 10px;
}
.selected-count { font-size: 13px; color: #5a6a8a; }
.clip-tag { max-width: 240px; }
.load-alert { margin-bottom: 14px; }
.file-name { display: flex; align-items: center; gap: 8px; min-width: 0; cursor: pointer; }
.file-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #34405c; }
.file-name:hover .file-text { color: var(--el-color-primary); }
.folder-icon { color: #f0a020; flex: none; }
.file-icon { color: #7c8aa5; flex: none; }
.dir-tag { flex: none; }
.more-btn { padding: 4px 6px; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.hint { font-size: 12px; color: #8a97b3; }
.detail-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.detail-icon {
  width: 42px; height: 42px; border-radius: 10px; flex: none;
  display: flex; align-items: center; justify-content: center;
}
.folder-bg { background: rgba(240, 160, 32, .12); color: #f0a020; }
.file-bg { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.detail-title { min-width: 0; }
.detail-name { font-weight: 600; font-size: 14px; color: var(--saas-text-main); word-break: break-all; }
.detail-sub { font-size: 12px; color: var(--saas-text-sub); margin-top: 4px; }
.break-all { word-break: break-all; }

@media (max-width: 768px) {
  .files-head { align-items: flex-start; }
  .head-actions { width: 100%; }
  .search-input { flex: 1 1 150px; width: auto; }
  .files-toolbar { padding: 8px; }
  .crumb { max-width: 110px; padding: 4px; }
  .search-title { max-width: 180px; }
  .pager { justify-content: center; }
  .hint { width: 100%; text-align: center; }
}
</style>
