<template>
  <div class="files-toolbar">
    <el-button type="primary" plain @click="$emit('mkdir')">
      <el-icon><FolderAdd /></el-icon>
      <span>新建文件夹</span>
    </el-button>
    <el-button plain @click="$emit('open-recycle-bin')">
      <el-icon><DeleteFilled /></el-icon>
      <span>回收站</span>
    </el-button>

    <template v-if="selected.length">
      <el-divider direction="vertical" />
      <span class="selected-count">
        已选 {{ selected.length }} 项<template v-if="selectedSizeText"> · {{ selectedSizeText }}</template>
      </span>
      <el-button @click="$emit('move-selected')">
        <el-icon><FolderOpened /></el-icon>
        <span>移动到</span>
      </el-button>
      <el-button @click="$emit('copy-selected')">
        <el-icon><CopyDocument /></el-icon>
        <span>复制</span>
      </el-button>
      <el-button @click="$emit('cut-selected')">
        <el-icon><Scissor /></el-icon>
        <span>剪切</span>
      </el-button>
      <el-button :disabled="selected.length !== 1" @click="$emit('rename-selected')">
        <el-icon><EditPen /></el-icon>
        <span>重命名</span>
      </el-button>
      <el-button type="danger" plain @click="$emit('delete-selected')">
        <el-icon><Delete /></el-icon>
        <span>删除</span>
      </el-button>
    </template>

    <template v-if="clipboard">
      <el-divider direction="vertical" />
      <el-tag type="info" effect="plain" closable class="clip-tag" @close="$emit('clear-clipboard')">
        剪贴板：{{ clipboard.mode === 'copy' ? '复制' : '移动' }} {{ clipboard.items.length }} 项
      </el-tag>
      <el-tooltip :disabled="!isSearching" content="请先退出搜索后再粘贴" placement="top">
        <span>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="isSearching || !clipboard.items.length"
            @click="$emit('paste')"
          >
            <el-icon><DocumentCopy /></el-icon>
            <span>粘贴到「{{ currentName }}」</span>
          </el-button>
        </span>
      </el-tooltip>
    </template>

    <div class="toolbar-filters">
      <el-select
        :model-value="fileType"
        class="filter-select"
        aria-label="文件类型筛选"
        @update:model-value="$emit('update:fileType', $event)"
      >
        <el-option :value="0" label="全部类型" />
        <el-option :value="4" label="视频" />
        <el-option :value="2" label="图片" />
        <el-option :value="5" label="压缩包" />
        <el-option :value="1" label="文档" />
        <el-option :value="3" label="音频" />
      </el-select>
      <el-select
        :model-value="sortBy"
        class="sort-select"
        aria-label="排序字段"
        @update:model-value="$emit('update:sortBy', $event)"
      >
        <el-option value="name" label="按名称" />
        <el-option value="mtime" label="修改时间" />
        <el-option value="ptime" label="创建时间" />
      </el-select>
      <el-button
        class="order-btn"
        :title="sortOrder === 'asc' ? '当前升序，点击切换降序' : '当前降序，点击切换升序'"
        @click="$emit('toggle-sort-order')"
      >
        <el-icon><Sort /></el-icon>
        <span>{{ sortOrder === 'asc' ? '升序' : '降序' }}</span>
      </el-button>
    </div>
  </div>
</template>

<script setup>
import {
  CopyDocument,
  Delete,
  DeleteFilled,
  DocumentCopy,
  EditPen,
  FolderAdd,
  FolderOpened,
  Scissor,
  Sort,
} from '@element-plus/icons-vue'

defineProps({
  selected: { type: Array, default: () => [] },
  selectedSizeText: { type: String, default: '' },
  clipboard: { type: Object, default: null },
  currentName: { type: String, default: '根目录' },
  isSearching: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  fileType: { type: Number, default: 0 },
  sortBy: { type: String, default: 'name' },
  sortOrder: { type: String, default: 'asc' },
})

defineEmits([
  'mkdir',
  'open-recycle-bin',
  'move-selected',
  'copy-selected',
  'cut-selected',
  'rename-selected',
  'delete-selected',
  'clear-clipboard',
  'paste',
  'update:fileType',
  'update:sortBy',
  'toggle-sort-order',
])
</script>
