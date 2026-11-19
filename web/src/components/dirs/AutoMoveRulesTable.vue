<template>
  <div>
    <!-- 规则表格工具栏：紧贴列表上方，便于直观管理与添加 -->
    <div class="rules-table-toolbar">
      <div class="rules-toolbar-left">
        <span class="rules-toolbar-title">整理规则映射列表</span>
        <el-tag v-if="rules.length" size="small" class="count-tag">共 {{ rules.length }} 条规则</el-tag>
        <span class="rules-toolbar-tip">按由上至下顺序依次排队扫描与移动（只移动正片文件，文件夹本身不移动）</span>
      </div>
      <div class="rules-toolbar-right">
        <el-button type="primary" size="small" @click="$emit('add')">
          <el-icon><Plus /></el-icon>
          <span>添加整理规则</span>
        </el-button>
      </div>
    </div>

    <!-- 规则表格 -->
    <div class="table-responsive-wrapper">
      <el-table :data="rules" size="small">
        <template #empty>
          <div class="rules-empty-box">
            <p>暂无整理规则，点击下方或上方「添加整理规则」配置对应关系</p>
            <el-button type="primary" plain size="small" style="margin-top: 6px" @click="$emit('add')">
              <el-icon><Plus /></el-icon>
              <span>添加整理规则</span>
            </el-button>
          </div>
        </template>
        <el-table-column label="整理模式" width="115">
          <template #default="{ row }">
            <el-tag
              :type="(row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library') ? 'success' : 'primary'"
              effect="plain"
              size="small"
            >
              {{ (row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library') ? '总库分流' : '精确匹配' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="源目录（扫描位置）" min-width="190">
          <template #default="{ row }">
            <el-button class="dir-btn" @click="$emit('pick-dir', { row, kind: 'move_source' })">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.source_dir || '请选择源目录' }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="目标目录（移入位置）" min-width="190">
          <template #default="{ row }">
            <el-button class="dir-btn" @click="$emit('pick-dir', { row, kind: 'move_target' })">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.target_dir || '请选择目标目录' }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="匹配关键词" width="130">
          <template #default="{ row }">
            <span v-if="(row.mode === 'library' || (!row.mode || row.mode === 'default') && moveSettings.auto_move_mode === 'library')">
              <el-tag type="info" size="small">自动识别总库子剧</el-tag>
            </span>
            <span v-else>
              <el-tag type="primary" effect="plain" size="small">
                {{ row.keyword || extractKeyword(row.target_dir) || '未设定' }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column width="120">
          <template #header>
            <span class="info-tip-label">
              <span>媒体类型</span>
              <el-tooltip placement="top" effect="dark">
                <template #content>
                  按标题匹配时的文件归属判别（配合两库同名场景）：<br />
                  · 电影库：跳过带集数标识（S01E01/第1集）的文件，剧集交给剧集规则；<br />
                  · 剧集库：正常认领（含剧场版/特别篇）；<br />
                  · 自动：不区分，与历史行为一致。<br />
                  与规则顺序无关，无论哪条规则先执行结果一致。
                </template>
                <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
              </el-tooltip>
            </span>
          </template>
          <template #default="{ row }">
            <el-tooltip placement="top" :disabled="row.media_type !== 'movie'"
                        content="电影库规则会跳过带集数标识（S01E01/第1集）的文件，避免重名影片错位归档">
              <el-select v-model="row.media_type" size="small" style="width: 104px" fit-input-width
                         @change="$emit('save-rule', row)">
                <el-option label="自动" value="auto" />
                <el-option label="电影库" value="movie" />
                <el-option label="剧集库" value="tv" />
              </el-select>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column width="88">
          <template #header>
            <span class="info-tip-label">
              <span>云下载</span>
              <el-tooltip placement="top" effect="dark">
                <template #content>
                  云下载目录专用模式：<br />
                  115 云下载的中文标题只在任务目录名上，目录内文件多为纯英文名——开启后按<b>任务子目录名</b>匹配（自动剥除含网址的广告括号段），目录内文件继承归属。<br />
                  电影/剧集按集数标识自动判别；广告域名/微小体积文件自动过滤不入库；<br />
                  仅当源目录是分类路由配置的云下载目录时可开启，配置变更后失效会自动拦下并告警。
                </template>
                <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
              </el-tooltip>
            </span>
          </template>
          <template #default="{ row }">
            <el-tooltip placement="top" :disabled="!(row.dir_match && !cloudValid(row))"
                        content="源目录已不是当前配置的云下载目录，云下载模式不会生效，请修改源目录或关闭此开关">
              <div style="display: flex; align-items: center; gap: 4px">
                <el-switch v-model="row.dir_match" :active-value="1" :inactive-value="0"
                           :disabled="!row.dir_match && !cloudValid(row)"
                           @change="$emit('toggle-dir-match', row)" />
                <el-icon v-if="row.dir_match && !cloudValid(row)" style="color: var(--el-color-warning)">
                  <WarningFilled />
                </el-icon>
              </div>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="上次整理状态" width="150">
          <template #default="{ row }">
            <span v-if="row.last_run_at" style="font-size: var(--fs-caption); color: var(--saas-text-main)">
              {{ row.last_moved_count > 0 ? `移动 ${row.last_moved_count} 个文件` : '无新匹配文件' }}
            </span>
            <span v-else style="color: var(--saas-text-sub); font-size: var(--fs-caption)">尚未执行</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-tooltip placement="top" :disabled="!(row.dir_match && !cloudValid(row))"
                        content="云下载模式未生效（源目录已不是云下载目录）：请改回云下载目录，或关闭云下载开关回落基本规则后再启用">
              <span>
                <el-switch
                  v-model="row.enabled"
                  :active-value="1"
                  :inactive-value="0"
                  :disabled="!!(row.dir_match && !cloudValid(row))"
                  @change="$emit('save-rule', row)"
                />
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" text @click="$emit('delete-rule', row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { extractKeyword, normPath } from './autoMoveUtils'

const props = defineProps({
  rules: {
    type: Array,
    default: () => []
  },
  moveSettings: {
    type: Object,
    default: () => ({})
  },
  cloudDirs: {
    type: Array,
    default: () => []
  }
})

defineEmits(['add', 'pick-dir', 'save-rule', 'delete-rule', 'toggle-dir-match'])

const cloudValid = (row) => (props.cloudDirs || []).includes(normPath(row.source_dir))
</script>
