<template>
  <div class="tmdb-section">
    <div class="tmdb-title">
      🔍 从 TMDB 选剧订阅
      <el-tooltip
        content="支持按本周热门/正在播出/即将上线/动漫/综艺/纪录片/高分榜浏览，也支持按片名或 IMDb ID（如 tt1234567）搜索"
        placement="top"
        effect="dark"
      >
        <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
      </el-tooltip>
      <div class="tmdb-search-row">
        <el-tooltip :disabled="!!tmdbKey" content="先填写并保存 TMDB API Key" placement="top" effect="dark">
          <el-input
            v-model="searchWord"
            name="tmdb-search-query"
            autocomplete="off"
            maxlength="80"
            :disabled="!tmdbKey"
            :placeholder="tmdbKey ? '搜索片名或 IMDb ID (tt...)' : '配置 API Key 后可搜索'"
            class="title-search"
            clearable
            @keyup.enter="doSearch(true)"
            @clear="exitSearch"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-tooltip>
        <el-button
          type="primary"
          :disabled="!tmdbKey"
          :loading="searching"
          class="tmdb-search-btn"
          @click="doSearch(true)"
        >
          搜索
        </el-button>
      </div>
    </div>

    <div class="toolbar">
      <template v-if="tmdbKey && !editingKey">
        <el-tag type="success" effect="plain">API Key 已配置</el-tag>
        <el-button text type="primary" @click="startEditKey">更换 Key</el-button>
      </template>
      <template v-else>
        <el-input
          v-model="tmdbKeyInput"
          name="tmdb-api-key"
          autocomplete="new-password"
          type="password"
          show-password
          :placeholder="tmdbKey ? '输入新 Key（留空不修改）' : 'TMDB API Key (v3)，可在 themoviedb.org 免费申请'"
          class="tmdb-key-input"
          clearable
        />
        <el-button :loading="savingKey" @click="saveTmdbKey">保存 Key</el-button>
        <el-button v-if="tmdbKey" text @click="editingKey = false">取消</el-button>
      </template>
    </div>

    <template v-if="tmdbKey">
      <div class="tmdb-cats">
        <span
          v-for="c in CATS"
          :key="c.key"
          class="cat-chip"
          :class="{ active: mode === 'browse' && activeCat === c.key }"
          @click="switchCat(c.key)"
        >{{ c.label }}</span>
        <span v-if="mode === 'search'" class="cat-chip active search-chip">
          搜索「{{ searchTerm }}」
          <el-icon class="clear-search" @click="exitSearch"><Close /></el-icon>
        </span>
      </div>

      <div class="tmdb-filter-bar">
        <div v-if="showKindFilter" class="seg-group" role="group" aria-label="类型筛选">
          <button
            v-for="k in KINDS"
            :key="k.value"
            type="button"
            class="seg-btn"
            :class="{ active: kindFilter === k.value }"
            @click="kindFilter = k.value"
          >{{ k.label }}</button>
        </div>

        <el-select
          v-if="mode === 'browse'"
          v-model="regionFilter"
          placeholder="全部地区"
          size="small"
          class="filter-select"
          @change="loadBrowse(true)"
        >
          <el-option v-for="rg in REGIONS" :key="rg.value" :label="rg.label" :value="rg.value" />
        </el-select>

        <el-select v-model="sortBy" size="small" class="filter-select sort-select">
          <el-option v-for="s in SORTS" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>

        <div class="filter-bar-right">
          <label class="hide-sub-toggle">
            <el-switch v-model="hideSubscribed" size="small" />
            <span class="tip-inline">隐藏已订阅</span>
          </label>
          <span v-if="hasActiveFilters" class="filter-summary">
            <span>{{ filteredItems.length }} 部</span>
            <el-button link type="primary" size="small" @click="resetFilters">重置</el-button>
          </span>
        </div>
      </div>

      <div v-if="isLoading && !filteredItems.length" class="tmdb-empty">加载中…</div>
      <div v-else-if="!filteredItems.length" class="tmdb-empty">
        {{ mode === 'search' ? '没有找到相关内容，请尝试其他片名' : '当前筛选条件下暂时没有内容' }}
      </div>
      <div v-if="filteredItems.length" class="tmdb-grid">
        <div v-for="it in filteredItems" :key="it.kind + ':' + it.id" class="tmdb-card">
          <div class="tmdb-poster">
            <el-image
              v-if="it.poster_path"
              :src="posterUrl(it.poster_path)"
              fit="cover"
              lazy
              class="poster-img"
            >
              <template #error>
                <div
                  class="poster-err"
                  role="button"
                  tabindex="0"
                  title="点击重试海报"
                  @click.stop="retryPoster(it.poster_path)"
                  @keydown.enter.stop="retryPoster(it.poster_path)"
                >
                  <el-icon :size="16"><RefreshRight /></el-icon>
                  <span>加载失败</span>
                  <span class="retry-hint">点击重试</span>
                </div>
              </template>
            </el-image>
            <div v-else class="poster-none">暂无海报</div>
          </div>
          <div class="tmdb-info">
            <div class="tmdb-name" :title="it.name">{{ it.name }}</div>
            <div v-if="it.original && it.original !== it.name" class="tmdb-orig" :title="it.original">
              {{ it.original }}
            </div>
            <div class="tmdb-meta">
              <el-tag size="small" :type="it.kind === 'tv' ? 'warning' : 'primary'" effect="plain">
                {{ it.kind === 'tv' ? '电视剧' : '电影' }}
              </el-tag>
              <el-tag v-if="it.season" size="small" type="danger" effect="plain">
                {{ it.season }}{{ it.episode_count ? ` · ${it.episode_count}集` : '' }}
              </el-tag>
              <el-tag v-if="countryLabel(it)" size="small" type="info" effect="plain">
                {{ countryLabel(it) }}
              </el-tag>
              <span v-if="it.date" class="tmdb-date">📺 {{ it.date }}</span>
              <span v-if="it.rating" class="tmdb-rating">⭐ {{ it.rating }}</span>
            </div>
            <div v-if="it.overview" class="tmdb-overview" :title="it.overview">{{ it.overview }}</div>
            <div class="tmdb-actions">
              <template v-if="matchedSub(it)">
                <el-button size="small" type="success" plain @click="$emit('edit-sub', matchedSub(it))">
                  已订阅 · 管理
                </el-button>
                <el-button
                  v-if="it.kind === 'tv'"
                  size="small"
                  text
                  type="primary"
                  @click="$emit('backfill-sub', matchedSub(it))"
                >
                  向前补全
                </el-button>
              </template>
              <template v-else>
                <el-button size="small" type="primary" @click="$emit('configure-sub', buildDraft(it))">
                  配置并订阅
                </el-button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <div v-if="canLoadMore" class="tmdb-more">
        <el-button :loading="isLoading" @click="loadMore">加载更多</el-button>
      </div>
    </template>
    <div v-else class="tmdb-empty tip">
      填写并保存 TMDB API Key 后，这里会自动展示近期热门影视与追剧日历供点选订阅。
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, InfoFilled, RefreshRight, Search } from '@element-plus/icons-vue'
import { api, errMsg, saveOk } from '../../api'

const props = defineProps({
  tmdbKey: { type: String, default: '' },
  subscriptions: { type: Array, default: () => [] },
})
const emit = defineEmits(['key-updated', 'edit-sub', 'configure-sub', 'backfill-sub'])

const CATS = [
  { key: 'trending', label: '🔥 本周热门' },
  { key: 'on_air', label: '📺 正在播出' },
  { key: 'upcoming', label: '📅 即将上线' },
  { key: 'anime', label: '🎌 动漫新番' },
  { key: 'variety', label: '🎤 热门综艺' },
  { key: 'doc', label: '🌍 纪录片' },
  { key: 'top_rated', label: '🏆 高分口碑' },
]
const REGIONS = [
  { value: '', label: '全部地区' },
  { value: 'CN', label: '华语' },
  { value: 'JPKR', label: '日韩' },
  { value: 'USGB', label: '欧美' },
]
const KINDS = [
  { value: '', label: '全部' },
  { value: 'tv', label: '电视剧' },
  { value: 'movie', label: '电影' },
]
const COUNTRY_MAP = {
  CN: '中国', HK: '香港', TW: '台湾', US: '美国', GB: '英国',
  JP: '日本', KR: '韩国', FR: '法国', DE: '德国', CA: '加拿大',
}

const SORTS = [
  { value: 'default', label: '默认排序' },
  { value: 'rating', label: '评分最高' },
  { value: 'date', label: '最新开播' },
  { value: 'popularity', label: '热度优先' },
]

const tmdbKeyInput = ref('')
const savingKey = ref(false)
const editingKey = ref(false)
const searchWord = ref('')
const searchTerm = ref('')
const searching = ref(false)
const mode = ref('browse')
const activeCat = ref('trending')
const regionFilter = ref('')
const kindFilter = ref('')
const sortBy = ref('default')
const hideSubscribed = ref(false)

const browseItems = ref([])
const browsePage = ref(1)
const browseTotalPages = ref(1)
const gridLoading = ref(false)

const searchItems = ref([])
const searchPage = ref(1)
const searchTotalPages = ref(1)

const isLoading = computed(() => (mode.value === 'search' ? searching.value : gridLoading.value))
const showKindFilter = computed(
  () => mode.value === 'search' || ['trending', 'on_air', 'upcoming', 'top_rated'].includes(activeCat.value),
)
const canLoadMore = computed(() => {
  if (mode.value === 'search') return searchItems.value.length > 0 && searchPage.value < searchTotalPages.value
  return browseItems.value.length > 0 && browsePage.value < browseTotalPages.value
})

const normName = (s) => String(s || '').trim().toLowerCase()
const matchedSub = (it) => {
  const names = [it.name, it.original].filter(Boolean).map(normName)
  return props.subscriptions.find((r) => {
    if (r.tmdb_id && it.id && Number(r.tmdb_id) === Number(it.id) && (!r.kind || r.kind === it.kind)) {
      return true
    }
    const terms = [r.keyword, ...(Array.isArray(r.aliases) ? r.aliases : [])]
      .filter(Boolean)
      .map(normName)
    return terms.some((t) => names.includes(t))
  }) || null
}

const hasActiveFilters = computed(
  () => Boolean(regionFilter.value || kindFilter.value || sortBy.value !== 'default' || hideSubscribed.value),
)

const filteredItems = computed(() => {
  let list = mode.value === 'search' ? [...searchItems.value] : [...browseItems.value]
  if (kindFilter.value) list = list.filter((it) => it.kind === kindFilter.value)
  if (hideSubscribed.value) list = list.filter((it) => !matchedSub(it))
  if (sortBy.value === 'rating') {
    list.sort((a, b) => (b.rating || 0) - (a.rating || 0) || (b.popularity || 0) - (a.popularity || 0))
  } else if (sortBy.value === 'date') {
    list.sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
  } else if (sortBy.value === 'popularity') {
    list.sort((a, b) => (b.popularity || 0) - (a.popularity || 0))
  }
  return list
})

const countryLabel = (it) => {
  const code = (it.origin_country && it.origin_country[0]) || ''
  return COUNTRY_MAP[code] || code
}

const dedupe = (arr) => {
  const seen = new Set()
  return arr.filter((it) => {
    const k = `${it.kind}:${it.id}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

let browseSeq = 0
const loadBrowse = async (reset = true) => {
  if (!props.tmdbKey) return
  const page = reset ? 1 : browsePage.value + 1
  const seq = ++browseSeq
  gridLoading.value = true
  try {
    const params = { kind: activeCat.value, page }
    if (regionFilter.value) params.region = regionFilter.value
    const { data } = await api.get('/api/tmdb/discover', { params })
    if (seq !== browseSeq) return
    browseItems.value = reset ? (data.items || []) : dedupe([...browseItems.value, ...(data.items || [])])
    browsePage.value = data.page || page
    browseTotalPages.value = data.total_pages || 1
  } catch (e) {
    if (seq === browseSeq) {
      if (reset) browseItems.value = []
      ElMessage.error(errMsg(e))
    }
  } finally {
    if (seq === browseSeq) gridLoading.value = false
  }
}

let searchSeq = 0
const doSearch = async (reset = true) => {
  const q = reset ? searchWord.value.trim() : searchTerm.value
  if (q.length < 2) { ElMessage.warning('请输入至少 2 个字符的片名'); return }
  const page = reset ? 1 : searchPage.value + 1
  const seq = ++searchSeq
  searching.value = true
  try {
    const { data } = await api.get('/api/tmdb/search', { params: { query: q, kind: 'all', page } })
    if (seq !== searchSeq) return
    searchItems.value = reset ? (data.items || []) : dedupe([...searchItems.value, ...(data.items || [])])
    searchTerm.value = q
    searchPage.value = data.page || page
    searchTotalPages.value = data.total_pages || 1
    mode.value = 'search'
  } catch (e) {
    if (seq === searchSeq) ElMessage.error(errMsg(e))
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

const loadMore = () => (mode.value === 'search' ? doSearch(false) : loadBrowse(false))
const switchCat = (key) => {
  activeCat.value = key
  mode.value = 'browse'
  kindFilter.value = ''
  loadBrowse(true)
}
const resetFilters = () => {
  const needReload = Boolean(regionFilter.value && mode.value === 'browse')
  regionFilter.value = ''
  kindFilter.value = ''
  sortBy.value = 'default'
  hideSubscribed.value = false
  if (needReload) loadBrowse(true)
}
const exitSearch = () => {
  searchWord.value = ''
  searchTerm.value = ''
  searchItems.value = []
  mode.value = 'browse'
}

const startEditKey = () => {
  tmdbKeyInput.value = ''
  editingKey.value = true
}
const saveTmdbKey = async () => {
  const key = tmdbKeyInput.value.trim()
  if (!key) { ElMessage.warning('请输入 TMDB API Key'); return }
  savingKey.value = true
  try {
    await api.put('/api/settings', { tmdb_api_key: key })
    tmdbKeyInput.value = ''
    editingKey.value = false
    saveOk('TMDB API Key 已保存', '已拉取近期影视，点击卡片即可订阅')
    emit('key-updated', key)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { savingKey.value = false }
}

const posterRetries = reactive({})
const retryPoster = (path) => { if (path) posterRetries[path] = Date.now() }
const posterUrl = (path) => {
  if (!path) return ''
  const base = `/api/tmdb/poster?path=${encodeURIComponent(path)}`
  return posterRetries[path] ? `${base}&_r=${posterRetries[path]}` : base
}

const buildDraft = (it) => {
  const aliases = it.original && it.original !== it.name ? [it.original] : []
  return {
    id: 0,
    tmdb_id: it.id,
    kind: it.kind || '',
    keyword: it.name,
    aliases,
    exclude_keywords: [],
    min_quality: '',
    note: '',
    auto_disable_movie: true,
  }
}

watch(
  () => props.tmdbKey,
  (k) => { if (k) loadBrowse(true) },
  { immediate: true },
)
</script>

<style scoped>
.tmdb-section { margin-bottom: 4px; min-width: 0; }
.tmdb-title {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-weight: 700; font-size: var(--fs-body); color: var(--saas-text-main);
  margin-bottom: 12px; padding: 9px 14px;
  background: var(--subcard-head-bg); border: 1px solid var(--saas-line); border-radius: var(--r-md);
}
.tmdb-search-row { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; max-width: 100%; }
.title-search { width: 320px; max-width: 100%; }
.tmdb-search-btn { flex: none; }
.tmdb-key-input { width: 360px; max-width: 100%; }
.tmdb-cats {
  display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
  overflow-x: auto; scrollbar-width: none; -webkit-overflow-scrolling: touch;
  margin-bottom: 10px; padding-bottom: 2px;
}
.tmdb-cats::-webkit-scrollbar { display: none; }
.tmdb-filter-bar {
  display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap;
  padding: 8px 12px; margin-bottom: 12px;
  background: var(--saas-hover, rgba(0, 0, 0, 0.03));
  border: 1px solid var(--saas-line); border-radius: var(--r-md);
}
.filter-select { width: 108px; }
.sort-select { width: 112px; }
.filter-bar-right { display: inline-flex; align-items: center; gap: 10px; margin-left: auto; flex-wrap: wrap; }
.filter-summary { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-caption); color: var(--text-muted); }
.hide-sub-toggle { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.cat-chip {
  flex: none; padding: 5px 14px; border-radius: 6px; font-size: var(--fs-caption); cursor: pointer; user-select: none;
  background: var(--glass-bg-strong); color: var(--saas-text-sub);
  border: 1px solid var(--saas-line); transition: all .18s cubic-bezier(0.4, 0, 0.2, 1); white-space: nowrap;
}
.cat-chip:hover { border-color: var(--el-color-primary); background: var(--saas-hover); color: var(--el-color-primary); }
.cat-chip.active {
  background: var(--el-color-primary, #256bf5); border-color: transparent; color: #fff; font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.22);
}
.search-chip { display: inline-flex; align-items: center; gap: 6px; }
.clear-search { cursor: pointer; }
.tmdb-empty { color: var(--text-muted); font-size: var(--fs-body); padding: 12px 0 4px; }
.tmdb-more { text-align: center; margin-top: 14px; }
.tmdb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap: 12px; margin-top: 12px; }
.tmdb-card {
  display: flex; gap: 12px; padding: 12px; border-radius: var(--r-md);
  background: var(--saas-card, #fff); border: 1px solid var(--saas-line); transition: all .2s ease;
}
.tmdb-card:hover { border-color: var(--el-color-primary-light-3); box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08); transform: translateY(-2px); }
.tmdb-poster { flex: none; }
.poster-img { width: 96px; height: 144px; border-radius: 8px; display: block; background: var(--saas-hover, #f4f7fe); }
.poster-err, .poster-none {
  width: 96px; height: 144px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: var(--saas-hover, #f4f7fe); color: var(--text-muted); font-size: var(--fs-caption); text-align: center; padding: 6px;
}
.poster-err { cursor: pointer; gap: 4px; }
.poster-err:hover { background: var(--saas-active, rgba(37, 107, 245, 0.12)); color: var(--el-color-primary, #256bf5); }
.poster-err .retry-hint { font-size: var(--fs-caption); color: var(--el-color-primary, #256bf5); opacity: .9; }
.tmdb-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tmdb-name { font-weight: 600; font-size: var(--fs-body); color: var(--saas-text-main, #1f2d3d); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tmdb-orig { font-size: var(--fs-caption); color: var(--text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tmdb-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 6px; font-size: var(--fs-caption); color: var(--saas-text-sub, #666); }
.tmdb-overview {
  font-size: var(--fs-caption); color: var(--text-muted); line-height: 1.6; margin-top: 6px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.tmdb-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: auto; padding-top: 8px; }

@media (max-width: 768px) {
  .tmdb-search-row { width: 100%; margin-left: 0; }
  .tmdb-search-row .title-search { flex: 1 1 0%; min-width: 0; width: auto; }
  .tmdb-key-input { width: auto; }
  .filter-bar-right { margin-left: 0; width: 100%; justify-content: space-between; }
  .tmdb-grid { grid-template-columns: 1fr; }
}
</style>
