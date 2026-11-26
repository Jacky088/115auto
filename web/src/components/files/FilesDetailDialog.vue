<template>
  <el-dialog
    :model-value="modelValue"
    title="文件详情"
    :width="'min(560px, calc(100vw - 36px))'"
    align-center
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template v-if="detailRow">
      <div class="detail-head">
        <div class="detail-icon" :class="detailRow.is_dir ? 'folder-bg' : 'file-bg'">
          <el-icon :size="22" :class="detailRow.is_dir ? '' : rowIconClass(detailRow)">
            <component :is="rowIconName(detailRow)" />
          </el-icon>
        </div>
        <div class="detail-title">
          <div class="detail-name">{{ detailRow.name }}</div>
          <div class="detail-sub">{{ detailRow.is_dir ? '文件夹' : fileKindLabel(detailRow.name) }}</div>
        </div>
      </div>

      <el-descriptions :column="1" border size="small">
        <el-descriptions-item v-if="detailFullPath" label="完整路径">
          <div class="detail-val-row">
            <span class="mono break-all">{{ detailFullPath }}</span>
            <el-button text type="primary" size="small" @click="$emit('copy-text', detailFullPath)">复制</el-button>
          </div>
        </el-descriptions-item>

        <el-descriptions-item label="文件 ID">
          <div class="detail-val-row">
            <span class="mono break-all">{{ detailRow.id }}</span>
            <el-button text type="primary" size="small" @click="$emit('copy-text', detailRow.id)">复制</el-button>
          </div>
        </el-descriptions-item>

        <el-descriptions-item v-if="detailRow.parent_id" label="上级目录">
          <div class="detail-val-row">
            <span class="detail-parent-wrap">
              <template v-if="detailParentName">
                <span>{{ detailParentName }}</span>
                <span class="mono detail-parent-id">({{ detailRow.parent_id }})</span>
              </template>
              <span v-else class="mono">{{ detailRow.parent_id }}</span>
            </span>
            <el-button
              v-if="isSearching"
              text
              type="primary"
              size="small"
              @click="$emit('locate-parent', detailRow)"
            >
              进入目录
            </el-button>
          </div>
        </el-descriptions-item>

        <el-descriptions-item v-if="detailRow.is_dir" label="目录大小">
          <div class="detail-val-row">
            <template v-if="dirStatLoading[detailRow.id]">
              <span>
                <el-icon class="is-loading" style="vertical-align: middle; margin-right: 4px"><Loading /></el-icon>
                正在统计…
              </span>
            </template>
            <template v-else-if="dirStats[detailRow.id]">
              <span>
                <span>{{ formatDirStatSize(dirStats[detailRow.id]) }}</span>
                <span class="detail-stat-meta">
                  （{{ dirStats[detailRow.id].file_count }} 个文件，{{ dirStats[detailRow.id].folder_count }} 个子目录）
                </span>
              </span>
              <el-button text type="primary" size="small" @click="$emit('calc-dir-stat', detailRow, true)">刷新</el-button>
            </template>
            <el-button v-else text type="primary" size="small" @click="$emit('calc-dir-stat', detailRow, false)">点击统计</el-button>
          </div>
        </el-descriptions-item>

        <el-descriptions-item v-else label="大小">{{ fmtSize(detailRow.size) }}</el-descriptions-item>
        <el-descriptions-item label="修改时间">{{ detailRow.mtime ? fmtTime(detailRow.mtime) : '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="detailRow.ctime" label="创建时间">{{ fmtTime(detailRow.ctime) }}</el-descriptions-item>

        <el-descriptions-item v-if="detailRow.pick_code" label="提取码">
          <div class="detail-val-row">
            <span class="mono break-all">{{ detailRow.pick_code }}</span>
            <el-button text type="primary" size="small" @click="$emit('copy-text', detailRow.pick_code)">复制</el-button>
          </div>
        </el-descriptions-item>

        <el-descriptions-item v-if="detailRow.sha1" label="SHA1">
          <span class="mono break-all">{{ detailRow.sha1 }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </template>
  </el-dialog>
</template>

<script setup>
import { Loading } from '@element-plus/icons-vue'
import { fmtSize, fmtTime } from '../../api'
import { fileKindLabel, formatDirStatSize, rowIconClass, rowIconName } from './fileIcons'

defineProps({
  modelValue: { type: Boolean, default: false },
  detailRow: { type: Object, default: null },
  isSearching: { type: Boolean, default: false },
  detailParentName: { type: String, default: '' },
  detailFullPath: { type: String, default: '' },
  dirStats: { type: Object, default: () => ({}) },
  dirStatLoading: { type: Object, default: () => ({}) },
})

defineEmits(['update:modelValue', 'locate-parent', 'calc-dir-stat', 'copy-text'])
</script>
