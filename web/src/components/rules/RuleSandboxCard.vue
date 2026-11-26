<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>规则测试沙盒</span>
        <el-tooltip
          content="模拟频道消息从黑名单、标签/正则分类、订阅命中、链接提取到目标目录的完整链路"
          placement="top"
          effect="dark"
        >
          <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
        </el-tooltip>
      </div>
    </template>
    <div class="toolbar">
      <el-input
        v-model="testText"
        placeholder="输入测试标题、消息正文或含 115/ED2K/磁力链接的文本，模拟完整命中链路"
        style="flex: 1; min-width: 240px"
        clearable
        @keyup.enter="runTest"
      />
      <el-input
        v-model="testChannel"
        placeholder="频道名（可选，测频道黑名单）"
        style="width: 190px"
        clearable
        @keyup.enter="runTest"
      />
      <el-button type="primary" :loading="testing" @click="runTest">模拟测试</el-button>
    </div>

    <el-descriptions v-if="testResult" :column="1" border size="small" style="margin-top: 10px">
      <el-descriptions-item label="判定分类">
        <template v-if="testResult.category">
          <el-tag size="small" type="primary" effect="plain" style="margin-right: 8px">
            {{ testResult.category }}
          </el-tag>
          <el-tag v-if="testResult.match_source === 'tag'" size="small" type="success">
            频道标签优先命中
          </el-tag>
          <el-tag v-else-if="testResult.match_source === 'rule'" size="small" type="info">
            正则规则命中{{ testResult.match_priority ? `（优先级 ${testResult.match_priority}）` : '' }}
          </el-tag>
        </template>
        <span v-else style="color: var(--text-muted)">（未命中规则，将按「未分类动作」处理）</span>
      </el-descriptions-item>
      <el-descriptions-item label="命中规则">
        <span class="mono">{{ testResult.matched_rule || '-' }}</span>
      </el-descriptions-item>
      <el-descriptions-item label="黑名单">
        <el-tag v-if="testResult.blocked" type="danger" size="small">
          拦截（{{ ruleTypeLabel(testResult.block_rule_type) }}：{{ testResult.block_rule_value }}）
        </el-tag>
        <el-tag v-else type="success" size="small">通过</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="订阅命中">
        <template v-if="testResult.matched_subscriptions && testResult.matched_subscriptions.length">
          <el-tag
            v-for="kw in testResult.matched_subscriptions"
            :key="kw"
            size="small"
            type="success"
            style="margin-right: 6px"
          >{{ kw }}</el-tag>
        </template>
        <span v-else style="color: var(--text-muted)">无</span>
      </el-descriptions-item>
      <el-descriptions-item v-if="testResult.extracted_links > 0" label="提取链接">
        <span>共 {{ testResult.extracted_links }} 条（分享 {{ testResult.share_links }} / 云下载 {{ testResult.offline_links }}）</span>
      </el-descriptions-item>
      <el-descriptions-item v-if="testResult.disabled_kinds && testResult.disabled_kinds.length" label="功能开关">
        <el-tag type="warning" size="small">
          {{ testResult.disabled_kinds.join('、') }}已关闭，实际不会处理
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="目标目录">
        <template v-if="testResult.blocked">-</template>
        <template v-else-if="testResult.offline_target_dir && testResult.target_kind === 'offline'">
          <span>{{ testResult.offline_target_dir }}</span>
          <el-tag size="small" type="warning" style="margin-left: 6px">云下载目录</el-tag>
        </template>
        <template v-else>{{ testResult.target_dir || '跳过（不转存、不云下载）' }}</template>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="testResult" class="sandbox-quick-actions">
      <el-button size="small" plain type="primary" @click="$emit('create-rule', testText)">
        以当前文本新建规则
      </el-button>
      <el-button size="small" plain type="warning" @click="$emit('add-blacklist', testText)">
        提取文本加入关键词黑名单
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import { api, errMsg } from '../../api'

defineEmits(['create-rule', 'add-blacklist'])

const RULE_TYPE_LABEL = {
  keyword: '关键词', channel: '频道', file_pattern: '文件名正则',
  size_limit: '分享体积下限', category: '分类',
}
const ruleTypeLabel = (t) => RULE_TYPE_LABEL[t] || t || '-'

const testText = ref('')
const testChannel = ref('')
const testing = ref(false)
const testResult = ref(null)

const runTest = async () => {
  if (!testText.value.trim()) return
  testing.value = true
  try {
    testResult.value = (await api.post('/api/rules/test', {
      text: testText.value,
      channel: testChannel.value.trim(),
    })).data
  } catch (e) {
    ElMessage.error(errMsg(e))
    testResult.value = null
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
.sandbox-quick-actions {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 10px;
}
</style>
