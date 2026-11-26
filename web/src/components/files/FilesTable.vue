<template>
  <div>
    <el-table
      ref="tableRef"
      :data="items"
      size="small"
      v-loading="loading"
      row-key="id"
      @selection-change="$emit('selection-change', $event)"
      @row-dblclick="$emit('row-dblclick', $event)"
      @sort-change="$emit('sort-change', $event)"
    >
      <template #empty>
        <el-empty v-if="!loading" :image-size="60" :description="emptyDesc" />
      </template>
      <el-table-column type="selection" width="44" />
      <el-table-column prop="name" label="名称" min-width="280" sortable="custom">
        <template #default="{ row }">
          <div
            class="file-name"
            role="button"
            tabindex="0"
            :aria-label="(row.is_dir ? '打开目录 ' : '选择文件 ') + row.name"
            @click="$emit('name-click', row)"
            @keydown.enter="$emit('name-click', row)"
            @keydown.space.prevent="$emit('name-click', row)"
          >
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
              <button
                class="dir-size-btn"
                :disabled="!!dirStatLoading[row.id]"
                @click.stop="$emit('calc-dir-stat', row, true)"
              >
                <el-icon v-if="dirStatLoading[row.id]" class="is-loading"><Loading /></el-icon>
                <span v-else>{{ formatDirStatSize(dirStats[row.id]) }}</span>
              </button>
            </el-tooltip>
          </template>
          <el-button
            v-else
            text
            type="primary"
            size="small"
            class="stat-calc-btn"
            :loading="!!dirStatLoading[row.id]"
            @click.stop="$emit('calc-dir-stat', row, false)"
          >
            统计大小
          </el-button>
        </template>
      </el-table-column>
      <el-table-column prop="mtime" label="修改时间" width="155" sortable="custom">
        <template #default="{ row }">{{ row.mtime ? fmtTime(row.mtime) : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="185" fixed="right">
        <template #default="{ row }">
          <div class="table-actions">
            <el-button v-if="row.is_dir" text type="primary" @click="$emit('open-dir', row)">打开</el-button>
            <el-button text @click="$emit('show-detail', row)">详情</el-button>
            <el-dropdown trigger="click" @command="$emit('row-command', $event, row)">
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
      <el-pagination
        layout="total, sizes, prev, pager, next"
        :total="total"
        :page-size="size"
        :page-sizes="[20, 50, 100]"
        :current-page="page"
        @current-change="onCurrentChange"
        @size-change="$emit('size-change', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Loading, MoreFilled } from '@element-plus/icons-vue'
import { fmtSize, fmtTime } from '../../api'
import { formatDirStatSize, rowIconClass, rowIconName } from './fileIcons'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  emptyDesc: { type: String, default: '当前目录为空' },
  dirStats: { type: Object, default: () => ({}) },
  dirStatLoading: { type: Object, default: () => ({}) },
  isSearching: { type: Boolean, default: false },
  searchScope: { type: String, default: 'all' },
  cid: { type: String, default: '0' },
  total: { type: Number, default: 0 },
  folderCount: { type: Number, default: null },
  fileCount: { type: Number, default: null },
  clipboard: { type: Object, default: null },
  page: { type: Number, default: 1 },
  size: { type: Number, default: 50 },
})

const emit = defineEmits([
  'selection-change',
  'row-dblclick',
  'sort-change',
  'name-click',
  'open-dir',
  'show-detail',
  'row-command',
  'calc-dir-stat',
  'update:page',
  'page-change',
  'size-change',
])

const onCurrentChange = (val) => {
  emit('update:page', val)
  emit('page-change', val)
}

const tableRef = ref(null)

defineExpose({
  sort: (prop, order) => tableRef.value?.sort(prop, order),
  clearSort: () => tableRef.value?.clearSort(),
})
</script>
