<template>
  <div>
    <!-- 匹配规则 -->
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <div style="display: flex; align-items: center; gap: 8px">
            <span>匹配规则</span>
            <el-tag size="small" class="count-tag">共 {{ rules.length }} 条规则</el-tag>
          </div>
          <el-tag v-if="dirtyRules" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
          <el-button v-if="dirtyRules" type="primary" size="small" @click="saveRules">保存规则</el-button>
          <el-button @click="renumberRules">重整序号</el-button>
          <el-button @click="resetRules">恢复默认</el-button>
          <el-button type="primary" plain @click="helpVisible = true">使用帮助</el-button>
          <el-dropdown @command="insertTemplate">
            <el-button>
              常用模板 <el-icon style="margin-left: 4px"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="(tpl, idx) in RULE_TEMPLATES" :key="idx" :command="tpl">
                  {{ tpl.label }}（{{ tpl.category }}）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button type="primary" @click="addRule()">添加规则</el-button>
        </div>
      </template>

      <div class="rules-filter-row">
        <el-select v-model="catFilter" size="small" style="width: 140px">
          <el-option label="全部分类" value="" />
          <el-option v-for="c in ruleCategories" :key="c" :label="c" :value="c" />
        </el-select>
        <el-input
          v-model="ruleSearch"
          size="small"
          clearable
          placeholder="搜索正则或说明…"
          style="width: 240px; max-width: 100%"
        />
        <span class="tip" style="margin-left: auto">
          数字越小越先匹配；消息里的 #电影/#剧集 标签优先于正则
        </span>
      </div>

      <el-table :data="filteredRules" size="small">
        <el-table-column label="优先级" width="136">
          <template #default="{ row }">
            <el-input-number
              v-model="row.priority"
              :min="1"
              :max="9999"
              :step="1"
              :step-strictly="true"
              :precision="0"
              style="width: 116px"
            />
          </template>
        </el-table-column>
        <el-table-column label="正则" min-width="250">
          <template #default="{ row }">
            <el-input v-model="row.pattern" class="mono" placeholder="正则表达式" />
            <div v-if="regexError(row.pattern)" class="regex-err">{{ regexError(row.pattern) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="150">
          <template #default="{ row }">
            <el-input v-model="row.note" maxlength="120" placeholder="可选中文说明" />
          </template>
        </el-table-column>
        <el-table-column label="分类" width="140">
          <template #default="{ row }">
            <el-select v-model="row.category" filterable allow-create>
              <el-option v-for="r in routes" :key="r.category" :label="r.category" :value="r.category" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="68">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" :active-value="1" :inactive-value="0" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="198" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button text size="small" :disabled="isFirst(row)" @click="moveRule(row, -1)">上移</el-button>
              <el-button text size="small" :disabled="isLast(row)" @click="moveRule(row, 1)">下移</el-button>
              <el-button type="danger" text @click="removeRule(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="btn-row end" style="margin-top: 14px">
        <span v-if="dirtyRules" class="tip-text">修改需点击「保存规则」后生效</span>
        <el-button type="primary" :disabled="!dirtyRules" @click="saveRules">保存规则</el-button>
      </div>
    </el-card>

    <RuleSandboxCard @create-rule="createRuleFromText" @add-blacklist="addBlacklistFromText" />
    <BlacklistCard ref="blacklistCardRef" />

    <!-- 使用帮助 -->
    <el-dialog v-model="helpVisible" title="匹配规则使用帮助" :width="'min(780px, calc(100vw - 36px))'" align-center>
      <div class="help-body">
        <h4>一、工作原理</h4>
        <ol>
          <li><b>消息去重 &amp; 黑名单</b>：同一消息只处理一次；命中频道/关键词黑名单直接拦截；</li>
          <li><b>分类判定</b>：消息自带标签（#电影 / #剧集 等）优先；无标签时按优先级从小到大逐条匹配正则，<b>首条命中生效</b>；</li>
          <li><b>目录路由</b>：按分类查找「转存整理」页配置（分享转存与云下载独立配置）。</li>
        </ol>
        <h4>二、常用正则参考</h4>
        <el-table :data="regexTips" size="small" border>
          <el-table-column prop="frag" label="正则片段" width="180">
            <template #default="{ row }"><code>{{ row.frag }}</code></template>
          </el-table-column>
          <el-table-column prop="meaning" label="含义" />
          <el-table-column prop="demo" label="示例" width="200" />
        </el-table>
      </div>
      <template #footer>
        <el-button type="primary" @click="helpVisible = false">我知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { api, errMsg, saveOk } from '../api'
import { confirmAction } from '../composables/confirmDialog'
import { useUnsavedGuard } from '../composables/unsavedGuard'
import BlacklistCard from '../components/rules/BlacklistCard.vue'
import RuleSandboxCard from '../components/rules/RuleSandboxCard.vue'

const RULE_TEMPLATES = [
  { label: '微短剧 / 短剧', pattern: '(短剧|微短剧|精品短剧)', category: '电视剧', note: '短剧识别' },
  { label: '字幕组标签（示例）', pattern: '^\\[某字幕组\\]', category: '动漫', note: '指定字幕组归动漫' },
  { label: '4K / 杜比视界电影', pattern: '(2160p|4K).*(DV|Dolby|REMUX)', category: '电影', note: '高规格电影' },
  { label: '纪录片厂牌', pattern: '(BBC|NHK|Discovery|国家地理)', category: '纪录片', note: '纪录片厂牌' },
]

const regexTips = [
  { frag: '第\\d+集', meaning: '"第"+若干数字+"集"', demo: '第25集' },
  { frag: '[Ss]\\d{2}[Ee]\\d{2}', meaning: '季集格式', demo: 'S01E05' },
  { frag: '\\[组名\\]', meaning: '方括号字面量（[]需转义）', demo: '[某字幕组]' },
  { frag: 'a|b', meaning: '或', demo: '短剧|微短剧' },
]

const rules = ref([])
const routes = ref([])
const dirtyRules = ref(false)
const helpVisible = ref(false)
const catFilter = ref('')
const ruleSearch = ref('')
const blacklistCardRef = ref(null)
let suppressWatch = false

const ruleCategories = computed(() => {
  const set = new Set(routes.value.map((r) => r.category).filter(Boolean))
  rules.value.forEach((r) => { if (r.category) set.add(r.category) })
  return [...set]
})

const filteredRules = computed(() => {
  const q = ruleSearch.value.trim().toLowerCase()
  return rules.value.filter((r) => {
    if (catFilter.value && r.category !== catFilter.value) return false
    if (q && !String(r.pattern || '').toLowerCase().includes(q) && !String(r.note || '').toLowerCase().includes(q)) {
      return false
    }
    return true
  })
})

const regexError = (pat) => {
  const s = String(pat || '').trim()
  if (!s) return ''
  try {
    new RegExp(s, 'i')
    return ''
  } catch (e) {
    return `正则语法错误：${e.message}`
  }
}

watch(rules, () => {
  if (!suppressWatch) dirtyRules.value = true
}, { deep: true })

const loadRules = async () => {
  try {
    const { data } = await api.get('/api/rules')
    suppressWatch = true
    rules.value = (data.rules || []).map((r) => ({ ...r, note: r.note || '' }))
    routes.value = data.routes || []
    await nextTick()
    suppressWatch = false
    dirtyRules.value = false
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const isFirst = (row) => rules.value.indexOf(row) <= 0
const isLast = (row) => {
  const idx = rules.value.indexOf(row)
  return idx < 0 || idx >= rules.value.length - 1
}

const moveRule = (row, delta) => {
  const idx = rules.value.indexOf(row)
  const target = idx + delta
  if (idx < 0 || target < 0 || target >= rules.value.length) return
  const list = [...rules.value]
  ;[list[idx], list[target]] = [list[target], list[idx]]
  list.forEach((r, i) => { r.priority = (i + 1) * 10 })
  rules.value = list
  dirtyRules.value = true
}

const renumberRules = () => {
  const list = [...rules.value].sort((a, b) => (Number(a.priority) || 0) - (Number(b.priority) || 0))
  list.forEach((r, i) => { r.priority = (i + 1) * 10 })
  rules.value = list
  dirtyRules.value = true
  ElMessage.success('已按优先级重排并整理为 10 间隔序号（记得保存规则）')
}

const removeRule = async (row) => {
  if (!await confirmAction({
    title: '删除这条规则？',
    message: '保存后立即生效。',
    confirmText: '删除',
    danger: true,
  })) return
  const idx = rules.value.indexOf(row)
  if (idx >= 0) rules.value.splice(idx, 1)
  dirtyRules.value = true
}

const addRule = (preset = {}) => {
  const maxPri = rules.value.reduce((m, r) => Math.max(m, Number(r.priority) || 0), 0)
  rules.value.push({
    pattern: preset.pattern || '',
    category: preset.category || '',
    note: preset.note || '',
    priority: maxPri ? maxPri + 10 : 50,
    enabled: 1,
  })
  dirtyRules.value = true
}

const insertTemplate = (tpl) => addRule(tpl)
const createRuleFromText = (text) => {
  const firstLine = String(text || '').split(/\r?\n/).find((l) => l.trim()) || ''
  const escaped = firstLine.trim().slice(0, 40).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  addRule({ pattern: escaped, note: '由测试沙盒生成' })
  ElMessage.success('已添加到规则列表末尾，请补充分类并保存')
}
const addBlacklistFromText = (text) => blacklistCardRef.value?.prefillKeyword(text)

const saveRules = async () => {
  for (let i = 0; i < rules.value.length; i++) {
    const r = rules.value[i]
    const p = Number(r.priority)
    if (!Number.isInteger(p) || p < 1) {
      ElMessage.warning(`第 ${i + 1} 条规则的优先级必须是大于等于 1 的整数`)
      return
    }
    if (!r.pattern?.trim() || !r.category?.trim()) {
      ElMessage.warning(`第 ${i + 1} 条规则的正则与分类不能为空`)
      return
    }
    if (regexError(r.pattern)) {
      ElMessage.warning(`第 ${i + 1} 条规则的${regexError(r.pattern)}`)
      return
    }
  }
  try {
    await api.put('/api/rules', { rules: rules.value })
    dirtyRules.value = false
    saveOk('规则已保存', '匹配规则更改已生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const resetRules = async () => {
  if (!await confirmAction({
    title: '恢复默认规则？',
    message: '自定义规则将被覆盖，目录映射保留。',
    confirmText: '恢复默认',
    danger: true,
  })) return
  try {
    const { data } = await api.post('/api/rules/reset')
    await loadRules()
    saveOk('已恢复默认', `已载入 ${data.count} 条最新内置规则，缺失的默认分类目录已补齐`)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

useUnsavedGuard(dirtyRules)
onMounted(loadRules)
</script>

<style scoped>
.rules-filter-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.tip { color: var(--saas-text-sub); font-size: var(--fs-caption); }
.tip-text { color: #e6a23c; font-size: var(--fs-caption); }
.regex-err { color: var(--el-color-danger); font-size: var(--fs-caption); margin-top: 3px; }
</style>
