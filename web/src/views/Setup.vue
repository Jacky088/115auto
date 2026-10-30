<template>
  <div class="saas-hero-bg setup-bg">
    <el-card class="setup-card">
      <h2 style="margin-top: 0">🎉 初始化向导</h2>
      <el-steps :active="step" align-center finish-status="success" style="margin-bottom: 24px">
        <el-step title="115 扫码登录" />
        <el-step title="TG 接入方式" />
        <el-step title="添加频道" />
        <el-step title="完成" />
      </el-steps>

      <!-- Step 0: 115 扫码 -->
      <div v-if="step === 0">
        <p style="color: #666">使用手机 115 App 扫描二维码登录。也可以稍后在「账号管理」中完成。</p>
        <div style="text-align: center; min-height: 220px">
          <template v-if="qr.loading"><el-skeleton style="width: 180px" animated /></template>
          <template v-else-if="qr.svg">
            <div class="qr-box" v-html="qr.svg" @click="createQr"></div>
            <p v-if="qr.scanned" style="color: #67c23a; margin: 4px">✅ 已扫描，请在手机上确认</p>
            <p v-else style="color: #999; font-size: 12px; margin: 4px">点击二维码可刷新</p>
          </template>
          <el-button v-else @click="createQr">生成二维码</el-button>
        </div>
        <div style="text-align: right">
          <el-button @click="step = 1" :disabled="!qr.ok">下一步</el-button>
          <el-button text type="info" @click="step = 1">跳过，稍后配置</el-button>
        </div>
      </div>

      <!-- Step 1: TG 接入 -->
      <div v-else-if="step === 1">
        <p style="color: #666">选择监听 Telegram 频道的方式：</p>
        <el-radio-group v-model="tgMode" style="display: block; margin: 16px 0">
          <el-radio value="polling" size="large" border style="display: block; margin: 10px 0; height: auto; padding: 12px">
            <b>网页轮询（推荐）</b><br />
            <span style="color: #999; font-size: 12px">只支持公开频道；不需要 TG 账号，零风控，延迟分钟级</span>
          </el-radio>
          <el-radio value="userbot" size="large" border style="display: block; margin: 10px 0; height: auto; padding: 12px">
            <b>Userbot 账号监听</b><br />
            <span style="color: #999; font-size: 12px">实时、支持私有频道；需要 TG api_id/api_hash，建议用小号</span>
          </el-radio>
          <el-radio value="both" size="large" border style="display: block; margin: 10px 0; height: auto; padding: 12px">
            <b>两者都启用</b>
          </el-radio>
        </el-radio-group>
        <div style="text-align: right">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" @click="saveMode">下一步</el-button>
        </div>
      </div>

      <!-- Step 2: 添加频道 -->
      <div v-else-if="step === 2">
        <p style="color: #666">添加要监控的频道（支持粘贴完整链接，每行一个）：</p>
        <el-input v-model="channelText" type="textarea" :rows="5"
                  placeholder="https://t.me/s/regeng115&#10;@some_channel" />
        <div style="text-align: right; margin-top: 16px">
          <el-button @click="step = 1">上一步</el-button>
          <el-button type="primary" :loading="adding" @click="addChannels">下一步</el-button>
        </div>
      </div>

      <!-- Step 3: 完成 -->
      <div v-else style="text-align: center; padding: 30px 0">
        <el-result icon="success" title="配置完成"
                   sub-title="默认分类规则和黑名单已就绪。点击下方按钮，系统立即开始监控频道并自动转存。">
          <template #extra>
            <div style="display: flex; flex-direction: column; align-items: center; gap: 12px">
              <el-button type="primary" size="large" :loading="starting" @click="startNow">🚀 开始运行</el-button>
              <el-button text type="info" size="small" @click="finish">稍后再启动（顶栏可随时点「开始运行」）</el-button>
            </div>
          </template>
        </el-result>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const router = useRouter()
const step = ref(0)
const tgMode = ref('polling')
const channelText = ref('')
const adding = ref(false)

// ---- 115 扫码 ----
const qr = reactive({ loading: false, svg: '', token: null, app: '', ok: false, scanned: false })
let pollTimer = null

const createQr = async () => {
  qr.loading = true
  qr.svg = ''
  qr.ok = false
  qr.scanned = false
  try {
    const { data } = await api.post('/api/account/115/qrcode')
    qr.svg = data.qr_svg
    qr.token = data.token
    qr.app = data.app
    startPoll()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    qr.loading = false
  }
}
const startPoll = () => {
  clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      // 115 状态接口是长轮询（挂起最长 30s），单独放宽超时
      const { data } = await api.post('/api/account/115/qrcode/poll',
        { token: qr.token, app: qr.app }, { timeout: 45000 })
      if (data.status === 1) qr.scanned = true
      if (data.status === 2 || data.ok) {
        clearInterval(pollTimer)
        qr.ok = true
        ElMessage.success('115 登录成功')
        step.value = 1
      } else if (data.status < 0) {
        clearInterval(pollTimer)
        ElMessage.warning('二维码已过期，请刷新')
        qr.svg = ''
      }
    } catch { /* 轮询失败忽略 */ }
  }, 3000)
}
onUnmounted(() => clearInterval(pollTimer))

// ---- TG mode ----
const saveMode = async () => {
  try {
    await api.put('/api/settings', { tg_mode: tgMode.value })
    step.value = 2
  } catch (e) { ElMessage.error(errMsg(e)) }
}

// ---- channels ----
const addChannels = async () => {
  adding.value = true
  let ok = 0
  try {
    for (const line of channelText.value.split('\n')) {
      const name = line.trim()
      if (!name) continue
      try {
        await api.post('/api/channels', { name, type: tgMode.value === 'userbot' ? 'userbot' : 'polling' })
        ok++
      } catch (e) { ElMessage.warning(`${name}: ${errMsg(e)}`) }
    }
    ElMessage.success(`已添加 ${ok} 个频道`)
    step.value = 3
  } finally {
    adding.value = false
  }
}

const starting = ref(false)
const startNow = async () => {
  starting.value = true
  try {
    await api.post('/api/wizard/complete')
    await api.post('/api/system/start')
    ElMessage.success('🚀 系统已开始运行')
    router.push('/dashboard')
  } catch (e) { ElMessage.error(errMsg(e)); router.push('/dashboard') } finally { starting.value = false }
}
const finish = async () => {
  await api.post('/api/wizard/complete')
  router.push('/dashboard')
}
</script>

<style scoped>
.setup-bg { padding: 40px 0; }
.setup-card { width: min(640px, calc(100vw - 24px)); border-radius: 16px; border: none;
              box-shadow: 0 24px 70px rgba(5, 18, 48, .45) !important; }
.qr-box { width: 200px; margin: 0 auto; cursor: pointer; background: #fff; padding: 8px; border-radius: 8px; }
.qr-box :deep(svg) { width: 100%; height: auto; display: block; }
</style>
