<template>
  <div>
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="files-head">
          <div class="crumbs">
            <el-tooltip :content="isSearching ? '退出搜索' : '返回根目录'">
              <el-button text class="nav-btn" title="返回根目录" aria-label="返回根目录"
                         :disabled="!isSearching && cid === '0'" @click="goHome">
                <el-icon :size="17"><FolderOpened /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="返回上一级">
              <el-button text class="nav-btn" title="返回上一级" aria-label="返回上一级"
                         :disabled="isSearching || path.length <= 1" @click="goParent">
                <el-icon :size="16"><Back /></el-icon>
              </el-button>
            </el-tooltip>
            <template v-if="!isSearching">
              <template v-for="entry in visibleCrumbs" :key="entry.key">
                <span class="crumb-sep">/</span>
                <button v-if="entry.ellipsis" class="crumb crumb-ellipsis" title="展开完整路径"
                        @click="crumbsExpanded = true">…</button>
                <button v-else class="crumb" :class="{ current: entry.idx === path.length - 1 }"
                        :title="entry.node.name" @click="goPath(entry.idx)">{{ entry.node.name }}</button>
              </template>
              <el-tooltip :content="`复制路径：${currentFullPath}`">
                <el-button text class="crumb-icon-btn" title="复制当前路径" aria-label="复制当前路径"
                           @click="copyText(currentFullPath)">
                  <el-icon :size="14"><CopyDocument /></el-icon>
                </el-button>
              </el-tooltip>
              <el-tooltip v-if="cid !== '0'" :content="isCurrentBookmarked ? '取消收藏当前目录' : '收藏到常用目录'">
                <el-button text class="crumb-icon-btn" :class="{ 'is-starred': isCurrentBookmarked }"
                           :title="isCurrentBookmarked ? '取消收藏' : '收藏当前目录'"
                           :aria-label="isCurrentBookmarked ? '取消收藏' : '收藏当前目录'"
                           @click="toggleBookmark">
                  <el-icon :size="15">
                    <StarFilled v-if="isCurrentBookmarked" />
                    <Star v-else />
                  </el-icon>
                </el-button>
              </el-tooltip>
            </template>
            <template v-else>
              <span class="crumb current search-title">
                {{ searchScope === 'dir' && cid !== '0' ? '目录内搜索' : '全盘搜索' }}「{{ searchTerm }}」
              </span>
              <el-button text type="primary" @click="clearSearch">返回目录</el-button>
            </template>
          </div>
          <div class="head-actions">
            <el-select v-model="searchScope" class="scope-select" aria-label="搜索范围"
                       @change="onSearchScopeChange">
              <el-option label="全盘" value="all" />
              <el-option label="当前目录" value="dir" :disabled="cid === '0'" />
            </el-select>
            <el-input v-model="searchInput" class="search-input" clearable
                      :placeholder="searchScope === 'dir' && cid !== '0' ? '搜索当前目录' : '搜索全盘文件'"
                      @keyup.enter="doSearch" @clear="clearSearch">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-button @click="doSearch" :loading="loading">搜索</el-button>
            <el-button @click="load" :loading="loading">刷新</el-button>
          </div>
        </div>
      </template>

      <!-- 常用目录快捷书签栏（仅在有收藏时显示，本地保存，0 额外请求） -->
      <div v-if="bookmarks.length" class="bookmark-bar">
        <span class="bookmark-label">
          <el-icon :size="13" class="bookmark-star"><StarFilled /></el-icon>
          常用目录
        </span>
        <el-tag v-for="bm in bookmarks" :key="bm.id" size="small" closable
                :type="!isSearching && String(cid) === String(bm.id) ? 'primary' : 'info'"
                :effect="!isSearching && String(cid) === String(bm.id) ? 'dark' : 'plain'"
                class="bookmark-tag" :title="bm.path"
                @click="openBookmark(bm)" @close="removeBookmark(bm.id)">
          {{ bm.name }}
        </el-tag>
      </div>

      <div class="files-toolbar">
        <el-button type="primary" plain @click="mkdirVisible = true">
          <el-icon><FolderAdd /></el-icon>
          <span>新建文件夹</span>
        </el-button>
        <el-button plain @click="openRecycleBin">
          <el-icon><DeleteFilled /></el-icon>
          <span>回收站</span>
        </el-button>
        <template v-if="selected.length">
          <el-divider direction="vertical" />
          <span class="selected-count">
            已选 {{ selected.length }} 项<template v-if="selectedSizeText"> · {{ selectedSizeText }}</template>
          </span>
          <el-button @click="openMovePicker(selected)">
            <el-icon><FolderOpened /></el-icon>
            <span>移动到</span>
          </el-button>
          <el-button @click="copySelected">
            <el-icon><CopyDocument /></el-icon>
            <span>复制</span>
          </el-button>
          <el-button @click="cutSelected">
            <el-icon><Scissor /></el-icon>
            <span>剪切</span>
          </el-button>
          <el-button :disabled="selected.length !== 1" @click="startRename(selected[0])">
            <el-icon><EditPen /></el-icon>
            <span>重命名</span>
          </el-button>
          <el-button type="danger" plain @click="deleteSelected">
            <el-icon><Delete /></el-icon>
            <span>删除</span>
          </el-button>
        </template>
        <template v-if="clipboard">
          <el-divider direction="vertical" />
          <el-tag type="info" effect="plain" closable class="clip-tag" @close="clipboard = null">
            剪贴板：{{ clipboard.mode === 'copy' ? '复制' : '移动' }} {{ clipboard.items.length }} 项
          </el-tag>
          <el-tooltip :disabled="!isSearching" content="请先退出搜索后再粘贴" placement="top">
            <span>
              <el-button type="primary" :loading="busy"
                        :disabled="isSearching || !clipboard.items.length" @click="paste">
                <el-icon><DocumentCopy /></el-icon>
                <span>粘贴到「{{ currentName }}」</span>
              </el-button>
            </span>
          </el-tooltip>
        </template>

        <div class="toolbar-filters">
          <el-select v-model="fileType" class="filter-select" aria-label="文件类型筛选"
                     @change="onFilterChange">
            <el-option :value="0" label="全部类型" />
            <el-option :value="4" label="视频" />
            <el-option :value="2" label="图片" />
            <el-option :value="5" label="压缩包" />
            <el-option :value="1" label="文档" />
            <el-option :value="3" label="音频" />
          </el-select>
          <el-select v-model="sortBy" class="sort-select" aria-label="排序字段"
                     @change="onSortControlChange">
            <el-option value="name" label="按名称" />
            <el-option value="mtime" label="修改时间" />
            <el-option value="ptime" label="创建时间" />
          </el-select>
          <el-button class="order-btn" @click="toggleSortOrder"
                     :title="sortOrder === 'asc' ? '当前升序，点击切换降序' : '当前降序，点击切换升序'">
            <el-icon><Sort /></el-icon>
            <span>{{ sortOrder === 'asc' ? '升序' : '降序' }}</span>
          </el-button>
        </div>
      </div>

      <el-alert v-if="loadError" :title="loadError" type="error" show-icon :closable="false"
                class="load-alert">
        <template #default>
          <div class="btn-row">
            <el-button text type="primary" @click="load">重试</el-button>
            <el-button v-if="needLogin" text type="primary"
                       @click="$router.push('/account')">去账号管理 →</el-button>
          </div>
        </template>
      </el-alert>

      <el-table ref="tableRef" :data="items" size="small" v-loading="loading" row-key="id"
                @selection-change="onSelection" @row-dblclick="onRowDblclick"
                @sort-change="onTableSortChange">
        <template #empty>
          <el-empty :image-size="60" :description="emptyDesc" />
        </template>
        <el-table-column type="selection" width="44" />
        <el-table-column prop="name" label="名称" min-width="280" sortable="custom">
          <template #default="{ row }">
            <div class="file-name" role="button" tabindex="0"
                 :aria-label="(row.is_dir ? '打开目录 ' : '选择文件 ') + row.name"
                 @click="onNameClick(row)" @keydown.enter="onNameClick(row)"
                 @keydown.space.prevent="onNameClick(row)">
              <el-icon :size="17" :class="rowIconClass(row)">
                <component :is="rowIconName(row)" />
              </el-icon>
              <span class="file-text" :title="row.name">{{ row.name }}</span>
              <el-tag v-if="row.is_dir" size="small" type="warning" effect="plain" class="dir-tag">目录</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="118">
          <template #default="{ row }">
            <template v-if="!row.is_dir">{{ fmtSize(row.size) }}</template>
            <template v-else-if="dirStats[row.id]">
              <el-tooltip :content="`共 ${dirStats[row.id].file_count} 个文件，${dirStats[row.id].folder_count} 个子目录（点击刷新）`">
                <button class="dir-size-btn" :disabled="!!dirStatLoading[row.id]"
                        @click.stop="calcDirStat(row, true)">
                  <el-icon v-if="dirStatLoading[row.id]" class="is-loading"><Loading /></el-icon>
                  <span v-else>{{ formatDirStatSize(dirStats[row.id]) }}</span>
                </button>
              </el-tooltip>
            </template>
            <el-button v-else text type="primary" size="small" class="stat-calc-btn"
                       :loading="!!dirStatLoading[row.id]"
                       @click.stop="calcDirStat(row)">计算</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="mtime" label="修改时间" width="155" sortable="custom">
          <template #default="{ row }">{{ row.mtime ? fmtTime(row.mtime) : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="185" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button v-if="row.is_dir" text type="primary" @click="openDir(row)">打开</el-button>
              <el-button text @click="showDetail(row)">详情</el-button>
              <el-dropdown trigger="click" @command="onRowCommand($event, row)">
                <el-button text class="more-btn"><el-icon><MoreFilled /></el-icon></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-if="isSearching && row.parent_id" command="locate_parent">
                      进入所在目录
                    </el-dropdown-item>
                    <el-dropdown-item v-if="!isSearching" command="copy_path">复制路径</el-dropdown-item>
                    <el-dropdown-item command="move_to">移动到…</el-dropdown-item>
                    <el-dropdown-item command="copy">复制</el-dropdown-item>
                    <el-dropdown-item command="cut">剪切</el-dropdown-item>
                    <el-dropdown-item command="rename">重命名</el-dropdown-item>
                    <el-dropdown-item command="delete" divided class="danger-dropdown-item">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <span class="hint">
          <template v-if="isSearching">
            共搜索到 {{ total }} 项（{{ searchScope === 'dir' && cid !== '0' ? '当前目录' : '全盘' }}）
          </template>
          <template v-else>
            当前目录共 {{ total }} 项
            <template v-if="folderCount !== null && fileCount !== null">
              （{{ folderCount }} 个文件夹，{{ fileCount }} 个文件）
            </template>
          </template>
          <template v-if="clipboard"> · 剪贴板 {{ clipboard.items.length }} 项待{{ clipboard.mode === 'copy' ? '复制' : '移动' }}</template>
        </span>
        <el-pagination layout="total, sizes, prev, pager, next" :total="total"
                       :page-size="size" :page-sizes="[20, 50, 100]"
                       v-model:current-page="page" v-model:page-size="size"
                       @current-change="load" @size-change="onSizeChange" />
      </div>
    </el-card>

    <!-- 移动到：直接弹窗选择目标目录 -->
    <DirPicker v-model="movePickerVisible"
               :title="`移动 ${moveTargets.length} 项到目标目录`"
               @pick-node="onMoveTargetPicked" />

    <!-- 新建文件夹 -->
    <el-dialog v-model="mkdirVisible" title="新建文件夹" :width="'min(420px, 94vw)'">
      <el-form label-width="80px" @submit.prevent>
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
    <el-dialog v-model="renameVisible" title="重命名" :width="'min(420px, 94vw)'">
      <el-form label-width="80px" @submit.prevent>
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
    <el-dialog v-model="detailVisible" title="文件详情" :width="'min(560px, 94vw)'">
      <template v-if="detailRow">
        <div class="detail-head">
          <div class="detail-icon" :class="detailRow.is_dir ? 'folder-bg' : 'file-bg'">
            <el-icon :size="22" :class="detailRow.is_dir ? '' : rowIconClass(detailRow)">
              <component :is="rowIconName(detailRow)" />
            </el-icon>
          </div>
          <div class="detail-title">
            <div class="detail-name">{{ detailRow.name }}</div>
            <div class="detail-sub">{{ detailRow.is_dir ? '文件夹' : fileKindLabel(detailRow.name) }}</div>
          </div>
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item v-if="detailFullPath" label="完整路径">
            <div class="detail-val-row">
              <span class="mono break-all">{{ detailFullPath }}</span>
              <el-button text type="primary" size="small" @click="copyText(detailFullPath)">复制</el-button>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="文件 ID">
            <div class="detail-val-row">
              <span class="mono break-all">{{ detailRow.id }}</span>
              <el-button text type="primary" size="small" @click="copyText(detailRow.id)">复制</el-button>
            </div>
          </el-descriptions-item>
          <el-descriptions-item v-if="detailRow.parent_id" label="上级目录">
            <div class="detail-val-row">
              <span class="detail-parent-wrap">
                <template v-if="detailParentName">
                  <span>{{ detailParentName }}</span>
                  <span class="mono detail-parent-id">({{ detailRow.parent_id }})</span>
                </template>
                <span v-else class="mono">{{ detailRow.parent_id }}</span>
              </span>
              <el-button v-if="isSearching" text type="primary" size="small"
                         @click="locateParentDir(detailRow)">进入目录</el-button>
            </div>
          </el-descriptions-item>
          <el-descriptions-item v-if="detailRow.is_dir" label="目录大小">
            <div class="detail-val-row">
              <template v-if="dirStatLoading[detailRow.id]">
                <span>
                  <el-icon class="is-loading" style="vertical-align: middle; margin-right: 4px"><Loading /></el-icon>
                  正在统计…
                </span>
              </template>
              <template v-else-if="dirStats[detailRow.id]">
                <span>
                  <span>{{ formatDirStatSize(dirStats[detailRow.id]) }}</span>
                  <span class="detail-stat-meta">
                    （{{ dirStats[detailRow.id].file_count }} 个文件，{{ dirStats[detailRow.id].folder_count }} 个子目录）
                  </span>
                </span>
                <el-button text type="primary" size="small" @click="calcDirStat(detailRow, true)">刷新</el-button>
              </template>
              <el-button v-else text type="primary" size="small" @click="calcDirStat(detailRow)">点击统计</el-button>
            </div>
          </el-descriptions-item>
          <el-descriptions-item v-else label="大小">{{ fmtSize(detailRow.size) }}</el-descriptions-item>
          <el-descriptions-item label="修改时间">{{ detailRow.mtime ? fmtTime(detailRow.mtime) : '-' }}</el-descriptions-item>
          <el-descriptions-item v-if="detailRow.ctime" label="创建时间">{{ fmtTime(detailRow.ctime) }}</el-descriptions-item>
          <el-descriptions-item v-if="detailRow.pick_code" label="提取码">
            <div class="detail-val-row">
              <span class="mono break-all">{{ detailRow.pick_code }}</span>
              <el-button text type="primary" size="small" @click="copyText(detailRow.pick_code)">复制</el-button>
            </div>
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

    <!-- 回收站查看与一键还原 -->
    <el-dialog v-model="rbVisible" title="115 回收站" :width="'min(780px, 94vw)'" top="5vh">
      <div class="rb-head">
        <span class="rb-tip">支持单个或批量还原回原目录（防误删不提供彻底清空，如需清空请前往 115 客户端）。</span>
        <div class="rb-actions">
          <el-button v-if="rbSelected.length" type="primary" :loading="rbReverting"
                     @click="revertRecycleItems(rbSelected)">
            <el-icon><RefreshLeft /></el-icon>
            <span>还原已选 ({{ rbSelected.length }})</span>
          </el-button>
          <el-button :loading="rbLoading" @click="loadRecycleBin">刷新</el-button>
        </div>
      </div>
      <el-alert v-if="rbError" :title="rbError" type="error" show-icon :closable="false" class="load-alert" />
      <el-table :data="rbItems" size="small" v-loading="rbLoading" row-key="id" max-height="420"
                @selection-change="(rows) => { rbSelected = rows }">
        <template #empty>
          <el-empty :image-size="56" description="回收站为空" />
        </template>
        <el-table-column type="selection" width="42" />
        <el-table-column label="名称" min-width="220">
          <template #default="{ row }">
            <div class="file-name" style="cursor: default">
              <el-icon :size="16" :class="rowIconClass(row)">
                <component :is="rowIconName(row)" />
              </el-icon>
              <span class="file-text" :title="row.name">{{ row.name }}</span>
              <el-tag v-if="row.is_dir" size="small" type="warning" effect="plain" class="dir-tag">目录</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="原目录" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.parent_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="大小" width="92">
          <template #default="{ row }">{{ row.is_dir ? '-' : fmtSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="删除时间" width="145">
          <template #default="{ row }">{{ row.dtime ? fmtTime(row.dtime) : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" :loading="rbReverting" @click="revertRecycleItems([row])">
              还原
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <span class="hint">回收站共 {{ rbTotal }} 项</span>
        <el-pagination layout="prev, pager, next" :total="rbTotal"
                       :page-size="rbSize" v-model:current-page="rbPage"
                       @current-change="loadRecycleBin" />
      </div>
      <template #footer>
        <el-button @click="rbVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, fmtTime, saveOk } from '../api'
import DirPicker from '../components/DirPicker.vue'

const SORT_KEY = 'files_sort_by'
const ORDER_KEY = 'files_sort_order'
const BOOKMARKS_KEY = 'files_bookmarks'
const VALID_SORTS = ['name', 'mtime', 'ptime']

const readSavedSort = () => {
  const s = localStorage.getItem(SORT_KEY)
  return VALID_SORTS.includes(s) ? s : 'name'
}
const readSavedOrder = () => (localStorage.getItem(ORDER_KEY) === 'desc' ? 'desc' : 'asc')
const readBookmarks = () => {
  try {
    const arr = JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '[]')
    if (!Array.isArray(arr)) return []
    return arr.filter((x) => x && x.id && x.name).slice(0, 12)
  } catch { return [] }
}

const tableRef = ref(null)
const items = ref([])
const total = ref(0)
const folderCount = ref(null)
const fileCount = ref(null)
const page = ref(1)
const size = ref(50)  // 每页条数：默认 50，可在分页器里切换（20/50/100）
const cid = ref('0')
const path = ref([{ id: '0', name: '根目录' }])
const loading = ref(false)
const busy = ref(false)
const loadError = ref('')
const selected = ref([])

// 移动端面包屑折叠（路径 > 3 层时在窄屏折叠中间层级）
const isNarrow = ref(typeof window !== 'undefined' && window.innerWidth <= 768)
const crumbsExpanded = ref(false)
const onResize = () => { isNarrow.value = window.innerWidth <= 768 }

const visibleCrumbs = computed(() => {
  const list = path.value
  if (!isNarrow.value || crumbsExpanded.value || list.length <= 3) {
    return list.map((node, idx) => ({ key: `${node.id}-${idx}`, idx, node, ellipsis: false }))
  }
  return [
    { key: `${list[0].id}-0`, idx: 0, node: list[0], ellipsis: false },
    { key: 'ellipsis', idx: -1, node: null, ellipsis: true },
    { key: `${list[list.length - 2].id}-${list.length - 2}`, idx: list.length - 2, node: list[list.length - 2], ellipsis: false },
    { key: `${list[list.length - 1].id}-${list.length - 1}`, idx: list.length - 1, node: list[list.length - 1], ellipsis: false },
  ]
})

// 常用目录书签（localStorage 本地保存）
const bookmarks = ref(readBookmarks())
const saveBookmarks = () => {
  try { localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks.value)) } catch {}
}
const isCurrentBookmarked = computed(() =>
  cid.value !== '0' && bookmarks.value.some((b) => String(b.id) === String(cid.value)))

const toggleBookmark = () => {
  if (cid.value === '0' || isSearching.value) return
  const curId = String(cid.value)
  const idx = bookmarks.value.findIndex((b) => String(b.id) === curId)
  if (idx >= 0) {
    bookmarks.value.splice(idx, 1)
    saveBookmarks()
    ElMessage.info('已移出常用目录')
    return
  }
  if (bookmarks.value.length >= 12) {
    ElMessage.warning('最多收藏 12 个常用目录，请先移除不常用的')
    return
  }
  bookmarks.value.push({ id: curId, name: currentName.value, path: currentFullPath.value })
  saveBookmarks()
  ElMessage.success(`已收藏「${currentName.value}」`)
}

const removeBookmark = (id) => {
  bookmarks.value = bookmarks.value.filter((b) => String(b.id) !== String(id))
  saveBookmarks()
}

const openBookmark = (bm) => {
  if (!bm || !bm.id) return
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  crumbsExpanded.value = false
  cid.value = String(bm.id)
  path.value = [{ id: '0', name: '根目录' }, { id: String(bm.id), name: bm.name }]
  pathFromSearch.value = true
  load()
}

// 排序与筛选偏好（不提供按目录大小排序）
const sortBy = ref(readSavedSort())
const sortOrder = ref(readSavedOrder())
const fileType = ref(0)
const searchScope = ref('all')

// 目录大小按需统计结果与加载态（按 cid 映射），以及按 ID 缓存的目录名（供搜索模式详情显示上级目录名）
const dirStats = reactive({})
const dirStatLoading = reactive({})
const dirNameCache = reactive({ '0': '根目录' })

// 多选体积汇总（纯前端累加已选文件 + 已统计过大小的目录）
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

// 切换每页条数：回到第 1 页并重新加载
const onSizeChange = () => { page.value = 1; load() }

const searchInput = ref('')
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

const _EXT_VIDEO = new Set(['mkv', 'mp4', 'ts', 'mov', 'avi', 'rmvb', 'm2ts', 'flv', 'wmv', 'webm', 'iso', 'mpg', 'mpeg'])
const _EXT_SUB = new Set(['srt', 'ass', 'ssa', 'vtt', 'sub', 'sup', 'idx'])
const _EXT_IMG = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'svg', 'heic', 'avif'])
const _EXT_AUDIO = new Set(['mp3', 'flac', 'wav', 'aac', 'm4a', 'ogg', 'ape', 'wma', 'dts'])
const _EXT_ARCHIVE = new Set(['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'tgz'])

const fileKind = (name) => {
  const idx = String(name || '').lastIndexOf('.')
  if (idx < 0) return 'doc'
  const ext = name.slice(idx + 1).toLowerCase()
  if (_EXT_VIDEO.has(ext)) return 'video'
  if (_EXT_SUB.has(ext)) return 'sub'
  if (_EXT_IMG.has(ext)) return 'image'
  if (_EXT_AUDIO.has(ext)) return 'audio'
  if (_EXT_ARCHIVE.has(ext)) return 'archive'
  return 'doc'
}

const fileKindLabel = (name) => ({
  video: '视频文件',
  sub: '字幕文件',
  image: '图片文件',
  audio: '音频文件',
  archive: '压缩包',
  doc: '文件',
}[fileKind(name)] || '文件')

const rowIconName = (row) => {
  if (row?.is_dir) return 'Folder'
  return {
    video: 'VideoPlay',
    sub: 'Tickets',
    image: 'Picture',
    audio: 'Headset',
    archive: 'Files',
    doc: 'Document',
  }[fileKind(row?.name)] || 'Document'
}

const rowIconClass = (row) => {
  if (row?.is_dir) return 'folder-icon'
  return `file-icon file-icon--${fileKind(row?.name)}`
}

const formatDirStatSize = (st) => {
  if (!st) return '-'
  if (st.size > 0) return fmtSize(st.size)
  if (st.size_text) return st.size_text
  return '0 B'
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
  if (prop !== 'name' && prop !== 'mtime') return
  if (!order) {
    // 第三次点击取消表头箭头时，回落到默认名称升序
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

const onSortControlChange = () => {
  persistSort()
  page.value = 1
  nextTick(syncTableSortArrow)
  load()
}

const toggleSortOrder = () => {
  sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  persistSort()
  page.value = 1
  nextTick(syncTableSortArrow)
  load()
}

const onFilterChange = () => {
  page.value = 1
  load()
}

const onSearchScopeChange = () => {
  if (isSearching.value) {
    page.value = 1
    load()
  }
}

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

const clipboard = ref(null)
const pathFromSearch = ref(false) // 从搜索结果/书签进入目录后，需要用后端返回重建完整路径

const onSelection = (rows) => { selected.value = rows }

const normalizePath = (raw, fallbackName = '') => {
  // 清洗后端返回的路径节点：id 无效的丢弃（否则点面包屑会跳错目录），
  // 根目录（id=0）只允许出现在首位
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
      // 末位节点与当前目录同名但 id 对不上（115 path 解析错位）：认定为同一目录，纠正 id
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
      },
    })
    if (seq !== loadSeq) return  // 已有更新的请求发出：丢弃过期响应
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

const goRoot = () => {
  searchInput.value = ''
  searchTerm.value = ''
  searchScope.value = 'all'
  page.value = 1
  crumbsExpanded.value = false
  cid.value = '0'
  path.value = [{ id: '0', name: '根目录' }]
  pathFromSearch.value = false
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
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  crumbsExpanded.value = false
  cid.value = String(node.id)
  if (cid.value === '0') searchScope.value = 'all'
  path.value = path.value.slice(0, index + 1)
  pathFromSearch.value = false
  load()
}

const openDir = (row) => {
  if (!row || !row.is_dir) return
  const wasSearching = isSearching.value
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  crumbsExpanded.value = false
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
  load()
}

// 搜索结果定位所在目录
const locateParentDir = (row) => {
  if (!row || !row.parent_id) return
  const pid = String(row.parent_id)
  detailVisible.value = false
  if (pid === '0') { goRoot(); return }
  searchInput.value = ''
  searchTerm.value = ''
  page.value = 1
  crumbsExpanded.value = false
  cid.value = pid
  const pName = dirNameCache[pid] || dirStats[String(row.id)]?.parent_name || pid
  path.value = [{ id: '0', name: '根目录' }, { id: pid, name: pName }]
  pathFromSearch.value = true
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

const clearLocalDirStats = () => {
  for (const k of Object.keys(dirStats)) delete dirStats[k]
}

// 移动到…弹窗
const movePickerVisible = ref(false)
const moveTargets = ref([])
const openMovePicker = (rows) => {
  if (!rows || !rows.length) return
  moveTargets.value = rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir }))
  movePickerVisible.value = true
}
const onMoveTargetPicked = async (node) => {
  if (!node || !moveTargets.value.length) return
  const targetCid = String(node.cid ?? '0')
  const ids = moveTargets.value.map((i) => i.id).filter(Boolean)
  if (!ids.length) return
  if (moveTargets.value.some((i) => i.is_dir && String(i.id) === targetCid)) {
    ElMessage.warning('不能将目录移动到其自身内部')
    return
  }
  if (!isSearching.value && targetCid === String(cid.value)) {
    ElMessage.info('目标目录即当前目录，无需移动')
    return
  }
  busy.value = true
  try {
    await api.post('/api/files/move', { ids, target_cid: targetCid })
    saveOk('移动完成', `${ids.length} 项已移动到「${node.path || node.name}」`)
    selected.value = []
    moveTargets.value = []
    clearLocalDirStats()
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const paste = async () => {
  if (!clipboard.value || isSearching.value) return
  const ids = clipboard.value.items.map((i) => i.id).filter(Boolean)
  if (!ids.length) return
  if (clipboard.value.items.some((i) => i.is_dir && String(i.id) === String(cid.value))) {
    ElMessage.warning('不能将目录粘贴到其自身内部')
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
    clearLocalDirStats()
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { busy.value = false }
}

const deleteRows = async (rows) => {
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      '删除后可在 115 回收站中找回。',
      `删除选中的 ${rows.length} 项内容？`,
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'danger-confirm-btn' },
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
    clearLocalDirStats()
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
    saveOk('文件夹已创建', `「${name}」，位于当前目录`)
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
  } catch { /* 查询失败时自动回退只显示 ID，不打扰用户 */ }
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

// 回收站状态与还原
const rbVisible = ref(false)
const rbLoading = ref(false)
const rbReverting = ref(false)
const rbError = ref('')
const rbItems = ref([])
const rbTotal = ref(0)
const rbPage = ref(1)
const rbSize = ref(50)
const rbSelected = ref([])

const loadRecycleBin = async () => {
  rbLoading.value = true
  rbError.value = ''
  try {
    const { data } = await api.get('/api/files/recyclebin', {
      params: { page: rbPage.value, size: rbSize.value },
    })
    rbItems.value = data.items || []
    rbTotal.value = data.total || 0
    rbSelected.value = []
  } catch (e) {
    rbItems.value = []
    rbTotal.value = 0
    rbError.value = errMsg(e, '读取回收站失败')
  } finally {
    rbLoading.value = false
  }
}

const openRecycleBin = () => {
  rbPage.value = 1
  rbVisible.value = true
  loadRecycleBin()
}

const revertRecycleItems = async (rows) => {
  if (!rows || !rows.length) return
  try {
    await ElMessageBox.confirm(
      '将还原回删除前的原目录。',
      `还原选中的 ${rows.length} 项内容？`,
      { type: 'info', confirmButtonText: '还原', cancelButtonText: '取消' },
    )
  } catch { return }
  const ids = rows.map((r) => String(r.id)).filter(Boolean)
  rbReverting.value = true
  try {
    const { data } = await api.post('/api/files/recyclebin/revert', { ids })
    saveOk('已还原', `${data.count || ids.length} 项已恢复至原目录`)
    clearLocalDirStats()
    loadRecycleBin()
    load()
  } catch (e) {
    ElMessage.error(errMsg(e, '还原失败'))
  } finally {
    rbReverting.value = false
  }
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
  window.addEventListener('resize', onResize)
  nextTick(syncTableSortArrow)
  load()
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.files-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.crumbs { display: flex; align-items: center; gap: 2px; flex-wrap: wrap; min-width: 0; flex: 1; }
.nav-btn { padding: 4px 6px; height: 28px; color: var(--el-color-primary); }
.crumb-icon-btn { padding: 4px 6px; height: 26px; color: var(--saas-text-sub); margin-left: 2px; }
.crumb-icon-btn:hover { color: var(--el-color-primary); }
.crumb-icon-btn.is-starred { color: #f0a020; }
.crumb {
  border: 0; background: none; color: var(--saas-text-sub); font-size: var(--fs-body);
  padding: 4px 7px; border-radius: var(--r-sm); cursor: pointer; max-width: 220px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.crumb:hover { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.crumb.current { color: var(--saas-text-main); font-weight: 600; cursor: default; }
.crumb-ellipsis { padding: 4px 6px; font-weight: 600; }
.search-title { max-width: 360px; }
.crumb-sep { color: #c0cbe3; font-size: var(--fs-caption); }
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.scope-select { width: 102px; }
.search-input { width: 240px; }
.bookmark-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 10px; padding: 6px 12px;
  background: var(--glass-bg-strong); border: 1px dashed var(--saas-line); border-radius: var(--r-md);
}
.bookmark-label {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: var(--fs-caption); color: var(--saas-text-sub); flex: none;
}
.bookmark-star { color: #f0a020; }
.bookmark-tag { cursor: pointer; max-width: 180px; }
.files-toolbar {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px;
  padding: 10px 12px; background: var(--glass-bg-strong); border: 1px solid var(--saas-line); border-radius: var(--r-md);
  transition: all .2s cubic-bezier(0.4, 0, 0.2, 1);
}
.toolbar-filters {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-left: auto;
}
.filter-select { width: 108px; }
.sort-select { width: 108px; }
.order-btn { padding: 0 10px; }
.selected-count { font-size: var(--fs-body); color: var(--saas-text-main); font-weight: 600; }
.clip-tag { max-width: 240px; }
.load-alert { margin-bottom: 14px; }
.file-name { display: flex; align-items: center; gap: 8px; min-width: 0; cursor: pointer; }
.file-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--saas-text-main); }
.file-name:hover .file-text { color: var(--el-color-primary); }
.folder-icon { color: #f0a020; flex: none; }
.file-icon { color: #7c8aa5; flex: none; }
.file-icon--video { color: #4f6bff; }
.file-icon--sub { color: #16a394; }
.file-icon--image { color: #e68a1e; }
.file-icon--audio { color: #d94f7a; }
.file-icon--archive { color: #a67c52; }
.dir-tag { flex: none; }
.stat-calc-btn { padding: 2px 6px; height: 24px; font-size: var(--fs-caption); }
.dir-size-btn {
  border: 0; background: none; padding: 2px 4px; margin: 0; cursor: pointer;
  color: var(--saas-text-main); font-size: inherit; border-radius: var(--r-sm);
  display: inline-flex; align-items: center;
}
.dir-size-btn:hover { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.detail-val-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap;
}
.detail-parent-wrap { word-break: break-all; }
.detail-stat-meta { color: var(--saas-text-sub); font-size: var(--fs-caption); margin-left: 4px; }
.detail-parent-id { color: var(--saas-text-sub); margin-left: 6px; font-size: var(--fs-caption); }
.more-btn { padding: 4px 6px; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.hint { font-size: var(--fs-caption); color: var(--text-muted); }
.detail-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.detail-icon {
  width: 42px; height: 42px; border-radius: var(--r-md); flex: none;
  display: flex; align-items: center; justify-content: center;
}
.folder-bg { background: rgba(240, 160, 32, .12); color: #f0a020; }
.file-bg { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.detail-title { min-width: 0; }
.detail-name { font-weight: 600; font-size: var(--fs-body); color: var(--saas-text-main); word-break: break-all; }
.detail-sub { font-size: var(--fs-caption); color: var(--saas-text-sub); margin-top: 4px; }
.break-all { word-break: break-all; }
.rb-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap; margin-bottom: 12px;
}
.rb-tip { font-size: var(--fs-caption); color: var(--text-muted); }
.rb-actions { display: flex; gap: 8px; align-items: center; }

@media (max-width: 768px) {
  .files-head { align-items: flex-start; }
  .head-actions { width: 100%; }
  .scope-select { width: 96px; }
  .search-input { flex: 1 1 150px; width: auto; }
  .bookmark-bar { padding: 6px 8px; }
  .files-toolbar { padding: 8px; }
  .toolbar-filters { margin-left: 0; width: 100%; }
  .filter-select, .sort-select { flex: 1 1 96px; width: auto; }
  .crumb { max-width: 110px; padding: 4px; }
  .search-title { max-width: 180px; }
  .pager { justify-content: center; }
  .hint { width: 100%; text-align: center; }
}
</style>
