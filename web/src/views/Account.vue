<template>
  <div class="account-grid page-card">
    <!-- 115 -->
    <el-card shadow="never">
        <template #header><b>115 网盘</b></template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="登录状态">
            <el-tag v-if="s115.login" type="success" size="small">有效</el-tag>
            <el-tag v-else type="danger" size="small">未登录 / 失效</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="账号">{{ s115.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="说明">转存使用 Cookie 接口，失效后需重新扫码；系统每 10 分钟自动巡检</el-descriptions-item>
        </el-descriptions>
        <div style="margin-top: 14px; display: flex; gap: 10px">
          <el-button type="primary" @click="openQr">扫码登录 / 重新登录</el-button>
          <el-button @click="load">刷新状态</el-button>
        </div>
      </el-card>

    <!-- TG Userbot -->
    <el-card shadow="never">
        <template #header><b>Telegram Userbot</b></template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="配置状态">
            <el-tag :type="tg.configured ? 'success' : 'info'" size="small">
              {{ tg.configured ? '已配置' : '未配置' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="连接状态">
            <el-tag :type="tg.connected ? 'success' : 'danger'" size="small">
              {{ tg.connected ? '已连接' : '未连接' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="说明">用于实时监听频道（含私有频道）。建议使用小号；api_id/api_hash 从 my.telegram.org 申请</el-descriptions-item>
        </el-descriptions>
        <div style="margin-top: 14px">
          <el-button type="primary" @click="tgVisible = true">登录 / 重新登录</el-button>
        </div>
      </el-card>
  </div>

  <!-- 115 扫码对话框 -->
  <el-dialog v-model="qrVisible" title="115 扫码登录" width="360px" @close="stopQrPoll">
    <div style="text-align: center; min-height: 220px">
      <template v-if="qr.svg">
        <div class="qr-box" v-html="qr.svg" @click="createQr"></div>
        <p v-if="qr.scanned" style="color: #67c23a; margin: 4px">✅ 已扫描，请在手机上确认</p>
        <p v-else style="color: #999; font-size: 12px">请用 115 App 扫码（点击可刷新）</p>
      </template>
      <el-button v-else :loading="qr.loading" @click="createQr">生成二维码</el-button>
    </div>
  </el-dialog>

  <!-- Userbot 登录对话框 -->
  <el-dialog v-model="tgVisible" title="Userbot 登录" width="440px" @close="resetTgFlow">
    <el-form label-width="100px">
      <template v-if="tgStep === 0">
        <el-form-item label="API ID"><el-input v-model="tgForm.api_id" placeholder="my.telegram.org 获取" /></el-form-item>
        <el-form-item label="API Hash"><el-input v-model="tgForm.api_hash" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="tgForm.phone" placeholder="+8613800138000" /></el-form-item>
      </template>
      <template v-else-if="tgStep === 1">
        <el-form-item label="验证码"><el-input v-model="tgForm.code" placeholder="TG 收到的验证码" /></el-form-item>
      </template>
      <template v-else-if="tgStep === 2">
        <el-form-item label="两步验证密码"><el-input v-model="tgForm.password" type="password" show-password /></el-form-item>
      </template>
      <el-alert v-if="tgError" :title="tgError" type="error" :closable="false" style="margin-bottom: 10px" />
    </el-form>
    <template #footer>
      <el-button @click="tgVisible = false">取消</el-button>
      <el-button type="primary" :loading="tgLoading" @click="tgNext">
        {{ tgStep === 0 ? '发送验证码' : tgStep === 1 ? '提交验证码' : '完成登录' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const s115 = ref({})
const tg = ref({})
const qrVisible = ref(false)
const qr = reactive({ loading: false, svg: '', token: null, app: '', scanned: false })
let pollTimer = null

const load = async () => {
  s115.value = (await api.get('/api/account/115/status')).data
  tg.value = (await api.get('/api/tg/userbot/status')).data
}

// ---- 115 QR ----
const openQr = () => { qrVisible.value = true; qr.svg = ''; createQr() }
const createQr = async () => {
  qr.loading = true
  qr.scanned = false
  try {
    const { data } = await api.post('/api/account/115/qrcode')
    qr.svg = data.qr_svg
    qr.token = data.token
    qr.app = data.app
    stopQrPoll()
    pollTimer = setInterval(async () => {
      try {
        const { data } = await api.post('/api/account/115/qrcode/poll',
          { token: qr.token, app: qr.app }, { timeout: 45000 })
        if (data.status === 1) qr.scanned = true
        if (data.ok || data.status === 2) {
          stopQrPoll()
          ElMessage.success('115 登录成功')
          qrVisible.value = false
          load()
        } else if (data.status < 0) { stopQrPoll(); qr.svg = '' }
      } catch { /* ignore */ }
    }, 3000)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { qr.loading = false }
}
const stopQrPoll = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

// ---- TG login flow ----
const tgVisible = ref(false)
const tgStep = ref(0)
const tgLoading = ref(false)
const tgError = ref('')
const tgForm = reactive({ api_id: '', api_hash: '', phone: '', code: '', password: '' })

const resetTgFlow = () => { tgStep.value = 0; tgError.value = '' }
const tgNext = async () => {
  tgLoading.value = true
  tgError.value = ''
  try {
    if (tgStep.value === 0) {
      await api.post('/api/tg/userbot/start-login', {
        api_id: Number(tgForm.api_id), api_hash: tgForm.api_hash, phone: tgForm.phone,
      })
      tgStep.value = 1
      ElMessage.success('验证码已发送，请查收 TG 消息')
    } else if (tgStep.value === 1) {
      const { data } = await api.post('/api/tg/userbot/submit-code', { code: tgForm.code })
      if (data.needs_password) { tgStep.value = 2 } else { tgDone() }
    } else {
      await api.post('/api/tg/userbot/submit-password', { password: tgForm.password })
      tgDone()
    }
  } catch (e) { tgError.value = errMsg(e) } finally { tgLoading.value = false }
}
const tgDone = () => {
  ElMessage.success('Userbot 登录成功')
  tgVisible.value = false
  load()
}

onMounted(load)
onUnmounted(stopQrPoll)
</script>

<style scoped>
.account-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .account-grid { grid-template-columns: 1fr; } }
.qr-box { width: 200px; margin: 0 auto; cursor: pointer; background: #fff; padding: 8px; border-radius: 8px; }
.qr-box :deep(svg) { width: 100%; height: auto; display: block; }
</style>
