<template>
  <el-card shadow="never" class="page-card" v-loading="loading">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>订阅追剧</span>
        <span class="tip">「仅订阅 / 全盘转存」已改为按频道独立设置，可到「账号管理」页按频道开关</span>
      </div>
    </template>

    <!-- 订阅关键词列表：命中频道消息即按关键词自动追剧；操作含补全/重置（与转存记录、频道页按钮文案统一） -->
    <div class="sub-section">
      <div class="sub-title">
        <div style="display: flex; align-items: center; gap: 8px">
          <span>📋 订阅关键词</span>
          <el-tag size="small" class="count-tag">共 {{ items.length }} 条订阅</el-tag>
        </div>
        <span class="tip">命中频道消息后自动转存并追更；在下方 TMDB 海报墙点选「订阅」即可添加</span>
      </div>
      <el-table :data="items">
        <template #empty>
          <el-empty :image-size="60" description="还没有订阅，可在下方浏览或搜索后点击「订阅」添加" />
        </template>
        <el-table-column prop="keyword" label="关键词" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="sub-keyword">{{ row.keyword }}</span>
            <div v-if="row.aliases && row.aliases.length" class="sub-alias"
                 :title="row.aliases.join('、')">别名：{{ row.aliases.join('、') }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" @change="(v) => toggle(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="最近集数" width="100">
          <template #default="{ row }">{{ row.last_ep || '-' }}</template>
        </el-table-column>
        <el-table-column label="最近命中" min-width="180">
          <template #default="{ row }">
            <template v-if="row.last_match_at">
              <div>{{ fmtTime(row.last_match_at) }}</div>
              <div v-if="row.last_title" class="hit-title">{{ row.last_title }}</div>
            </template>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button type="primary" text @click="openBackfill(row)">补全</el-button>
              <el-popconfirm title="重置后将清除该订阅的追更进度记录，下次命中重新记录。确认重置？"
                             confirm-button-text="重置" cancel-button-text="取消" confirm-button-type="warning"
                             width="280" @confirm="resetProgress(row)">
                <template #reference><el-button type="warning" text>重置</el-button></template>
              </el-popconfirm>
              <el-popconfirm title="删除后将不再按该关键词自动追剧。确认删除？"
                             confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                             width="260" @confirm="del(row)">
                <template #reference><el-button type="danger" text>删除</el-button></template>
              </el-popconfirm>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-divider />

    <!-- 从 TMDB 选剧订阅：默认按分类浏览热门影视，点卡片订阅；精确查找时再搜索 -->
    <div class="tmdb-section">
      <div class="tmdb-title">
        🔍 从 TMDB 选剧订阅
        <span class="tip">API Key 在 themoviedb.org 免费申请</span>
        <!-- 搜索框固定在右上角（移动端独占一行与搜索按钮对齐）：未配置 Key 时置灰 -->
        <div class="tmdb-search-row">
          <el-tooltip :disabled="!!tmdbKey" content="先填写并保存 TMDB API Key" placement="top">
            <el-input v-model="searchWord" maxlength="80" :disabled="!tmdbKey"
                      :placeholder="tmdbKey ? '搜索片名，如：庆余年' : '配置 API Key 后可搜索'"
                      class="title-search" clearable @keyup.enter="doSearch" @clear="exitSearch">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </el-tooltip>
          <el-button type="primary" :disabled="!tmdbKey" :loading="searching" class="tmdb-search-btn" @click="doSearch">
            搜索
          </el-button>
        </div>
      </div>
      <!-- Key 输入：未配置或点「更换」时显示；已配置态只读展示 -->
      <div class="toolbar">
        <template v-if="tmdbKey && !editingKey">
          <el-tag type="success" effect="plain">API Key 已配置</el-tag>
          <el-button text type="primary" @click="startEditKey">更换 Key</el-button>
        </template>
        <template v-else>
          <el-input v-model="tmdbKeyInput" type="password" show-password
                    :placeholder="tmdbKey ? '输入新 Key（留空不修改）' : 'TMDB API Key (v3)'"
                    class="tmdb-key-input" clearable />
          <el-button :loading="savingKey" @click="saveTmdbKey">保存 Key</el-button>
          <el-button v-if="tmdbKey" text @click="editingKey = false">取消</el-button>
        </template>
      </div>

      <template v-if="tmdbKey">
        <!-- 分类浏览（默认）：只展示正在播出/即将上线等近期内容 -->
        <div class="tmdb-cats">
          <span v-for="c in CATS" :key="c.key" class="cat-chip"
                :class="{ active: mode === 'browse' && activeCat === c.key }"
                @click="switchCat(c.key)">{{ c.label }}</span>
          <template v-if="mode === 'search'">
            <span class="cat-chip active search-chip">
              搜索「{{ searchTerm }}」
              <el-icon class="clear-search" @click="exitSearch"><Close /></el-icon>
            </span>
          </template>
          <!-- 类型筛选：仅在数据混合了电影/剧集的分类下显示 -->
          <template v-if="showKindFilter">
            <span class="k-chips">
              <span v-for="k in KINDS" :key="k.value" class="cat-chip k-chip"
                    :class="{ active: kindFilter === k.value }"
                    @click="kindFilter = k.value">{{ k.label }}</span>
            </span>

          </template>
        </div>
      </template>

      <template v-if="tmdbKey">
        <div v-if="gridLoading && !filteredItems.length" class="tmdb-empty">加载中…</div>
        <div v-else-if="!filteredItems.length" class="tmdb-empty">
          {{ mode === 'search' ? '没有找到相关内容，请尝试其他片名' : (kindFilter ? '该筛选条件下暂时没有内容' : '该分类暂时没有内容') }}
        </div>
        <div v-if="filteredItems.length" class="tmdb-grid">
          <div v-for="it in filteredItems" :key="it.kind + it.id" class="tmdb-card">
            <div class="tmdb-poster">
              <el-image v-if="it.poster_path" :src="posterUrl(it.poster_path)"
                        fit="cover" lazy class="poster-img">
                <template #error>
                  <div class="poster-err" title="点击重试海报" @click.stop="retryPoster(it.poster_path)">
                    <el-icon :size="16"><RefreshRight /></el-icon>
                    <span>加载失败</span>
                    <span class="retry-hint">点击重试</span>
                  </div>
                </template>
              </el-image>
              <div v-else class="poster-none">无海报</div>
            </div>
            <div class="tmdb-info">
              <div class="tmdb-name" :title="it.name">{{ it.name }}</div>
              <div v-if="it.original && it.original !== it.name" class="tmdb-orig" :title="it.original">
                {{ it.original }}
              </div>
              <div class="tmdb-meta">
                <el-tag size="small" :type="it.kind === 'tv' ? 'warning' : 'primary'"
                        effect="plain">{{ it.kind === 'tv' ? '电视剧' : '电影' }}</el-tag>
                <el-tag v-if="it.season" size="small" type="danger" effect="plain"
                        :title="`最新一季：${it.season}（${it.date} 开播）`">{{ it.season }}</el-tag>
                <span v-if="it.date" class="tmdb-date">📺 {{ it.date }} {{ it.kind === 'movie' ? '上映' : (it.season ? '最新季' : '开播') }}</span>
                <span v-if="it.rating" class="tmdb-rating">⭐ {{ it.rating }}</span>
              </div>
              <div v-if="it.overview" class="tmdb-overview" :title="it.overview">{{ it.overview }}</div>
              <el-button size="small" type="primary" class="tmdb-sub-btn"
                         :disabled="isSubscribed(it)" @click="subFromTmdb(it)">
                {{ isSubscribed(it) ? '已订阅' : '订阅' }}
              </el-button>
            </div>
          </div>
        </div>
        <div v-if="filteredItems.length && mode === 'browse' && browsePage < browseTotalPages"
             class="tmdb-more">
          <el-button :loading="gridLoading" @click="loadMore">加载更多</el-button>
        </div>
      </template>
      <div v-else class="tmdb-empty">填写并保存 API Key 后，这里会自动展示正在播出 / 即将上线剧集和电影供点选订阅</div>
    </div>
  </el-card>

  <BackfillDialog ref="backfillDialogRef" />
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Close, RefreshRight } from '@element-plus/icons-vue'
import { api, errMsg, fmtTime, saveOk } from '../api'
import BackfillDialog from '../components/BackfillDialog.vue'

const items = ref([])
const loading = ref(false)

// ---- TMDB 选剧订阅：默认按分类浏览，搜索用于精确查找 ----
const tmdbKey = ref('')
const tmdbKeyInput = ref('')
const savingKey = ref(false)
const editingKey = ref(false) // 已配置态默认隐藏输入框，点「更换 Key」展开
const searchWord = ref('')
const searching = ref(false)
const searchTerm = ref('') // 当前搜索词（进入搜索模式时记录）

// 分类 → 后端 discover kind。全部只拉近期内容（追剧日历导向）：
// on_air 正在播出(在更剧+热映电影) / upcoming 即将上线(待映电影+定档新剧，按日期升序)
// / anime 动漫新番(45天) / doc 纪录片(90天)
const CATS = [
  { key: 'on_air', label: '📺 正在播出', kind: 'on_air' },
  { key: 'upcoming', label: '📅 即将上线', kind: 'upcoming' },
  { key: 'anime', label: '🎌 动漫新番', kind: 'anime' },
  { key: 'doc', label: '🌍 纪录片', kind: 'doc' },
]

const mode = ref('browse') // browse: 分类浏览 | search: 搜索结果
const activeCat = ref('on_air')
const browseItems = ref([])
const browsePage = ref(1)
const browseTotalPages = ref(1)
const gridLoading = ref(false)

// 类型筛选（仅「正在播出/即将上线」等混合分类显示）：''=全部 / tv / movie
const KINDS = [
  { value: '', label: '全部' },
  { value: 'tv', label: '电视剧' },
  { value: 'movie', label: '电影' },
]
const kindFilter = ref('')
const showKindFilter = computed(() =>
  mode.value === 'browse' && ['on_air', 'upcoming'].includes(activeCat.value))


// 网格统一展示：浏览模式展示分类数据，搜索模式展示搜索结果；叠加类型/地区筛选
const results = ref([])
const gridItems = computed(() => (mode.value === 'search' ? results.value : browseItems.value))
const filteredItems = computed(() => {
  let items = gridItems.value
  if (kindFilter.value && mode.value === 'browse') {
    items = items.filter((it) => it.kind === kindFilter.value)
  }

  return items
})

const startEditKey = () => {
  tmdbKeyInput.value = ''
  editingKey.value = true
}

const switchCat = (key) => {
  activeCat.value = key
  mode.value = 'browse'
  kindFilter.value = '' // 切分类时重置类型筛选
  loadBrowse(true)
}

// 请求序号：快速切换分类时丢弃过期响应，避免旧分类数据覆盖新分类
let browseSeq = 0

const loadBrowse = async (reset = true) => {
  const cat = CATS.find((c) => c.key === activeCat.value) || CATS[0]
  const page = reset ? 1 : browsePage.value + 1
  const seq = ++browseSeq
  gridLoading.value = true
  try {
    const { data } = await api.get('/api/tmdb/discover',
      { params: { kind: cat.kind, page } })
    if (seq !== browseSeq) return // 已被更新的请求取代
    // 追加分页时按 (kind,id) 去重：两页之间/多数据源合并都可能出现重复条目
    const merged = reset ? (data.items || []) : [...browseItems.value, ...(data.items || [])]
    const seen = new Set()
    browseItems.value = merged.filter((it) => {
      const key = `${it.kind}:${it.id}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
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

const loadMore = () => loadBrowse(false)

const doSearch = async () => {
  const q = searchWord.value.trim()
  if (q.length < 2) { ElMessage.warning('请输入至少 2 个字符的片名'); return }
  if (searching.value) return
  searching.value = true
  try {
    // 搜索不区分类型：电视剧+电影一起返回（后端 kind=all）
    const { data } = await api.get('/api/tmdb/search', { params: { query: q, kind: 'all' } })
    results.value = data.items || []
    searchTerm.value = q
    mode.value = 'search'
  } catch (e) { ElMessage.error(errMsg(e)) } finally { searching.value = false }
}

const exitSearch = () => {
  searchWord.value = ''
  searchTerm.value = ''
  results.value = []
  mode.value = 'browse'
}

const posterRetries = reactive({})
const retryPoster = (path) => {
  if (!path) return
  posterRetries[path] = Date.now()
}

const posterUrl = (path) => {
  if (!path) return ''
  // 海报鉴权走 HttpOnly Cookie（sa_poster / sa_auth），<img> 标签自动携带，无需任何 token
  const base = `/api/tmdb/poster?path=${encodeURIComponent(path)}`
  return posterRetries[path] ? `${base}&_r=${posterRetries[path]}` : base
}

// 已订阅判定：关键词或别名与片名/原始名一致（去空白、忽略大小写）
const normName = (s) => String(s || '').trim().toLowerCase()
const isSubscribed = (it) => {
  const names = [it.name, it.original].filter(Boolean).map(normName)
  return items.value.some((r) => {
    const terms = [r.keyword, ...(Array.isArray(r.aliases) ? r.aliases : [])]
      .filter(Boolean).map(normName)
    return terms.some((t) => names.includes(t))
  })
}

const saveTmdbKey = async () => {
  const key = tmdbKeyInput.value.trim()
  if (!key) { ElMessage.warning('请输入 TMDB API Key'); return }
  savingKey.value = true
  try {
    await api.put('/api/settings', { tmdb_api_key: key })
    tmdbKey.value = key
    tmdbKeyInput.value = ''
    editingKey.value = false // 保存成功回到已配置态（输入框收起）
    saveOk('TMDB API Key 已保存', '已拉取近期影视，点击卡片即可订阅')
    loadBrowse(true)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { savingKey.value = false }
}

const subFromTmdb = async (it) => {
  try {
    // 同时保存原始名作为别名：频道帖子常用原名，只存中文译名会漏命中
    const aliases = []
    if (it.original && it.original !== it.name) aliases.push(it.original)
    await api.post('/api/subscriptions', {
      keyword: it.name,
      aliases,
      tmdb_id: it.id,
      kind: it.kind,
      note: `TMDB ${it.kind === 'tv' ? '电视剧' : '电影'}`
        + (it.season ? ` · 最新季 ${it.season}` : '')
        + (it.year ? ` · ${it.year}` : ''),
    })
    ElMessage.success(`已添加订阅「${it.name}」，命中后会记录追更进度并推送通知`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const load = async () => {
  loading.value = true
  try {
    const [sub, st] = await Promise.all([
      api.get('/api/subscriptions'),
      api.get('/api/settings').catch(() => null),
    ])
    items.value = sub.data.items || []
    if (st?.data) {
      tmdbKey.value = String(st.data.tmdb_api_key || '')
    }
  } catch (e) { ElMessage.error(errMsg(e)) } finally { loading.value = false }
  // 已配置 Key：进页面就按分类拉取热门影视（无需搜索）
  if (tmdbKey.value) loadBrowse(true)
}

const toggle = async (row, v) => {
  try {
    await api.put(`/api/subscriptions/${row.id}`, { enabled: !!v })
    row.enabled = v ? 1 : 0
    ElMessage.success(v ? `已启用订阅「${row.keyword}」` : `已停用订阅「${row.keyword}」`)
  } catch (e) {
    row.enabled = v ? 0 : 1
    ElMessage.error(errMsg(e))
  }
}

const resetProgress = async (row) => {
  try {
    await api.put(`/api/subscriptions/${row.id}`, { reset_progress: true })
    ElMessage.success(`已重置「${row.keyword}」的追更进度`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
// ---- 向前补全：通过统一 BackfillDialog 组件处理 ----
const backfillDialogRef = ref(null)
const openBackfill = (row) => backfillDialogRef.value?.openForSubscription(row)

const del = async (row) => {
  try {
    await api.delete(`/api/subscriptions/${row.id}`)
    ElMessage.success(`已删除订阅「${row.keyword}」`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

onMounted(load)
</script>

<style scoped>
.sub-keyword { font-weight: 500; font-size: var(--fs-body); }
.tip { color: #999; font-size: var(--fs-caption); font-weight: 400; }
.hit-title { font-size: var(--fs-caption); color: #999; margin-top: 2px; word-break: break-all; }
.sub-alias { font-size: var(--fs-caption); color: #999; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- TMDB 选剧 ---- */
.sub-section,
.tmdb-section { margin-bottom: 4px; min-width: 0; }
.sub-title,
.tmdb-title {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-weight: 700; font-size: 15px; color: var(--saas-text-main);
  margin-bottom: 12px; padding: 9px 14px;
  background: var(--subcard-head-bg);
  border: 1px solid var(--saas-line);
  border-radius: 10px;
}
.sub-title .tip,
.tmdb-title .tip { font-weight: 400; }
.tmdb-search-row { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; max-width: 100%; }
.title-search { width: 320px; max-width: 100%; }
.tmdb-search-btn { flex: none; }
.tmdb-key-input { width: 300px; max-width: 100%; }
.k-chips { display: inline-flex; gap: 6px; margin-left: auto; }
.k-chip { font-size: var(--fs-caption); padding: 1px 10px; }
.tmdb-empty { color: #999; font-size: var(--fs-body); padding: 12px 0 4px; }
.tmdb-cats { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.cat-chip {
  padding: 5px 14px; border-radius: 999px; font-size: var(--fs-caption); cursor: pointer; user-select: none;
  background: var(--glass-bg-strong); color: var(--saas-text-sub);
  border: 1px solid var(--saas-line); transition: all .18s cubic-bezier(0.4, 0, 0.2, 1);
}
.cat-chip:hover {
  border-color: var(--el-color-primary);
  background: rgba(79, 107, 255, .12);
  color: var(--el-color-primary);
}
.cat-chip.active {
  background: var(--grad-brand); border-color: transparent; color: #fff; font-weight: 600;
  box-shadow: 0 3px 12px rgba(79, 107, 255, .38);
}
.search-chip { display: inline-flex; align-items: center; gap: 6px; }
.clear-search { cursor: pointer; }
.clear-search:hover { opacity: .75; }
.tmdb-more { text-align: center; margin-top: 14px; }
.tmdb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap: 12px; margin-top: 12px; }
.tmdb-card {
  display: flex; gap: 12px; padding: 12px; border-radius: 10px;
  background: var(--saas-card, #fff); border: 1px solid var(--saas-line);
  transition: all .2s ease;
}
.tmdb-card:hover {
  border-color: var(--el-color-primary-light-3);
  box-shadow: 0 6px 20px rgba(79, 107, 255, .15);
  transform: translateY(-2px);
}
.tmdb-poster { flex: none; }
.poster-img { width: 96px; height: 144px; border-radius: 8px; display: block; background: var(--saas-hover, #f4f7fe); }
.poster-err, .poster-none {
  width: 96px; height: 144px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: var(--saas-hover, #f4f7fe); color: #999; font-size: var(--fs-caption); text-align: center; padding: 6px;
  user-select: none; transition: background .15s, color .15s;
}
.poster-err {
  cursor: pointer; gap: 4px;
}
.poster-err:hover {
  background: var(--saas-active, #eaeeff);
  color: var(--el-color-primary, #4f6bff);
}
.poster-err .retry-hint {
  font-size: 11px;
  color: var(--el-color-primary, #4f6bff);
  opacity: .9;
}
.tmdb-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tmdb-name { font-weight: 600; font-size: var(--fs-body); color: var(--saas-text-main, #1f2d3d); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tmdb-orig { font-size: var(--fs-caption); color: #999; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tmdb-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 6px; font-size: var(--fs-caption); color: var(--saas-text-sub, #666); }
.tmdb-overview {
  font-size: var(--fs-caption); color: #8a97b3; line-height: 1.6; margin-top: 6px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.tmdb-sub-btn { margin-top: auto; align-self: flex-start; }

@media (max-width: 768px) {
  .tmdb-search-row {
    width: 100%;
    margin-left: 0;
  }
  .tmdb-search-row .title-search {
    flex: 1 1 0%;
    min-width: 0;
    width: auto;
  }
  .tmdb-key-input {
    width: auto;
  }
  .tmdb-grid {
    grid-template-columns: 1fr;
  }
}
</style>
