<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>黑名单</span>
        <span style="color: var(--text-muted); font-size: var(--fs-caption)">
          频道/关键词命中会在提取链接前拦截；文件名正则与分享体积下限在转存前复核，命中记录可复核放行
        </span>
      </div>
    </template>

    <el-tabs v-model="blTab" @tab-change="onTabChange">
      <el-tab-pane label="关键词" name="keyword" />
      <el-tab-pane label="频道" name="channel" />
      <el-tab-pane label="文件名正则" name="file_pattern" />
      <el-tab-pane label="命中记录" name="hits" />
    </el-tabs>

    <template v-if="blTab !== 'hits'">
      <div class="toolbar">
        <el-input
          v-model="blNewValue"
          class="bl-add-input"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 8 }"
          :placeholder="blPlaceholder"
        />
        <el-button type="primary" @click="addBlacklistItem">添加</el-button>
        <el-button v-if="blTab === 'keyword'" @click="blRestoreDefaults">恢复默认</el-button>
        <el-button v-if="blTab === 'file_pattern'" @click="regexHelpVisible = true">正则说明</el-button>
      </div>
      <p class="tip-box is-neutral">{{ blHint }}</p>

      <div class="bl-filter-row">
        <el-input
          v-model="blSearch"
          size="small"
          clearable
          placeholder="搜索黑名单内容…"
          style="width: 240px; max-width: 100%"
        />
        <span class="bl-count">共 {{ filteredItems.length }} 条</span>
      </div>

      <el-table :data="pagedItems" size="small" empty-text="暂无条目">
        <el-table-column prop="value" label="内容" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ mono: blTab === 'file_pattern' }">{{ row.value }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="添加时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" text @click="delBlacklistItem(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="filteredItems.length > PAGE_SIZE" class="bl-pager">
        <el-pagination
          v-model:current-page="blPage"
          :page-size="PAGE_SIZE"
          :total="filteredItems.length"
          layout="total, prev, pager, next"
          size="small"
        />
      </div>
    </template>

    <template v-else>
      <div class="bl-filter-row">
        <el-radio-group v-model="hitStageFilter" size="small" @change="hitPage = 1">
          <el-radio-button value="">全部环节</el-radio-button>
          <el-radio-button value="message">消息级</el-radio-button>
          <el-radio-button value="share_info">内容级</el-radio-button>
        </el-radio-group>
        <span class="bl-count">共 {{ filteredHits.length }} 条记录</span>
      </div>
      <p class="tip-box is-neutral">
        被黑名单拦截的消息记录。点击内容可查看完整消息（分享链接已高亮）；若确认为误拦，可点击「放行」重新处理。
      </p>
      <el-table :data="pagedHits" size="small" empty-text="暂无拦截记录" v-loading="blHitsLoading">
        <el-table-column prop="created_at" label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="channel" label="频道" width="120">
          <template #default="{ row }"><span class="mono">@{{ row.channel }}</span></template>
        </el-table-column>
        <el-table-column label="拦截原因" width="200">
          <template #default="{ row }">
            <el-tag size="small" :type="stageTag(row.stage)" style="margin-right: 5px">
              {{ stageLabel(row.stage) }}
            </el-tag>
            <el-tag size="small" type="danger" effect="plain">
              {{ row.rule_value || ruleTypeLabel(row.rule_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="260">
          <template #default="{ row }">
            <span
              class="hit-summary"
              role="button"
              tabindex="0"
              aria-label="查看拦截详情"
              :title="row.text"
              @click="openDetail(row)"
              @keydown.enter="openDetail(row)"
              @keydown.space.prevent="openDetail(row)"
            >
              {{ (row.text || '（无文本）').slice(0, 60) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="252" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button text type="primary" @click="openDetail(row)">详情</el-button>
              <el-button type="primary" text @click="releaseHit(row, false)">放行</el-button>
              <el-button
                v-if="canRemoveRule(row)"
                type="warning"
                text
                @click="releaseHit(row, true)"
              >放行并移除词</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="filteredHits.length > PAGE_SIZE" class="bl-pager">
        <el-pagination
          v-model:current-page="hitPage"
          :page-size="PAGE_SIZE"
          :total="filteredHits.length"
          layout="total, prev, pager, next"
          size="small"
        />
      </div>
    </template>

    <!-- 拦截详情弹窗 -->
    <el-dialog v-model="detailVisible" title="拦截详情" :width="'min(640px, calc(100vw - 36px))'" align-center>
      <el-descriptions :column="2" border size="small" v-if="detailRow">
        <el-descriptions-item label="时间">{{ fmtTime(detailRow.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="来源频道">
          <span class="mono">@{{ detailRow.channel }}</span> · 消息 #{{ detailRow.message_id }}
        </el-descriptions-item>
        <el-descriptions-item label="拦截环节">{{ stageLabel(detailRow.stage) }}</el-descriptions-item>
        <el-descriptions-item label="命中规则">
          <el-tag size="small" type="danger" effect="plain">
            {{ ruleTypeLabel(detailRow.rule_type) }}：{{ detailRow.rule_value }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <div class="detail-content" v-html="detailHtml"></div>
      <template #footer>
        <div class="detail-footer">
          <el-button text @click="copyLinks">复制消息中的资源链接</el-button>
          <div style="display: flex; align-items: center; gap: 10px">
            <el-button
              v-if="detailRow && canRemoveRule(detailRow)"
              type="warning"
              plain
              @click="releaseFromDetail(true)"
            >放行并移除该词</el-button>
            <el-button type="primary" @click="releaseFromDetail(false)">放行并转存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 文件名正则帮助 -->
    <el-dialog v-model="regexHelpVisible" title="文件名正则说明" :width="'min(780px, calc(100vw - 36px))'" align-center>
      <div class="help-body">
        <h4>一、作用机制</h4>
        <p>
          转存前会先读取分享内的<b>文件列表</b>：只要任意一个文件名命中你设置的正则，
          <b>整个分享就会被跳过</b>（记录为「黑名单」状态，可到命中记录里放行）。适合过滤广告文件、样本、垃圾附件等。
        </p>
        <p class="eg">例：设置 <code>\.url$</code> 后，分享内含有「更多资源点这里.url」这类广告文件的分享会被整体拦截。</p>
        <h4>二、语法要点</h4>
        <el-table :data="blRegexTips" size="small" border>
          <el-table-column prop="frag" label="正则片段" width="200">
            <template #default="{ row }"><code>{{ row.frag }}</code></template>
          </el-table-column>
          <el-table-column prop="meaning" label="含义" />
        </el-table>
        <h4>三、常用配方</h4>
        <el-table :data="blRecipes" size="small" border>
          <el-table-column prop="re" label="正则" min-width="220">
            <template #default="{ row }"><code>{{ row.re }}</code></template>
          </el-table-column>
          <el-table-column prop="use" label="用途" />
        </el-table>
      </div>
      <template #footer>
        <el-button type="primary" @click="regexHelpVisible = false">我知道了</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtTime, highlightLinks, saveOk } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'
import { usePersistedTab } from '../../composables/usePersistedTab'

const PAGE_SIZE = 15
const blTab = usePersistedTab('blacklist', ['keyword', 'channel', 'file_pattern', 'hits'], 'keyword')
const blItems = ref([])
const blHits = ref([])
const blHitsLoading = ref(false)
const blNewValue = ref('')
const blSearch = ref('')
const blPage = ref(1)
const hitStageFilter = ref('')
const hitPage = ref(1)
const detailVisible = ref(false)
const detailRow = ref(null)
const regexHelpVisible = ref(false)

const blPlaceholderMap = {
  keyword: '标题包含即拦截，如：预告、枪版（可一次粘贴多行批量添加）',
  channel: '频道名，如：some_channel',
  file_pattern: '正则表达式，如：.*\\.txt$',
}
const blPlaceholder = computed(() => blPlaceholderMap[blTab.value] || blPlaceholderMap.keyword)
const blHints = {
  keyword: '消息标题或正文命中关键词 → 整条消息丢弃（含所有链接）；内置已覆盖预告/花絮/枪版/抢先版/解说/广告/引流等，误拦可到「命中记录」放行',
  channel: '频道黑名单 → 该频道的所有消息直接忽略',
  file_pattern: '分享内文件名命中正则 → 该分享整体跳过（转存前检查）',
}
const blHint = computed(() => blHints[blTab.value] || blHints.keyword)
const RULE_TYPE_LABEL = {
  keyword: '关键词', channel: '频道', file_pattern: '文件名正则',
  size_limit: '分享体积下限', category: '分类',
}
const ruleTypeLabel = (t) => RULE_TYPE_LABEL[t] || t || '-'
const stageLabel = (s) => ({ message: '消息级', share_info: '内容级' }[s] || s)
const stageTag = (s) => ({ message: 'info', share_info: 'warning' }[s] || 'info')
const canRemoveRule = (row) => ['keyword', 'channel', 'file_pattern'].includes(row?.rule_type) && !!row?.rule_value

const blRegexTips = [
  { frag: '\\.txt$', meaning: '以 .txt 结尾（$ 表示结尾）' },
  { frag: '\\.(jpg|png)$', meaning: '以 .jpg 或 .png 结尾' },
  { frag: 'sample|proof', meaning: '含 sample 或 proof（样本/截图文件）' },
  { frag: '广告|更多资源', meaning: '含这些字样的文件名' },
]
const blRecipes = [
  { re: '\\.(url|website|lnk)$', use: '拦截推广快捷方式（常见于广告分享）' },
  { re: '^sample|\\.sample$', use: '拦截样本视频（sample 片段）' },
  { re: '推广|广告|加微信|公众号', use: '拦截中文广告命名的文件' },
]

const filteredItems = computed(() => {
  const q = blSearch.value.trim().toLowerCase()
  if (!q) return blItems.value
  return blItems.value.filter((it) => String(it.value || '').toLowerCase().includes(q))
})
const pagedItems = computed(() => {
  const start = (blPage.value - 1) * PAGE_SIZE
  return filteredItems.value.slice(start, start + PAGE_SIZE)
})

const filteredHits = computed(() => {
  if (!hitStageFilter.value) return blHits.value
  return blHits.value.filter((it) => it.stage === hitStageFilter.value)
})
const pagedHits = computed(() => {
  const start = (hitPage.value - 1) * PAGE_SIZE
  return filteredHits.value.slice(start, start + PAGE_SIZE)
})

const detailHtml = computed(() => highlightLinks(detailRow.value?.text || ''))

const loadBlacklist = async () => {
  if (blTab.value === 'hits') {
    blHitsLoading.value = true
    try {
      blHits.value = (await api.get('/api/blacklist/hits')).data
    } catch (e) { ElMessage.error(errMsg(e)) } finally { blHitsLoading.value = false }
  } else {
    try {
      blItems.value = (await api.get(`/api/blacklist/${blTab.value}`)).data
    } catch (e) { ElMessage.error(errMsg(e)) }
  }
}

const onTabChange = () => {
  blSearch.value = ''
  blPage.value = 1
  hitPage.value = 1
  loadBlacklist()
}

const addBlacklistItem = async () => {
  if (!blNewValue.value.trim()) return
  try {
    await api.post(`/api/blacklist/${blTab.value}`, { value: blNewValue.value })
    blNewValue.value = ''
    ElMessage.success('已添加，立即生效')
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const prefillKeyword = (rawText) => {
  blTab.value = 'keyword'
  const firstLine = String(rawText || '').split(/\r?\n/).find((l) => l.trim()) || ''
  blNewValue.value = firstLine.trim().slice(0, 60)
}

const blRestoreDefaults = async () => {
  if (!await confirmAction({
    title: '恢复默认关键词？',
    message: '只补齐缺失项，不会删除你添加的内容。',
    confirmText: '恢复默认',
    type: 'info',
  })) return
  try {
    const { data } = await api.post('/api/blacklist/restore-defaults')
    saveOk('已恢复默认', `已补齐 ${data.added} 条缺失的默认关键词`)
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const delBlacklistItem = async (row) => {
  if (!await confirmAction({
    title: '删除这条黑名单？',
    message: '删除后不再拦截相关内容。',
    confirmText: '删除',
    danger: true,
  })) return
  try {
    await api.delete(`/api/blacklist/${blTab.value}/${row.id}`)
    ElMessage.success('已删除')
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const releaseHit = async (row, removeRule = false) => {
  if (!await confirmAction({
    title: removeRule ? '放行并移除黑名单规则？' : '确认放行这条被拦截的消息？',
    message: removeRule
      ? `将重新处理来自 @${row.channel || '未知频道'} 的消息，并从黑名单删除「${row.rule_value}」。`
      : `将忽略黑名单规则，把来自 @${row.channel || '未知频道'} 的这条消息重新交给转存流程处理。`,
    confirmText: removeRule ? '放行并移除' : '确认放行',
    danger: removeRule,
  })) return false
  try {
    await api.post(`/api/blacklist/hits/${row.id}/release`, null, {
      params: removeRule ? { remove_rule: true } : undefined,
    })
    ElMessage.success(removeRule ? '已放行并移除该黑名单词条' : '已放行，将忽略黑名单并重新处理该消息')
    loadBlacklist()
    return true
  } catch (e) {
    ElMessage.error(errMsg(e))
    return false
  }
}

const openDetail = (row) => { detailRow.value = row; detailVisible.value = true }
const releaseFromDetail = async (removeRule = false) => {
  if (detailRow.value && await releaseHit(detailRow.value, removeRule)) detailVisible.value = false
}

const copyLinks = async () => {
  const text = detailRow.value?.text || ''
  const re = /(https?:\/\/[^\s]+|115:\/\/[^\s]+|ed2k:\/\/\|file\|[^|\s]+\|\d+\|[0-9A-Fa-f]{32}\|\/?|magnet:\?xt=urn:btih:[^\s]+)/gi
  const links = [...new Set(text.match(re) || [])]
  if (!links.length) { ElMessage.warning('该消息中没有资源链接'); return }
  try {
    await navigator.clipboard.writeText(links.join('\n'))
    ElMessage.success(`已复制 ${links.length} 条链接`)
  } catch { ElMessage.warning('复制失败，请手动选择复制') }
}

defineExpose({ prefillKeyword })
onMounted(loadBlacklist)
</script>

<style scoped>
.bl-add-input { flex: 1; min-width: 0; }
.bl-filter-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.bl-count { font-size: var(--fs-caption); color: var(--text-muted); margin-left: auto; }
.bl-pager { display: flex; justify-content: flex-end; margin-top: 10px; }
@media (max-width: 768px) {
  .bl-add-input { flex: 1 0 100% !important; }
  .bl-add-input :deep(.el-textarea__inner) { min-height: calc(3em + 10px) !important; }
}
.hit-summary { cursor: pointer; color: var(--saas-text-sub); font-size: var(--fs-caption); }
.hit-summary:hover { color: var(--el-color-primary); text-decoration: underline; }
.detail-footer { display: flex; justify-content: space-between; align-items: center; width: 100%; gap: 10px; flex-wrap: wrap; }
.detail-content {
  margin-top: 14px; padding: 12px 14px; border-radius: var(--r-md);
  background: var(--subcard-bg); border: 1px solid var(--saas-line);
  font-size: var(--fs-body); line-height: 1.9; color: var(--saas-text-main);
  white-space: pre-wrap; word-break: break-word; max-height: 42vh; overflow-y: auto;
}
</style>
