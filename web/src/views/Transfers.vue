<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="card-head">
        <div class="title-with-badge">
          <span class="card-title">转存归档</span>
          <el-tag size="small" class="count-tag">
            {{ activeTab === 'records' ? `共 ${total} 条转存` : `共 ${ledgerTotal} 条整理` }}
          </el-tag>
        </div>
      </div>
    </template>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- ============ Tab 1：转存记录（原有列表，原样保留） ============ -->
      <el-tab-pane label="转存记录" name="records">
        <div class="toolbar">
          <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px" @change="loadFromFirstPage">
            <el-option v-for="(v, k) in statusLabel" :key="k" :label="v" :value="k" />
          </el-select>
          <el-input v-model="filter.search" placeholder="搜索标题 / 分享码" clearable style="width: 260px"
                    @keyup.enter="loadFromFirstPage" @clear="loadFromFirstPage">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button @click="loadFromFirstPage">查询</el-button>
          <el-button type="danger" plain :disabled="!selected.length" @click="delSelected">
            批量删除{{ selected.length ? `（${selected.length}）` : '' }}
          </el-button>
          <el-popconfirm title="确认重试全部失败任务？将重新入队。" confirm-button-text="重试"
                         cancel-button-text="取消" confirm-button-type="primary" width="240"
                         @confirm="retryAll">
            <template #reference>
              <el-button type="warning" plain :loading="retrying">批量重试失败任务</el-button>
            </template>
          </el-popconfirm>
        </div>

        <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon
                  style="margin-bottom: 10px" />

        <el-table ref="tableRef" :data="items" size="small" v-loading="loading"
                  @selection-change="onSelect">
          <template #empty>
            <el-empty :image-size="60" description="暂无转存记录" />
          </template>
          <el-table-column type="selection" width="42" :selectable="canDelete" />
          <el-table-column prop="created_at" label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">
              <span>{{ row.title || row.share_code }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="channel" label="来源" width="120" />
          <el-table-column prop="category" label="分类" width="85" />
          <el-table-column prop="kind" label="类型" width="80">
            <template #default="{ row }">
              <el-tag :type="row.kind === 'offline' ? 'warning'
                              : (row.priority > 0 ? 'danger' : 'primary')" size="small">
                {{ row.kind === 'offline' ? '云下载'
                   : (row.priority > 0 ? '补全' : (row.increment ? '追更' : '分享')) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="target_dir" label="目标目录" width="150" show-overflow-tooltip />
          <el-table-column prop="size" label="大小" width="85">
            <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
          </el-table-column>
          <el-table-column prop="file_count" label="文件数" width="75" />
          <el-table-column prop="attempts" label="重试" width="60" />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tooltip :content="row.error" :disabled="!row.error">
                <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel[row.status] || row.status }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <div class="table-actions">
                <el-button v-if="['failed', 'skipped', 'blocked'].includes(row.status)"
                           type="primary" text @click="retry(row)">重试</el-button>
                <el-button v-if="row.status === 'success' && row.kind === 'share'
                                && row.channel && row.message_id"
                           type="primary" text @click="openBackfill(row)">补全</el-button>
                <el-popconfirm v-if="['queued', 'submitted', 'failed', 'skipped', 'duplicate'].includes(row.status)"
                               title="确认删除该任务记录？"
                               confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                               width="220" @confirm="del(row)">
                  <template #reference><el-button type="danger" text>删除</el-button></template>
                </el-popconfirm>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <BackfillDialog ref="backfillDialogRef" />

        <el-pagination style="margin-top: 14px; justify-content: flex-end" layout="total, sizes, prev, pager, next"
                       :total="total" :page-size="size" :page-sizes="[20, 50, 100]"
                       v-model:current-page="page" v-model:page-size="size"
                       @current-change="load" @size-change="onSizeChange" />
      </el-tab-pane>

      <!-- ============ Tab 2：整理记录（定时文件整理的移动账本） ============ -->
      <el-tab-pane label="整理记录" name="ledger">
        <div class="toolbar">
          <el-select v-model="ledgerFilter.undone" placeholder="状态" clearable style="width: 130px"
                     @change="loadLedgerFromFirstPage">
            <el-option label="已归档" value="0" />
            <el-option label="已撤销" value="1" />
          </el-select>
          <el-select v-model="ledgerFilter.rule_id" placeholder="整理规则" clearable style="width: 200px"
                     @change="loadLedgerFromFirstPage">
            <el-option v-for="r in ledgerRules" :key="r.id" :value="r.id"
                       :label="`${r.source_dir} ➔ ${r.target_dir}`" />
          </el-select>
          <el-select v-model="ledgerFilter.batch_id" placeholder="批次" clearable style="width: 210px"
                     @change="loadLedgerFromFirstPage">
            <el-option v-for="b in ledgerBatches" :key="b.batch_id" :value="b.batch_id"
                       :label="`${fmtTime(b.t0)} · ${b.c} 个文件`" />
          </el-select>
          <el-input v-model="ledgerFilter.keyword" placeholder="搜索文件名 / 目标路径" clearable style="width: 220px"
                    @keyup.enter="loadLedgerFromFirstPage" @clear="loadLedgerFromFirstPage">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button @click="loadLedgerFromFirstPage">查询</el-button>
          <el-popconfirm v-if="ledgerFilter.batch_id && ledgerBatchAlive > 0"
                         title="确认撤销本批次全部已归档文件？文件将移回整理前的源目录。"
                         confirm-button-text="撤销本批" cancel-button-text="取消" confirm-button-type="danger"
                         width="300" @confirm="openUndoBatch">
            <template #reference>
              <el-button type="danger" plain>撤销本批{{ ledgerBatchAlive ? `（${ledgerBatchAlive}）` : '' }}</el-button>
            </template>
          </el-popconfirm>
        </div>

        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 10px"
                  title="整理记录为定时文件整理的实际移动流水（每完成一批移动即记录一条）；撤销会把文件移回整理前的源目录。" />

        <el-table :data="ledgerItems" size="small" v-loading="ledgerLoading">
          <template #empty>
            <el-empty :image-size="60" description="暂无整理记录（账本自功能上线起记录）" />
          </template>
          <el-table-column prop="created_at" label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="orig_name" label="文件名" min-width="200" show-overflow-tooltip />
          <el-table-column prop="orig_path" label="源位置" width="160" show-overflow-tooltip />
          <el-table-column prop="dest_path" label="目标位置" min-width="220" show-overflow-tooltip />
          <el-table-column prop="rule_label" label="规则" width="170" show-overflow-tooltip />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tooltip :content="row.undo_note" :disabled="!row.undo_note">
                <el-tag v-if="row.undone === 1" type="info" size="small">已撤销</el-tag>
                <el-tag v-else-if="row.undo_note" type="warning" size="small">需人工核对</el-tag>
                <el-tag v-else type="success" size="small">已归档</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.undone === 0" type="danger" text @click="openUndoOne(row)">撤销</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination style="margin-top: 14px; justify-content: flex-end" layout="total, sizes, prev, pager, next"
                       :total="ledgerTotal" :page-size="ledgerSize" :page-sizes="[20, 50, 100]"
                       v-model:current-page="ledgerPage" v-model:page-size="ledgerSize"
                       @current-change="loadLedger" @size-change="onLedgerSizeChange" />

        <!-- 撤销确认弹窗：说明「再移走」后果，可选停用涉及规则 -->
        <el-dialog v-model="undoDialog.visible" title="撤销整理" width="480px">
          <p style="margin: 0 0 8px">
            将撤销 <b>{{ undoDialog.label }}</b>，文件会被移回整理前的源目录。
          </p>
          <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 10px"
                    title="撤销后，若规则仍启用且文件仍命中匹配条件，下次定时整理会把它再次移走。" />
          <el-checkbox v-model="undoDialog.stopRules">
            同时停用涉及的整理规则（{{ undoDialog.ruleLabels || '无' }}）
          </el-checkbox>
          <template #footer>
            <el-button @click="undoDialog.visible = false">取消</el-button>
            <el-button type="danger" :loading="undoDialog.loading" @click="doUndo">确认撤销</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api, errMsg, fmtSize, fmtTime, saveOk, statusLabel } from '../api'
import BackfillDialog from '../components/BackfillDialog.vue'

const isMobile = ref(window.innerWidth <= 768)
const onRz = () => { isMobile.value = window.innerWidth <= 768 }
onMounted(() => window.addEventListener('resize', onRz))
onUnmounted(() => window.removeEventListener('resize', onRz))

const activeTab = ref('records')
const onTabChange = (tab) => {
  // 整理记录懒加载：首次切到该 Tab 时才请求账本与规则列表
  if (tab === 'ledger' && !ledgerLoaded.value) {
    loadLedger()
    loadLedgerRules()
  }
}

// ---- Tab 1：转存记录（原有逻辑，保持不变） ----
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)  // 每页条数：默认 50，可在分页器里切换（20/50/100）
const loading = ref(false)
const retrying = ref(false)
const filter = reactive({ status: '', search: '' })
const tableRef = ref(null)
const selected = ref([])
const canDelete = (row) => !['running', 'success'].includes(row.status)

const statusTag = (s) => ({ success: 'success', failed: 'danger', blocked: 'warning',
                            queued: 'info', running: 'primary', duplicate: 'info', skipped: 'info' }[s] || 'info')

const loadError = ref('')
let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.get('/api/transfers', {
      params: { status: filter.status, search: filter.search, page: page.value, size: size.value },
    })
    if (seq !== loadSeq) return  // 已有更新的请求：丢弃过期响应，防止快速筛选时旧页覆盖新页
    items.value = data.items
    total.value = data.total
  } catch (e) {
    if (seq === loadSeq) loadError.value = errMsg(e)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// 筛选/搜索条件变化：重置回第 1 页，避免停在全空页
const loadFromFirstPage = () => { page.value = 1; load() }

// 切换每页条数：回到第 1 页并重新加载
const onSizeChange = () => { page.value = 1; load() }
const retryAll = async () => {
  retrying.value = true
  try {
    const { data } = await api.post('/api/transfers/retry-failed')
    if (data.count > 0) {
      saveOk('批量重试成功', `${data.count} 个失败任务已重新入队`)
    } else {
      ElMessage.info('当前没有失败任务')
    }
    load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { retrying.value = false }
}
const onSelect = (rows) => { selected.value = rows }
const delSelected = async () => {
  const n = selected.value.length
  if (!n) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${n} 条任务记录？删除后仪表盘的最近转存记录会同步移除。`,
      '批量删除', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }  // 用户取消
  try {
    const { data } = await api.post('/api/transfers/batch-delete',
      { ids: selected.value.map(r => r.id) })
    if (data.skipped) {
      ElMessage.warning(`已删除 ${data.deleted} 条；${data.skipped} 条因正在转存/已成功被跳过`)
    } else {
      ElMessage.success(`已删除 ${data.deleted} 条任务记录`)
    }
    tableRef.value?.clearSelection()
    selected.value = []
    // 当前页被删空时回到上一页，避免停在空白页
    if (items.value.length <= n && page.value > 1) page.value -= 1
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const retry = async (row) => {
  try {
    await api.post(`/api/transfers/${row.id}/retry`)
    ElMessage.success('已重新入队')
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
// ---- 向前补全：通过统一 BackfillDialog 组件处理 ----
const backfillDialogRef = ref(null)
const openBackfill = (row) => backfillDialogRef.value?.openForTransfer(row)

const del = async (row) => {
  try {
    await api.delete(`/api/transfers/${row.id}`)
    ElMessage.success('任务记录已删除')
    tableRef.value?.clearSelection()
    selected.value = []
    if (items.value.length <= 1 && page.value > 1) page.value -= 1
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

// ---- Tab 2：整理记录（定时文件整理移动账本） ----
const ledgerLoaded = ref(false)
const ledgerLoading = ref(false)
const ledgerItems = ref([])
const ledgerTotal = ref(0)
const ledgerPage = ref(1)
const ledgerSize = ref(50)
const ledgerFilter = reactive({ undone: '', rule_id: null, batch_id: '', keyword: '' })
const ledgerRules = ref([])
const ledgerBatches = ref([])

const ledgerBatchAlive = computed(() => {
  const b = ledgerBatches.value.find((x) => x.batch_id === ledgerFilter.batch_id)
  return b ? Number(b.alive || 0) : 0
})

const loadLedgerRules = async () => {
  try {
    const { data } = await api.get('/api/auto-move/rules')
    ledgerRules.value = data.rules || []
  } catch { /* 规则列表加载失败不阻塞账本 */ }
}

let ledgerSeq = 0
const loadLedger = async () => {
  const seq = ++ledgerSeq
  ledgerLoading.value = true
  try {
    const { data } = await api.get('/api/auto-move/records', {
      params: {
        undone: ledgerFilter.undone, rule_id: ledgerFilter.rule_id || 0,
        batch_id: ledgerFilter.batch_id, keyword: ledgerFilter.keyword,
        page: ledgerPage.value, size: ledgerSize.value,
      },
    })
    if (seq !== ledgerSeq) return
    ledgerItems.value = data.items
    ledgerTotal.value = data.total
    ledgerBatches.value = data.batches || []
  } catch (e) {
    if (seq === ledgerSeq) ElMessage.error(errMsg(e))
  } finally {
    if (seq === ledgerSeq) ledgerLoading.value = false
  }
  ledgerLoaded.value = true
}
const loadLedgerFromFirstPage = () => { ledgerPage.value = 1; loadLedger() }
const onLedgerSizeChange = () => { ledgerPage.value = 1; loadLedger() }

// ---- 撤销（单条 / 整批），可选停用涉及规则 ----
const undoDialog = reactive({
  visible: false, loading: false, label: '', ruleLabels: '',
  record_id: 0, batch_id: '', stopRules: false,
})
const openUndoOne = (row) => {
  undoDialog.record_id = row.id
  undoDialog.batch_id = ''
  undoDialog.label = `「${row.orig_name}」`
  undoDialog.ruleLabels = row.rule_label || ''
  undoDialog.stopRules = false
  undoDialog.visible = true
}
const openUndoBatch = () => {
  const b = ledgerBatches.value.find((x) => x.batch_id === ledgerFilter.batch_id)
  if (!b) return
  undoDialog.record_id = 0
  undoDialog.batch_id = b.batch_id
  undoDialog.label = `本批次（${b.c} 条记录）`
  const ids = String(b.rule_ids || '').split(',').filter((x) => x && x !== '0').map(Number)
  undoDialog.ruleLabels = ids
    .map((id) => ledgerRules.value.find((r) => r.id === id))
    .filter(Boolean)
    .map((r) => `${r.source_dir} ➔ ${r.target_dir}`)
    .join('；') || '（规则已删除）'
  undoDialog.stopRules = false
  undoDialog.visible = true
}
const doUndo = async () => {
  undoDialog.loading = true
  try {
    const { data } = await api.post('/api/auto-move/records/undo', {
      record_id: undoDialog.record_id || undefined,
      batch_id: undoDialog.batch_id || undefined,
      stop_rule_ids: undoDialog.stopRules ? currentStopRuleIds() : [],
    })
    const parts = [`恢复 ${data.undone} 条`]
    if (data.failed) parts.push(`失败 ${data.failed} 条`)
    if (data.skipped?.length) parts.push(`跳过 ${data.skipped.length} 条（${data.skipped[0].reason}）`)
    if (data.disabled_rules?.length) parts.push(`已停用 ${data.disabled_rules.length} 条规则`)
    if (data.undone > 0) saveOk('撤销完成', parts.join('，'))
    else ElMessage.warning(parts.join('，') || '没有可撤销的记录')
    undoDialog.visible = false
    loadLedger()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { undoDialog.loading = false }
}
// 停用规则 id 取当前撤销目标的涉及规则（单条=该行规则；整批=批次聚合的 rule_ids）
const currentStopRuleIds = () => {
  if (undoDialog.record_id) {
    const row = ledgerItems.value.find((x) => x.id === undoDialog.record_id)
    return row ? [row.task_id] : []
  }
  const b = ledgerBatches.value.find((x) => x.batch_id === undoDialog.batch_id)
  return String(b?.rule_ids || '').split(',').filter((x) => x && x !== '0').map(Number)
}

onMounted(load)
</script>

<style scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.title-with-badge { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-title { font-weight: 700; font-size: var(--fs-title); color: var(--saas-text-main); }
</style>
