<template>
  <el-card shadow="never" class="page-card">
    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="关键词" name="keyword" />
      <el-tab-pane label="频道" name="channel" />
      <el-tab-pane label="文件名正则" name="file_pattern" />
      <el-tab-pane label="命中记录" name="hits" />
    </el-tabs>

    <template v-if="tab !== 'hits'">
      <div class="toolbar">
        <el-input v-model="newValue" style="flex: 1" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }"
                  :placeholder="placeholder" />
        <el-button type="primary" @click="add">添加</el-button>
        <el-popconfirm v-if="tab === 'keyword'" title="确认恢复默认关键词？只补齐缺失项，不会删除你添加的内容。"
                       confirm-button-text="恢复默认" cancel-button-text="取消" confirm-button-type="primary"
                       width="270" @confirm="restoreDefaults">
          <template #reference>
            <el-button>恢复默认</el-button>
          </template>
        </el-popconfirm>
        <el-button v-if="tab === 'file_pattern'" @click="regexHelpVisible = true">正则说明</el-button>
      </div>
      <p style="color:#999; font-size:12px; margin-top: 0">
        {{ hint }}
      </p>
      <el-table :data="items" size="small" empty-text="暂无条目">
        <el-table-column prop="value" label="内容" min-width="300" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono" style="font-size:12px">{{ row.value }}</span></template>
        </el-table-column>
        <el-table-column prop="created_at" label="添加时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-popconfirm title="确认删除该条目？删除后不再拦截。" confirm-button-text="删除"
                           cancel-button-text="取消" confirm-button-type="danger" width="220"
                           @confirm="del(row)">
              <template #reference><el-button size="small" type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template v-else>
      <p style="color:#999; font-size:12px; margin-top: 0; margin-bottom: 10px">
        被黑名单拦截的消息记录。点击内容可查看完整消息（分享链接已高亮），确认误拦可放行重新处理。
      </p>
      <el-table :data="hits" size="small" empty-text="暂无拦截记录" v-loading="hitsLoading">
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
            <el-tag size="small" type="danger" effect="plain">{{ row.rule_value || row.rule_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="300">
          <template #default="{ row }">
            <span class="hit-summary" @click="openDetail(row)" :title="row.text">
              {{ (row.text || '（无文本）').slice(0, 60) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openDetail(row)">详情</el-button>
            <el-popconfirm title="确认放行？将忽略黑名单重新处理该消息。" confirm-button-text="放行"
                           cancel-button-text="取消" confirm-button-type="primary" width="240"
                           @confirm="release(row)">
              <template #reference><el-button size="small" type="primary" text>放行</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <!-- 命中详情弹窗 -->
  <el-dialog v-model="detailVisible" title="拦截详情" width="640px" top="6vh">
    <el-descriptions :column="2" border size="small" v-if="detailRow">
      <el-descriptions-item label="时间">{{ fmtTime(detailRow.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="来源频道">
        <span class="mono">@{{ detailRow.channel }}</span> · 消息 #{{ detailRow.message_id }}
      </el-descriptions-item>
      <el-descriptions-item label="拦截环节">{{ stageLabel(detailRow.stage) }}</el-descriptions-item>
      <el-descriptions-item label="命中规则">
        <el-tag size="small" type="danger" effect="plain">
          {{ detailRow.rule_type }}: {{ detailRow.rule_value }}
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>
    <div class="detail-content" v-html="detailHtml"></div>
    <template #footer>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <el-button text @click="copyLinks">复制消息中的资源链接</el-button>
        <div>
          <el-button @click="detailVisible = false">关闭</el-button>
          <el-popconfirm title="确认放行？将忽略黑名单重新处理该消息。" confirm-button-text="放行"
                         cancel-button-text="取消" confirm-button-type="primary" width="240"
                         @confirm="releaseFromDetail">
            <template #reference>
              <el-button type="primary">放行并转存</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </template>
  </el-dialog>

  <!-- 文件名正则帮助 -->
  <el-dialog v-model="regexHelpVisible" title="文件名正则说明" width="780px" top="4vh">
    <div class="help-body">
      <h4>一、作用机制</h4>
      <p>转存前会先读取分享内的<b>文件列表</b>：只要任意一个文件名命中你设置的正则，
        <b>整个分享就会被跳过</b>（记录为"黑名单"状态，可到命中记录里放行）。适合过滤广告文件、样本、垃圾附件等。</p>
      <p class="eg">例：设置 <code>\.url$</code> 后，分享内含有「更多资源点这里.url」这类广告文件的分享会被整体拦截。</p>

      <h4>二、语法要点</h4>
      <el-table :data="regexTips" size="small" border>
        <el-table-column prop="frag" label="正则片段" width="200">
          <template #default="{ row }"><code>{{ row.frag }}</code></template>
        </el-table-column>
        <el-table-column prop="meaning" label="含义" />
      </el-table>

      <h4>三、常用配方</h4>
      <el-table :data="recipes" size="small" border>
        <el-table-column prop="re" label="正则" min-width="220">
          <template #default="{ row }"><code>{{ row.re }}</code></template>
        </el-table-column>
        <el-table-column prop="use" label="用途" />
      </el-table>

      <h4>四、注意事项</h4>
      <ul>
        <li>正则默认<b>忽略大小写</b>；一条正则写错语法不影响其他规则；</li>
        <li>拦截粒度是<b>整个分享</b>——正则越宽松，误杀越多。建议从具体后缀（如 <code>\.url$</code>）起步，观察命中记录后再逐步放宽；</li>
        <li>确认误拦后，到「命中记录」里点「放行」即可重新转存该分享。</li>
      </ul>
    </div>
    <template #footer>
      <el-button type="primary" @click="regexHelpVisible = false">我知道了</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { api, errMsg, fmtTime, highlightLinks } from '../api'

const tab = ref('keyword')
const items = ref([])
const hits = ref([])
const hitsLoading = ref(false)
const newValue = ref('')
const detailVisible = ref(false)
const detailRow = ref(null)
const regexHelpVisible = ref(false)

const placeholderMap = {
  keyword: '标题包含即拦截，如：预告、枪版（可一次粘贴多行批量添加）',
  channel: '频道名，如：some_channel',
  file_pattern: '正则表达式，如：.*\\.txt$',
}
const placeholder = ref(placeholderMap.keyword)
const hints = {
  keyword: '消息标题命中关键词 → 整条消息丢弃（含所有链接）',
  channel: '频道黑名单 → 该频道的所有消息直接忽略',
  file_pattern: '分享内文件名命中正则 → 该分享整体跳过（转存前检查）',
}
const hint = ref(hints.keyword)

const regexTips = [
  { frag: '\\.txt$', meaning: '以 .txt 结尾（$ 表示结尾）', },
  { frag: '\\.(jpg|png)$', meaning: '以 .jpg 或 .png 结尾', },
  { frag: 'sample|proof', meaning: '含 sample 或 proof（样本/截图文件）', },
  { frag: '(?i)', meaning: '忽略大小写（系统已默认忽略）', },
  { frag: '广告|更多资源', meaning: '含这些字样的文件名', },
]
const recipes = [
  { re: '\\.(url|website|lnk)$', use: '拦截推广快捷方式（常见于广告分享）' },
  { re: '\\.(txt|nfo|doc|pdf)$', use: '拦截说明文档类小文件（谨慎：正规分享也可能带说明文件）' },
  { re: '^sample|\\.sample$', use: '拦截样本视频（sample 片段）' },
  { re: '推广|广告|加微信|公众号', use: '拦截中文广告命名的文件' },
]

const stageLabel = (s) => ({ message: '消息级', share_info: '内容级' }[s] || s)
const stageTag = (s) => ({ message: 'info', share_info: 'warning' }[s] || 'info')

const detailHtml = computed(() => highlightLinks(detailRow.value?.text || ''))

const load = async () => {
  if (tab.value === 'hits') {
    hitsLoading.value = true
    try {
      hits.value = (await api.get('/api/blacklist/hits')).data
    } finally {
      hitsLoading.value = false
    }
  } else {
    items.value = (await api.get(`/api/blacklist/${tab.value}`)).data
    placeholder.value = placeholderMap[tab.value]
    hint.value = hints[tab.value]
  }
}
const add = async () => {
  if (!newValue.value.trim()) return
  try {
    await api.post(`/api/blacklist/${tab.value}`, { value: newValue.value })
    newValue.value = ''
    ElMessage.success('已添加，立即生效')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const restoreDefaults = async () => {
  try {
    const { data } = await api.post('/api/blacklist/restore-defaults')
    ElNotification.success({ title: '已恢复默认', message: `补齐了 ${data.added} 条缺失的默认关键词` })
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const del = async (row) => {
  try {
    await api.delete(`/api/blacklist/${tab.value}/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const release = async (row) => {
  try {
    await api.post(`/api/blacklist/hits/${row.id}/release`)
    ElNotification.success({ title: '已放行', message: '已忽略黑名单并重新处理该消息' })
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const openDetail = (row) => {
  detailRow.value = row
  detailVisible.value = true
}
const releaseFromDetail = async () => {
  if (!detailRow.value) return
  await release(detailRow.value)
  detailVisible.value = false
}
const copyLinks = async () => {
  const text = detailRow.value?.text || ''
  const re = /(https?:\/\/[^\s]+|115:\/\/[^\s]+|ed2k:\/\/\|file\|[^|\s]+\|\d+\|[0-9A-Fa-f]{32}\|\/?|magnet:\?xt=urn:btih:[^\s]+)/gi
  const links = [...new Set(text.match(re) || [])]
  if (!links.length) {
    ElMessage.warning('该消息中没有资源链接')
    return
  }
  try {
    await navigator.clipboard.writeText(links.join('\n'))
    ElMessage.success(`已复制 ${links.length} 条链接`)
  } catch {
    ElMessage.error('复制失败，请手动选择复制')
  }
}
onMounted(load)
</script>

<style scoped>
.hit-summary { cursor: pointer; color: #3a4661; font-size: 12px; }
.hit-summary:hover { color: var(--el-color-primary); text-decoration: underline; }
.detail-content {
  margin-top: 14px; padding: 12px 14px; border-radius: 10px;
  background: #f6f9ff; border: 1px solid #e9eefb;
  font-size: 13px; line-height: 1.9; color: #3a4661;
  white-space: pre-wrap; word-break: break-word;
  max-height: 42vh; overflow-y: auto;
}
</style>
