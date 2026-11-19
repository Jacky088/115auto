<template>
  <el-dialog
    :model-value="modelValue"
    title="115 回收站"
    :width="'min(780px, 94vw)'"
    top="5vh"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="rb-head">
      <span class="rb-tip">支持单个或批量还原回原目录（防误删不提供彻底清空，如需清空请前往 115 客户端）。</span>
      <div class="rb-actions">
        <el-button
          v-if="rbSelected.length"
          type="primary"
          :loading="rbReverting"
          @click="revertRecycleItems(rbSelected)"
        >
          <el-icon><RefreshLeft /></el-icon>
          <span>还原已选 ({{ rbSelected.length }})</span>
        </el-button>
        <el-button :loading="rbLoading" @click="loadRecycleBin">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="rbError" :title="rbError" type="error" show-icon :closable="false" class="load-alert" />

    <el-table
      :data="rbItems"
      size="small"
      v-loading="rbLoading"
      row-key="id"
      max-height="420"
      @selection-change="onSelectionChange"
    >
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
      <el-pagination
        layout="prev, pager, next"
        :total="rbTotal"
        :page-size="rbSize"
        v-model:current-page="rbPage"
        @current-change="loadRecycleBin"
      />
    </div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { RefreshLeft } from '@element-plus/icons-vue'
import { api, errMsg, fmtSize, fmtTime, saveOk } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'
import { rowIconClass, rowIconName } from './fileIcons'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'recovered'])

const rbLoading = ref(false)
const rbReverting = ref(false)
const rbError = ref('')
const rbItems = ref([])
const rbTotal = ref(0)
const rbPage = ref(1)
const rbSize = ref(50)
const rbSelected = ref([])

const onSelectionChange = (rows) => {
  rbSelected.value = rows
}

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

const revertRecycleItems = async (rows) => {
  if (!rows || !rows.length) return
  const ok = await confirmAction({
    title: `还原选中的 ${rows.length} 项内容？`,
    message: '将还原回删除前的原目录。',
    confirmText: '还原',
    type: 'primary',
  })
  if (!ok) return

  const ids = rows.map((r) => String(r.id)).filter(Boolean)
  rbReverting.value = true
  try {
    const { data } = await api.post('/api/files/recyclebin/revert', { ids })
    saveOk('已还原', `${data.count || ids.length} 项已恢复至原目录`)
    loadRecycleBin()
    emit('recovered')
  } catch (e) {
    ElMessage.error(errMsg(e, '还原失败'))
  } finally {
    rbReverting.value = false
  }
}

watch(() => props.modelValue, (val) => {
  if (val) {
    rbPage.value = 1
    loadRecycleBin()
  }
})
</script>
