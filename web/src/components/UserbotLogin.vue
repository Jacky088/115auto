<template>
  <div class="ub-box" :class="{ compact }">
    <div class="ub-status">
      <span>连接状态：</span>
      <el-tag :type="st.connected ? 'success' : 'danger'" size="small">
        {{ st.connected ? '已连接' : '未连接' }}
      </el-tag>
      <el-tag v-if="st.connected && who" type="success" effect="plain" size="small">账号：{{ who }}</el-tag>
      <el-button v-if="st.connected" text type="primary" size="small" @click="resetFlow">
        重新登录
      </el-button>
    </div>

    <!-- 登录流程：api_id/api_hash → 验证码 →（可选）两步验证密码 -->
    <el-form v-if="!st.connected || flowStep > 0" label-width="90px" size="default" class="ub-form"
             @submit.prevent>
      <template v-if="flowStep === 0">
        <el-form-item label="API ID">
          <el-input v-model="form.api_id" placeholder="my.telegram.org 获取（纯数字）" />
        </el-form-item>
        <el-form-item label="API Hash">
          <el-input v-model="form.api_hash" type="password" show-password
                    placeholder="my.telegram.org 获取" />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="form.phone" placeholder="+8613800138000（建议小号）" />
        </el-form-item>
        <el-button type="primary" :loading="busy" @click="startLogin">发送验证码并校验</el-button>
        <span class="ub-hint">发送验证码本身就会校验 api_id/api_hash 是否正确</span>
      </template>
      <template v-else-if="flowStep === 1">
        <el-form-item label="验证码">
          <el-input v-model="form.code" placeholder="TG 官方发来的验证码" />
        </el-form-item>
        <el-button type="primary" :loading="busy" @click="submitCode">提交验证码</el-button>
        <el-button @click="resetFlow">重填凭证</el-button>
      </template>
      <template v-else>
        <el-form-item label="两步密码">
          <el-input v-model="form.password" type="password" show-password
                    placeholder="该账号开启了两步验证" />
        </el-form-item>
        <el-button type="primary" :loading="busy" @click="submitPwd">完成登录</el-button>
      </template>
      <el-alert v-if="flowErr" :title="flowErr" type="error" :closable="false" show-icon class="ub-err" />
    </el-form>

    <!-- 已连接：一键自检（频道可见性） -->
    <template v-else>
      <el-button type="primary" plain :loading="testing" @click="runTest">
        测试：检查频道可见性
      </el-button>
      <span class="ub-hint">逐个验证已启用的 Userbot 频道能否真正收到消息</span>
      <div v-if="testResult" class="ub-result">
        <el-alert v-if="!testResult.ok && testResult.err" :title="testResult.err" type="error"
                  :closable="false" show-icon />
        <template v-else>
          <el-alert :title="testResult.ok ? '✅ 连接与授权正常' + (testResult.channels.length ? '，频道全部可访问' : '') : '⚠️ 部分频道无法访问'"
                    :type="testResult.ok ? 'success' : 'warning'" :closable="false" show-icon />
          <div v-for="c in testResult.channels" :key="c.name" class="ub-ch">
            <el-icon :size="14" :class="c.ok ? 'ok' : 'bad'">
              <CircleCheckFilled v-if="c.ok" /><CircleCloseFilled v-else />
            </el-icon>
            <span class="ub-ch-name">{{ c.name }}</span>
            <span v-if="c.ok" class="ub-ch-title">{{ c.title }}</span>
            <span v-else class="ub-ch-err">{{ c.err }}</span>
          </div>
          <p v-if="!testResult.channels.length" class="ub-hint" style="margin-top: 8px">
            尚未添加 Userbot 类型频道；连接与授权已验证通过。添加频道后可再次测试。
          </p>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const props = defineProps({ compact: Boolean })  // compact: 用于弹窗内（缩小留白）
const emit = defineEmits(['update'])

const st = reactive({ configured: false, connected: false, me: '' })
const flowStep = ref(0)  // 0 凭证 / 1 验证码 / 2 两步密码
const flowErr = ref('')
const busy = ref(false)
const form = reactive({ api_id: '', api_hash: '', phone: '', code: '', password: '' })
const testing = ref(false)
const testResult = ref(null)

const who = ref('')

const loadStatus = async () => {
  try {
    const d = (await api.get('/api/tg/userbot/status')).data
    st.configured = !!d.configured
    st.connected = !!d.connected
  } catch { /* ignore */ }
}

const resetFlow = () => { flowStep.value = 0; flowErr.value = ''; form.code = ''; form.password = '' }

const startLogin = async () => {
  if (!form.api_id.trim() || !form.api_hash.trim() || !form.phone.trim()) {
    flowErr.value = '请完整填写 API ID / API Hash / 手机号'
    return
  }
  busy.value = true
  flowErr.value = ''
  try {
    await api.post('/api/tg/userbot/start-login', {
      api_id: Number(form.api_id), api_hash: form.api_hash.trim(), phone: form.phone.trim(),
    })
    flowStep.value = 1
    ElMessage.success('验证码已发送，请查收 TG 官方消息')
  } catch (e) { flowErr.value = errMsg(e) } finally { busy.value = false }
}

const _finish = async () => {
  resetFlow()
  await loadStatus()
  ElMessage.success('Userbot 登录成功，建议点「测试」验证频道可见性')
  emit('update')
}

const submitCode = async () => {
  if (!form.code.trim()) { flowErr.value = '请输入验证码'; return }
  busy.value = true
  flowErr.value = ''
  try {
    const { data } = await api.post('/api/tg/userbot/submit-code', { code: form.code })
    if (data.needs_password) flowStep.value = 2
    else await _finish()
  } catch (e) { flowErr.value = errMsg(e) } finally { busy.value = false }
}

const submitPwd = async () => {
  if (!form.password) { flowErr.value = '请输入两步验证密码'; return }
  busy.value = true
  flowErr.value = ''
  try {
    await api.post('/api/tg/userbot/submit-password', { password: form.password })
    await _finish()
  } catch (e) { flowErr.value = errMsg(e) } finally { busy.value = false }
}

const runTest = async () => {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = (await api.post('/api/tg/userbot/test')).data
    if (testResult.value?.me) who.value = testResult.value.me
  } catch (e) { testResult.value = { ok: false, err: errMsg(e) } } finally { testing.value = false }
}

onMounted(loadStatus)
defineExpose({ loadStatus, runTest })
</script>

<style scoped>
.ub-box { display: flex; flex-direction: column; gap: 12px; }
.ub-box.compact { gap: 10px; }
.ub-status { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.ub-form :deep(.el-button + .el-button) { margin-left: 10px; }
.ub-hint { color: #999; font-size: 12px; }
.ub-err { margin-top: 10px; }
.ub-result { display: flex; flex-direction: column; gap: 6px; }
.ub-ch { display: flex; align-items: center; gap: 6px; font-size: 13px; padding-left: 4px; }
.ub-ch .ok { color: #67c23a; }
.ub-ch .bad { color: #f56c6c; }
.ub-ch-name { font-weight: 600; }
.ub-ch-title { color: #909399; }
.ub-ch-err { color: #f56c6c; font-size: 12px; }
</style>
