<template>
  <div class="files-page">
    <el-card shadow="never" class="page-card">
      <template #header>
        <FilesHeader
          ref="headerRef"
          :cid="cid"
          :path="path"
          :is-searching="isSearching"
          :search-term="searchTerm"
          v-model:search-scope="searchScope"
          :loading="loading"
          :current-name="currentName"
          :current-full-path="currentFullPath"
          @go-home="goHome"
          @go-parent="goParent"
          @go-path="goPath"
          @open-bookmark="openBookmark"
          @search="handleSearch"
          @clear-search="clearSearch"
          @refresh="load"
        />
      </template>
      <FilesToolbar
        :selected="selected"
        :selected-size-text="selectedSizeText"
        :clipboard="clipboard"
        :current-name="currentName"
        :is-searching="isSearching"
        :busy="busy"
        v-model:file-type="fileType"
        v-model:sort-by="sortBy"
        :sort-order="sortOrder"
        @mkdir="actionDialogsRef?.openMkdir()"
        @open-recycle-bin="openRecycleBin"
        @move-selected="openMovePicker(selected)"
        @copy-selected="copySelected"
        @cut-selected="cutSelected"
        @rename-selected="startRename(selected[0])"
        @delete-selected="deleteSelected"
        @clear-clipboard="clipboard = null"
        @paste="paste"
        @toggle-sort-order="toggleSortOrder"
      />
      <el-alert
        v-if="loadError"
        :title="loadError"
        type="error"
        show-icon
        :closable="false"
        class="load-alert"
      >
        <template #default>
          <div class="btn-row">
            <el-button text type="primary" @click="load">重试</el-button>
            <el-button
              v-if="needLogin"
              text
              type="primary"
              @click="$router.push('/account')"
            >
              去账号管理 →
            </el-button>
          </div>
        </template>
      </el-alert>
      <FilesTable
        ref="tableRef"
        :items="items"
        :loading="loading"
        :empty-desc="emptyDesc"
        :dir-stats="dirStats"
        :dir-stat-loading="dirStatLoading"
        :is-searching="isSearching"
        :search-scope="searchScope"
        :cid="cid"
        :total="total"
        :folder-count="folderCount"
        :file-count="fileCount"
        :clipboard="clipboard"
        v-model:page="page"
        :size="size"
        @selection-change="onSelection"
        @row-dblclick="onRowDblclick"
        @sort-change="onTableSortChange"
        @name-click="onNameClick"
        @open-dir="openDir"
        @show-detail="showDetail"
        @row-command="onRowCommand"
        @calc-dir-stat="calcDirStat"
        @page-change="onPageChange"
        @size-change="onSizeChange"
      />
    </el-card>
    <!-- 移动到：直接弹窗选择目标目录 -->
    <DirPicker
      v-model="movePickerVisible"
      :title="`移动 ${moveTargets.length} 项到目标目录`"
      @pick-node="onMoveTargetPicked"
    />
    <!-- 新建文件夹与重命名弹窗 -->
    <FilesActionDialogs ref="actionDialogsRef" :cid="cid" @saved="load" />

    <!-- 文件详情弹窗 -->
    <FilesDetailDialog
      v-model="detailVisible"
      :detail-row="detailRow"
      :is-searching="isSearching"
      :detail-parent-name="detailParentName"
      :detail-full-path="detailFullPath"
      :dir-stats="dirStats"
      :dir-stat-loading="dirStatLoading"
      @locate-parent="locateParentDir"
      @calc-dir-stat="calcDirStat"
      @copy-text="copyText"
    />
    <!-- 回收站查看与一键还原 -->
    <FilesRecycleBinDialog v-model="rbVisible" @recovered="onRecycleRecovered" />
  </div>
</template>
<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtSize } from '../api'
import DirPicker from '../components/DirPicker.vue'
import FilesHeader from '../components/files/FilesHeader.vue'
import FilesToolbar from '../components/files/FilesToolbar.vue'
import FilesTable from '../components/files/FilesTable.vue'
import FilesDetailDialog from '../components/files/FilesDetailDialog.vue'
import FilesRecycleBinDialog from '../components/files/FilesRecycleBinDialog.vue'
import FilesActionDialogs from '../components/files/FilesActionDialogs.vue'
import { useFileOperations } from '../components/files/useFileOperations'
import '../components/files/files.css'
const SORT_KEY = 'files_sort_by'
const ORDER_KEY = 'files_sort_order'
const VALID_SORTS = ['name', 'mtime', 'ptime']

const $router = useRouter()
const readSavedSort = () => {
  const s = localStorage.getItem(SORT_KEY)
  return VALID_SORTS.includes(s) ? s : 'name'
}
const readSavedOrder = () => (localStorage.getItem(ORDER_KEY) === 'desc' ? 'desc' : 'asc')

const headerRef = ref(null)
const actionDialogsRef = ref(null)
const tableRef = ref(null)
const items = ref([])
const total = ref(0)
const folderCount = ref(null)
const fileCount = ref(null)
const page = ref(1)
const size = ref(50)
const cid = ref('0')
const path = ref([{ id: '0', name: '根目录' }])
const loading = ref(false)
const loadError = ref('')
const selected = ref([])
const sortBy = ref(readSavedSort())
const sortOrder = ref(readSavedOrder())
const fileType = ref(0)
const searchScope = ref('all')
const dirStats = reactive({})
const dirStatLoading = reactive({})
const dirNameCache = reactive({ '0': '根目录' })

const selectedSizeText = computed(() => {
  if (!selected.value.length) return ''
  let bytes = 0
  let counted = 0
  let uncountedDirs = 0
  for (const r of selected.value) {
    if (!r.is_dir) {
      bytes += Number(r.size || 0)
      counted += 1
    } else if (dirStats[String(r.id)] && dirStats[String(r.id)].size >= 0) {
      bytes += Number(dirStats[String(r.id)].size || 0)
      counted += 1
    } else {
      uncountedDirs += 1
    }
  }
  if (counted === 0) return ''
  return uncountedDirs > 0 ? `共 ${fmtSize(bytes)}+` : `共 ${fmtSize(bytes)}`
})
const onSizeChange = (val) => {
  size.value = val
  page.value = 1
  load()
}
const onPageChange = (val) => {
  page.value = val
  load()
}
const searchTerm = ref('')
const isSearching = computed(() => !!searchTerm.value)
const currentName = computed(() => path.value[path.value.length - 1]?.name || '根目录')
const currentFullPath = computed(() => {
  const segs = path.value.slice(1).map((p) => p.name).filter(Boolean)
  return segs.length ? `/${segs.join('/')}` : '/'
})
const needLogin = computed(() => /未登录|Cookie|登录/.test(loadError.value))
const emptyDesc = computed(() => {
  if (isSearching.value) return '没有找到匹配的文件'
  if (fileType.value > 0) return '当前目录下没有该类型的文件'
  return '当前目录为空'
})
const itemFullPath = (row) => {
  if (!row) return currentFullPath.value
  const base = currentFullPath.value
  return base === '/' ? `/${row.name}` : `${base}/${row.name}`
}
const persistSort = () => {
  localStorage.setItem(SORT_KEY, sortBy.value)
  localStorage.setItem(ORDER_KEY, sortOrder.value)
}
let syncingSort = false
const syncTableSortArrow = () => {
  const tb = tableRef.value
  if (!tb) return
  syncingSort = true
  try {
    if (sortBy.value === 'name' || sortBy.value === 'mtime') {
      tb.sort(sortBy.value, sortOrder.value === 'desc' ? 'descending' : 'ascending')
    } else {
      tb.clearSort()
    }
  } finally {
    syncingSort = false
  }
}

const onTableSortChange = ({ prop, order }) => {
  if (syncingSort) return
  if (!order) {
    sortBy.value = 'name'
    sortOrder.value = 'asc'
  } else {
    sortBy.value = prop
    sortOrder.value = order === 'descending' ? 'desc' : 'asc'
  }
  persistSort()
  page.value = 1
  load()
}
watch(sortBy, () => {
  persistSort()
  page.value = 1
  nextTick(syncTableSortArrow)
  load()
})
const toggleSortOrder = () => {
  sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  persistSort()
  page.value = 1
  nextTick(syncTableSortArrow)
  load()
}
watch(fileType, () => {
  page.value = 1
  load()
})
watch(searchScope, () => {
  if (isSearching.value) {
    page.value = 1
    load()
  }
})

const calcDirStat = async (row, refresh = false) => {
  if (!row || !row.is_dir || !row.id || String(row.id) === '0') return
  const key = String(row.id)
  if (dirStatLoading[key]) return
  dirStatLoading[key] = true
  try {
    const { data } = await api.get('/api/files/stat', {
      params: { cid: key, refresh },
    })
    dirStats[key] = data
    if (data?.parent_name && row.parent_id) {
      dirNameCache[String(row.parent_id)] = data.parent_name
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '目录大小统计失败'))
  } finally {
    dirStatLoading[key] = false
  }
}
const pathFromSearch = ref(false)
const onSelection = (rows) => {
  selected.value = rows
}

const normalizePath = (raw, fallbackName = '') => {
  const list = []
  for (const p of raw || []) {
    const id = String(p.id ?? '')
    const name = String(p.name ?? '')
    if (!id || (id === '0' && list.length)) continue
    if (list.length && list[list.length - 1].id === id) continue
    list.push({ id, name: !name || name === id ? '' : name })
  }
  if (!list.length || list[0].id !== '0') {
    list.unshift({ id: '0', name: '根目录' })
  }
  const last = list[list.length - 1]
  const curName = fallbackName || currentName.value
  if (!last || String(last.id) !== String(cid.value)) {
    if (last && last.name && last.name === curName) {
      last.id = String(cid.value)
    } else {
      list.push({ id: String(cid.value), name: curName })
    }
  } else if (!last.name) {
    last.name = curName
  }
  return list
}
let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.get('/api/files/list', {
      params: {
        cid: cid.value,
        search: searchTerm.value,
        search_scope: searchScope.value,
        sort: sortBy.value,
        order: sortOrder.value,
        file_type: fileType.value,
        page: page.value,
        size: size.value,
        fc: folderCount.value !== null ? folderCount.value : undefined,
        file_count: fileCount.value !== null ? fileCount.value : undefined,
        total_count: total.value || undefined,
      },
    })
    if (seq !== loadSeq) return
    items.value = data.items || []
    total.value = data.total || 0
    folderCount.value = data.folder_count ?? null
    fileCount.value = data.file_count ?? null
    selected.value = []
    if (seq !== loadSeq) return
    if (!isSearching.value) {
      const cur = path.value[path.value.length - 1]
      if (pathFromSearch.value || !cur || String(cur.id) !== String(cid.value)) {
        const fallbackName = cur?.name || '根目录'
        path.value = normalizePath(data.path, fallbackName)
        pathFromSearch.value = false
      } else if (!cur.name) {
        cur.name = String(cid.value) === '0' ? '根目录' : String(cid.value)
      }
    }
  } catch (e) {
    if (seq !== loadSeq) return
    items.value = []
    total.value = 0
    folderCount.value = null
    fileCount.value = null
    loadError.value = errMsg(e)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
const clearLocalDirStats = () => {
  for (const k of Object.keys(dirStats)) delete dirStats[k]
}
const {
  busy,
  clipboard,
  movePickerVisible,
  moveTargets,
  copySelected,
  cutSelected,
  openMovePicker,
  onMoveTargetPicked,
  paste,
  deleteSelected,
  copyRows,
  cutRows,
  deleteRows,
} = useFileOperations({
  cid,
  currentName,
  isSearching,
  selected,
  load,
  clearLocalDirStats,
})
const goRoot = () => {
  searchTerm.value = ''
  searchScope.value = 'all'
  page.value = 1
  cid.value = '0'
  path.value = [{ id: '0', name: '根目录' }]
  pathFromSearch.value = false
  headerRef.value?.resetSearchInput('')
  headerRef.value?.collapseCrumbs()
  load()
}
const goHome = () => {
  if (isSearching.value) clearSearch()
  else goRoot()
}
const goParent = () => {
  if (isSearching.value || path.value.length <= 1) return
  goPath(path.value.length - 2)
}
const goPath = (index) => {
  if (index === path.value.length - 1) return
  const node = path.value[index]
  if (!node || !node.id) return
  searchTerm.value = ''
  page.value = 1
  cid.value = String(node.id)
  if (cid.value === '0') searchScope.value = 'all'
  path.value = path.value.slice(0, index + 1)
  pathFromSearch.value = false
  headerRef.value?.resetSearchInput('')
  headerRef.value?.collapseCrumbs()
  load()
}
const openBookmark = (bm) => {
  if (!bm || !bm.id) return
  searchTerm.value = ''
  page.value = 1
  cid.value = String(bm.id)
  path.value = [{ id: '0', name: '根目录' }, { id: String(bm.id), name: bm.name }]
  pathFromSearch.value = true
  headerRef.value?.resetSearchInput('')
  headerRef.value?.collapseCrumbs()
  load()
}
const openDir = (row) => {
  if (!row || !row.is_dir) return
  const wasSearching = isSearching.value
  searchTerm.value = ''
  page.value = 1
  cid.value = String(row.id)
  const existing = path.value.findIndex((p) => String(p.id) === String(row.id))
  if (existing >= 0) {
    path.value = path.value.slice(0, existing + 1)
  } else if (wasSearching) {
    path.value = [{ id: '0', name: '根目录' }, { id: String(row.id), name: row.name }]
    pathFromSearch.value = true
  } else {
    path.value = [...path.value, { id: String(row.id), name: row.name }]
  }
  headerRef.value?.resetSearchInput('')
  headerRef.value?.collapseCrumbs()
  load()
}
const locateParentDir = (row) => {
  if (!row || !row.parent_id) return
  const pid = String(row.parent_id)
  detailVisible.value = false
  if (pid === '0') {
    goRoot()
    return
  }
  searchTerm.value = ''
  page.value = 1
  cid.value = pid
  const pName = dirNameCache[pid] || dirStats[String(row.id)]?.parent_name || pid
  path.value = [{ id: '0', name: '根目录' }, { id: pid, name: pName }]
  pathFromSearch.value = true
  headerRef.value?.resetSearchInput('')
  headerRef.value?.collapseCrumbs()
  load()
}
const onNameClick = (row) => {
  if (row.is_dir) openDir(row)
  else showDetail(row)
}
const onRowDblclick = (row) => {
  if (row.is_dir) openDir(row)
}

const handleSearch = ({ term, scope }) => {
  if (!term) {
    clearSearch()
    return
  }
  searchTerm.value = term
  if (scope) searchScope.value = scope
  page.value = 1
  selected.value = []
  load()
}
const clearSearch = () => {
  searchTerm.value = ''
  page.value = 1
  headerRef.value?.resetSearchInput('')
  load()
}
const startRename = (row) => {
  actionDialogsRef.value?.openRename(row)
}
const onRowCommand = (cmd, row) => {
  if (cmd === 'locate_parent') locateParentDir(row)
  else if (cmd === 'copy_path') copyText(itemFullPath(row))
  else if (cmd === 'move_to') openMovePicker([row])
  else if (cmd === 'copy') copyRows([row])
  else if (cmd === 'cut') cutRows([row])
  else if (cmd === 'rename') startRename(row)
  else if (cmd === 'delete') deleteRows([row])
}
const detailVisible = ref(false)
const detailRow = ref(null)

const detailParentName = computed(() => {
  const row = detailRow.value
  if (!row || !row.parent_id) return ''
  const pid = String(row.parent_id)
  if (pid === '0') return '根目录'
  if (!isSearching.value && String(cid.value) === pid) return currentName.value
  const st = dirStats[String(row.id)]
  if (st?.parent_name) return st.parent_name
  return dirNameCache[pid] || ''
})
const detailFullPath = computed(() => {
  const row = detailRow.value
  if (!row) return ''
  if (!isSearching.value) return itemFullPath(row)
  return dirStats[String(row.id)]?.full_path || ''
})

const fetchParentName = async (pid) => {
  const key = String(pid || '')
  if (!key || key === '0' || dirNameCache[key]) return
  try {
    const { data } = await api.get('/api/files/name', { params: { cid: key } })
    if (data?.name) dirNameCache[key] = data.name
  } catch { /* 容错回退 */ }
}
const showDetail = (row) => {
  detailRow.value = row
  detailVisible.value = true
  if (row?.is_dir) {
    if (!dirStats[String(row.id)]) {
      calcDirStat(row).then(() => {
        if (isSearching.value && !detailParentName.value && row?.parent_id && String(row.parent_id) !== '0') {
          fetchParentName(row.parent_id)
        }
      })
    } else if (isSearching.value && !detailParentName.value && row?.parent_id && String(row.parent_id) !== '0') {
      fetchParentName(row.parent_id)
    }
  } else if (isSearching.value && row?.parent_id && String(row.parent_id) !== '0') {
    fetchParentName(row.parent_id)
  }
}
const rbVisible = ref(false)
const openRecycleBin = () => {
  rbVisible.value = true
}
const onRecycleRecovered = () => {
  clearLocalDirStats()
  load()
}
const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(String(text || ''))
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}
onMounted(() => {
  nextTick(syncTableSortArrow)
  load()
})
</script>
