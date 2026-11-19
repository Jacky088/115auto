<template>
  <div>
    <div class="files-head">
      <div class="crumbs">
        <el-tooltip :content="isSearching ? '退出搜索' : '返回根目录'">
          <el-button
            text
            class="nav-btn"
            title="返回根目录"
            aria-label="返回根目录"
            :disabled="!isSearching && cid === '0'"
            @click="$emit('go-home')"
          >
            <el-icon :size="17"><FolderOpened /></el-icon>
          </el-button>
        </el-tooltip>

        <el-tooltip content="返回上一级">
          <el-button
            text
            class="nav-btn"
            title="返回上一级"
            aria-label="返回上一级"
            :disabled="isSearching || path.length <= 1"
            @click="$emit('go-parent')"
          >
            <el-icon :size="16"><Back /></el-icon>
          </el-button>
        </el-tooltip>

        <template v-if="!isSearching">
          <template v-for="entry in visibleCrumbs" :key="entry.key">
            <span class="crumb-sep">/</span>
            <button
              v-if="entry.ellipsis"
              class="crumb crumb-ellipsis"
              title="展开完整路径"
              @click="crumbsExpanded = true"
            >…</button>
            <button
              v-else
              class="crumb"
              :class="{ current: entry.idx === path.length - 1 }"
              :title="entry.node.name"
              @click="$emit('go-path', entry.idx)"
            >
              {{ entry.node.name }}
            </button>
          </template>

          <el-tooltip :content="`复制路径：${currentFullPath}`">
            <el-button
              text
              class="crumb-icon-btn"
              title="复制当前路径"
              aria-label="复制当前路径"
              @click="copyText(currentFullPath)"
            >
              <el-icon :size="14"><CopyDocument /></el-icon>
            </el-button>
          </el-tooltip>

          <el-tooltip v-if="cid !== '0'" :content="isCurrentBookmarked ? '取消收藏当前目录' : '收藏到常用目录'">
            <el-button
              text
              class="crumb-icon-btn"
              :class="{ 'is-starred': isCurrentBookmarked }"
              :title="isCurrentBookmarked ? '取消收藏' : '收藏当前目录'"
              :aria-label="isCurrentBookmarked ? '取消收藏' : '收藏当前目录'"
              @click="toggleBookmark"
            >
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
          <el-button text type="primary" @click="handleClearSearch">返回目录</el-button>
        </template>
      </div>

      <div class="head-actions">
        <el-select
          :model-value="searchScope"
          class="scope-select"
          aria-label="搜索范围"
          @update:model-value="onScopeChange"
        >
          <el-option label="全盘" value="all" />
          <el-option label="当前目录" value="dir" :disabled="cid === '0'" />
        </el-select>
        <el-input
          v-model="searchInput"
          class="search-input"
          clearable
          :placeholder="searchScope === 'dir' && cid !== '0' ? '搜索当前目录' : '搜索全盘文件'"
          @keyup.enter="handleSearch"
          @clear="handleClearSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button :loading="loading" @click="handleSearch">搜索</el-button>
        <el-button :loading="loading" @click="$emit('refresh')">刷新</el-button>
      </div>
    </div>

    <!-- 常用目录快捷书签栏（仅在有收藏时显示，本地保存，0 额外请求） -->
    <div v-if="bookmarks.length" class="bookmark-bar">
      <span class="bookmark-label">
        <el-icon :size="13" class="bookmark-star"><StarFilled /></el-icon>
        常用目录
      </span>
      <el-tag
        v-for="bm in bookmarks"
        :key="bm.id"
        size="small"
        closable
        :type="!isSearching && String(cid) === String(bm.id) ? 'primary' : 'info'"
        :effect="!isSearching && String(cid) === String(bm.id) ? 'dark' : 'plain'"
        class="bookmark-tag"
        :title="bm.path"
        @click="$emit('open-bookmark', bm)"
        @close="removeBookmark(bm.id)"
      >
        {{ bm.name }}
      </el-tag>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Back,
  CopyDocument,
  FolderOpened,
  Search,
  Star,
  StarFilled,
} from '@element-plus/icons-vue'

const BOOKMARKS_KEY = 'files_bookmarks'

const props = defineProps({
  cid: { type: String, default: '0' },
  path: { type: Array, default: () => [{ id: '0', name: '根目录' }] },
  isSearching: { type: Boolean, default: false },
  searchTerm: { type: String, default: '' },
  searchScope: { type: String, default: 'all' },
  loading: { type: Boolean, default: false },
  currentName: { type: String, default: '根目录' },
  currentFullPath: { type: String, default: '/' },
})

const emit = defineEmits([
  'go-home',
  'go-parent',
  'go-path',
  'open-bookmark',
  'search',
  'clear-search',
  'refresh',
  'update:searchScope',
])

const searchInput = ref('')
const crumbsExpanded = ref(false)
const isNarrow = ref(typeof window !== 'undefined' && window.innerWidth <= 768)

const onResize = () => {
  isNarrow.value = window.innerWidth <= 768
}

onMounted(() => {
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})

const readBookmarks = () => {
  try {
    const arr = JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '[]')
    if (!Array.isArray(arr)) return []
    return arr.filter((x) => x && x.id && x.name).slice(0, 12)
  } catch {
    return []
  }
}

const bookmarks = ref(readBookmarks())

const saveBookmarks = () => {
  try {
    localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks.value))
  } catch {}
}

const isCurrentBookmarked = computed(() =>
  props.cid !== '0' && bookmarks.value.some((b) => String(b.id) === String(props.cid)))

const visibleCrumbs = computed(() => {
  const list = props.path
  if (!isNarrow.value || crumbsExpanded.value || list.length <= 3) {
    return list.map((node, idx) => ({ key: `${node.id}-${idx}`, idx, node, ellipsis: false }))
  }
  return [
    { key: `${list[0].id}-0`, idx: 0, node: list[0], ellipsis: false },
    { key: 'ellipsis', idx: -1, node: null, ellipsis: true },
    { key: `${list[list.length - 1].id}-${list.length - 1}`, idx: list.length - 1, node: list[list.length - 1], ellipsis: false },
  ]
})

const toggleBookmark = () => {
  if (props.cid === '0' || props.isSearching) return
  const curId = String(props.cid)
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
  bookmarks.value.push({ id: curId, name: props.currentName, path: props.currentFullPath })
  saveBookmarks()
  ElMessage.success(`已收藏「${props.currentName}」`)
}

const removeBookmark = (id) => {
  bookmarks.value = bookmarks.value.filter((b) => String(b.id) !== String(id))
  saveBookmarks()
}

const handleSearch = () => {
  const q = searchInput.value.trim()
  if (!q) {
    handleClearSearch()
    return
  }
  crumbsExpanded.value = false
  emit('search', { term: q, scope: props.searchScope })
}

const handleClearSearch = () => {
  searchInput.value = ''
  crumbsExpanded.value = false
  emit('clear-search')
}

const onScopeChange = (val) => {
  emit('update:searchScope', val)
  if (props.isSearching && searchInput.value.trim()) {
    emit('search', { term: searchInput.value.trim(), scope: val })
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

const resetSearchInput = (val = '') => {
  searchInput.value = val
}

defineExpose({
  resetSearchInput,
  collapseCrumbs: () => { crumbsExpanded.value = false },
})
</script>
