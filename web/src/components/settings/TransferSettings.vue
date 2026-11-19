<template>
  <div class="transfer-settings-group">
    <!-- 功能开关 -->
    <div class="section-card">
      <div class="section-title">功能开关</div>
      <el-form-item label="115 分享转存">
        <div class="switch-row">
          <el-switch v-model="f.enable_share_transfer" @change="onToggleShareTransfer" />
          <span class="tip">关闭后新消息中的分享链接不再转存，排队任务自动跳过</span>
        </div>
      </el-form-item>
      <el-form-item label="云下载（ED2K / 磁力）">
        <div class="switch-row">
          <el-switch v-model="f.enable_offline_download" />
          <span class="tip">关闭后新消息中的电驴/磁力链接不再提交云下载，排队任务自动跳过</span>
        </div>
      </el-form-item>
    </div>

    <!-- 转存控制 -->
    <div class="section-card">
      <div class="section-title">转存控制</div>
      <el-form-item label="转存间隔">
        <div class="range-row">
          <el-select
            :model-value="transferPreset"
            style="width: 220px"
            fit-input-width
            @update:model-value="$emit('update:transferPreset', $event)"
          >
            <el-option
              v-for="p in TRANSFER_PRESETS"
              :key="p.label"
              :label="p.label"
              :value="p.label"
            />
          </el-select>
          <span class="tip range-tip">每笔转存在区间内随机等待；频繁触发风控请选「稳健」</span>
        </div>
      </el-form-item>

      <el-form-item label="每日转存上限">
        <div class="range-row">
          <el-input-number
            v-model="f.daily_limit"
            :min="1"
            :max="50"
            :step="1"
            :step-strictly="true"
            :precision="0"
          />
          <span class="tip range-tip">1~50；115 单账号每日转存的安全上限，不建议设为最大值</span>
        </div>
      </el-form-item>

      <el-form-item label="分享体积下限">
        <div class="range-row">
          <el-input-number
            v-model="f.min_size_gb"
            :min="0"
            :max="1000"
            :step="0.1"
            :precision="1"
          />
          <span class="tip range-tip">单位 GB；低于此值的分享视为广告并自动跳过，0 表示关闭</span>
        </div>
      </el-form-item>

      <el-form-item label="失败重试">
        <div class="range-row">
          <el-select
            :model-value="retryPreset"
            style="width: 220px"
            fit-input-width
            @update:model-value="$emit('update:retryPreset', $event)"
          >
            <el-option
              v-for="p in RETRY_PRESETS"
              :key="p.label"
              :label="p.label"
              :value="p.label"
            />
          </el-select>
          <span class="tip range-tip">失败后按预设间隔逐级重试，最多 5 次尝试（含首次）</span>
        </div>
      </el-form-item>

      <el-form-item label="空间不足自动暂停">
        <div class="switch-row">
          <el-switch v-model="f.space_guard_enabled" />
          <span class="tip">115 使用率 ≥90%（或剩余 &lt;10GB）时自动暂停转存队列：本条标记「跳过」且不再自动重试，后续任务保留排队、不判失败；回落到 88% 以下且剩余 ≥10GB 时自动恢复；关闭后不做空间预检</span>
        </div>
      </el-form-item>
    </div>

    <!-- Telegram 接入与代理 -->
    <div class="section-card">
      <div class="section-title">Telegram</div>
      <el-form-item label="接入方式">
        <el-radio-group v-model="f.tg_mode">
          <el-radio value="polling">网页轮询</el-radio>
          <el-radio value="userbot">Userbot</el-radio>
          <el-radio value="both">两者</el-radio>
        </el-radio-group>
        <div class="tip" style="width: 100%">
          {{ tgModeTip }}<br />
          <span class="warn-text">⚠️ 接入方式的变更保存后需重启程序，才会开启/停止对应监听（重启容器：<code>docker restart autosave115</code>）</span>
        </div>
      </el-form-item>

      <el-form-item v-if="f.tg_mode !== 'userbot'" label="轮询间隔">
        <div class="range-row">
          <el-select
            :model-value="pollPreset"
            style="width: 220px"
            fit-input-width
            @update:model-value="$emit('update:pollPreset', $event)"
          >
            <el-option
              v-for="p in POLL_PRESETS"
              :key="p.label"
              :label="p.label"
              :value="p.label"
            />
          </el-select>
          <span class="tip range-tip">每轮轮询在区间内随机等待，降低被识别风险；仅作用于网页轮询频道</span>
        </div>
      </el-form-item>

      <el-form-item v-if="f.tg_mode !== 'polling'" label="Userbot">
        <div class="ub-inline">
          <el-tag :type="ub.connected ? 'success' : 'danger'" size="small">
            {{ ub.connected ? '已连接' : '未连接' }}
          </el-tag>
          <span class="tip">
            {{ ub.connected
              ? 'TG 账号监听运行中（实时接收私密频道消息）'
              : '未登录 Userbot，相关频道无法收消息' }}
          </span>
        </div>
      </el-form-item>

      <el-form-item label="TG 代理">
        <div class="range-row input-btn-row" style="width: 100%">
          <el-input
            v-model="f.proxy"
            placeholder="socks5://127.0.0.1:1080 或 http://127.0.0.1:7890（仅 TG 流量走代理，留空直连）"
            :title="f.proxy === SECRET_MASK ? '已配置代理（显示为掩码）：清空后输入新地址可更换' : ''"
          />
          <el-button :loading="proxyTesting" @click="testProxy">测试代理</el-button>
        </div>
        <div class="tip" style="width: 100%">
          国内部署必填：所有 TG 流量（频道抓取/补漏/按钮解析/通知/Bot 指令）都走此代理，
          115 直连不受影响。<br />
          <span class="warn-text">注意：代理变更对网页轮询/Bot 即时生效，但已连接的 Userbot
          需重启程序或在「账号管理」重新登录后才会改走新代理。</span>
        </div>
      </el-form-item>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk, SECRET_MASK } from '../../api'

const props = defineProps({
  f: {
    type: Object,
    required: true,
  },
  pollPreset: {
    type: String,
    default: '标准（30秒~2分）',
  },
  transferPreset: {
    type: String,
    default: '标准（1~3分）',
  },
  retryPreset: {
    type: String,
    default: '标准（1分/5分/30分）',
  },
})

defineEmits(['update:pollPreset', 'update:transferPreset', 'update:retryPreset'])

// 预设配置
const POLL_PRESETS = [
  { label: '快速（15~45秒）', min: 15, max: 45 },
  { label: '标准（30秒~2分）', min: 30, max: 120 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]

const TRANSFER_PRESETS = [
  { label: '快速（30秒~1分）', min: 30, max: 60 },
  { label: '标准（1~3分）', min: 60, max: 180 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]

const RETRY_PRESETS = [
  { label: '快速（30秒/2分/10分）', sec: [30, 120, 600] },
  { label: '标准（1分/5分/30分）', sec: [60, 300, 1800] },
  { label: '稳健（5分/30分/2小时）', sec: [300, 1800, 7200] },
]

// Userbot 状态
const ub = reactive({ connected: false })
const loadUb = async () => {
  try {
    ub.connected = !!(await api.get('/api/tg/userbot/status')).data.connected
  } catch { /* ignore */ }
}

watch(() => props.f.tg_mode, (v) => {
  if (v && v !== 'polling') loadUb()
})

onMounted(() => {
  if (props.f.tg_mode && props.f.tg_mode !== 'polling') loadUb()
})

const tgModeTip = computed(() => ({
  polling: '网页轮询：仅抓取公开频道，免 TG 账号',
  userbot: 'Userbot：TG 小号实时监听，支持私密频道',
  both: '当前模式：两者——按每个频道自身的接入方式分流处理，互不影响',
}[props.f.tg_mode] || ''))

// 联动：关闭分享转存总开关时，下级「分享追更」自动关闭
const onToggleShareTransfer = (val) => {
  if (!val) {
    props.f.share_update_enabled = false
  }
}

// 代理测试
const proxyTesting = ref(false)
const testProxy = async () => {
  proxyTesting.value = true
  try {
    const { data } = await api.post('/api/settings/proxy/test', { proxy: props.f.proxy })
    saveOk('代理可用', data.detail)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    proxyTesting.value = false
  }
}

defineExpose({ loadUb, POLL_PRESETS, TRANSFER_PRESETS, RETRY_PRESETS })
</script>
