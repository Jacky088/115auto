<template>
  <el-card v-if="visible" shadow="never" class="page-card risk-card">
    <div class="risk-row">
      <div class="risk-info">
        <div class="risk-title">
          <el-tag :type="risk.cooling ? 'danger' : 'warning'" effect="dark" size="small">
            {{ risk.cooling ? '115 风控冷却中' : '115 自适应退避中' }}
          </el-tag>
          <span class="risk-meta">
            风控降速 {{ risk.level }}/{{ risk.max_level }} 级（间隔 {{ risk.pace_factor }} 倍）
            <template v-if="remain > 0"> · 剩余冷却 <b>{{ fmtRemain(remain) }}</b></template>
          </span>
        </div>
        <div v-if="risk.last_error" class="risk-err">最近风控提示：{{ risk.last_error }}</div>
      </div>
      <el-button type="primary" size="small" :loading="probing" @click="probeNow">
        立即探测恢复
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../../api'

const props = defineProps({
  risk: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['recovered'])

const probing = ref(false)
const remain = ref(0)
let timer = null

const visible = computed(() => !!(props.risk && (props.risk.cooling || Number(props.risk.level) > 0)))

const stopTimer = () => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(
  () => props.risk?.cooldown_remaining_sec,
  (v) => {
    remain.value = Math.max(0, Number(v) || 0)
    stopTimer()
    if (remain.value > 0) {
      timer = setInterval(() => {
        if (remain.value > 0) remain.value -= 1
        else stopTimer()
      }, 1000)
    }
  },
  { immediate: true },
)

onBeforeUnmount(stopTimer)

const fmtRemain = (s) => {
  const m = Math.floor(s / 60)
  const sec = s % 60
  return m > 0 ? `${m}分${String(sec).padStart(2, '0')}秒` : `${sec}秒`
}

const probeNow = async () => {
  probing.value = true
  try {
    const { data } = await api.post('/api/system/115/probe')
    if (data.ok) {
      ElMessage.success(data.msg || '115 接口已恢复')
      emit('recovered')
    } else {
      ElMessage.warning(data.msg || '暂未恢复，请稍后再试')
      emit('recovered')
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    probing.value = false
  }
}
</script>

<style scoped>
.risk-card {
  margin-bottom: 16px;
  border-color: rgba(245, 158, 11, 0.45);
  background: rgba(245, 158, 11, 0.06);
}
.risk-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.risk-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.risk-meta {
  font-size: var(--fs-body);
  color: var(--saas-text-main);
}
.risk-err {
  margin-top: 6px;
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
  word-break: break-all;
}
</style>
