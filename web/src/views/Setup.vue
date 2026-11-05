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
        <p class="step-desc">
          国内服务器：直接用手机 115 App 扫码登录（推荐）。<br />
          海外 VPS：扫码易被 115 风控拦截，请点下方「手动导入 Cookie」。
        </p>
        <div style="text-align: center; min-height: 220px">
          <template v-if="qr.loading"><el-skeleton style="width: 180px; margin: 0 auto" animated /></template>
          <template v-else-if="qr.svg">
            <div class="qr-box" v-html="qr.svg" @click="createQr"></div>
            <p v-if="qr.err" class="qr-err">❌ {{ qr.err }}</p>
            <p v-else-if="qr.scanned" style="color: #67c23a; margin: 4px">✅ 已扫描，请在手机上确认</p>
            <p v-else style="color: #999; font-size: 12px; margin: 4px">点击二维码可刷新</p>
          </template>
          <el-button v-else type="primary" @click="createQr">生成二维码</el-button>
        </div>
        <div class="btn-row end">
          <el-button plain @click="cookieVisible = true">手动导入 Cookie</el-button>
          <el-button plain @click="step = 1">跳过，稍后配置</el-button>
          <el-button type="primary" @click="step = 1" :disabled="!qr.ok">下一步</el-button>
        </div>
      </div>

      <!-- Step 1: TG 接入 -->
      <div v-else-if="step === 1">
        <p class="step-desc">选择监听 Telegram 频道的方式：</p>
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
        <div v-if="tgMode !== 'polling'" class="ub-wizard">
          <p class="ub-wizard-tip">Userbot 模式必须登录 TG 账号后才能进入下一步：</p>
          <div class="ub-row">
            <span>连接状态：</span>
            <el-tag :type="ub.connected ? 'success' : 'danger'" size="small">
              {{ ub.connected ? '已连接' : '未连接' }}
            </el-tag>
            <el-button v-if="!ub.connected" type="primary" size="small" @click="ubDialog = true">
              打开登录窗口
            </el-button>
            <el-button v-else text type="primary" size="small" @click="ubDialog = true">
              重新登录 / 测试
            </el-button>
          </div>
          <p v-if="!ub.connected" class="ub-warn">⚠️ 未完成 Userbot 登录时「下一步」不可点击</p>
        </div>
        <div class="btn-row end" style="margin-top: 16px">
          <el-button @click="step = 0">上一步</el-button>
          <el-tooltip :disabled="tgMode === 'polling' || ub.connected" placement="top"
                      content="Userbot 模式必须先完成 TG 账号登录">
            <span>
              <el-button type="primary" :disabled="tgMode !== 'polling' && !ub.connected"
                         @click="saveMode">下一步</el-button>
            </span>
          </el-tooltip>
        </div>
      </div>

      <!-- Step 2: 添加频道 -->
      <div v-else-if="step === 2">
        <p class="step-desc">
          添加要监控的频道（支持粘贴完整链接，每行一个）{{ tgMode === 'both' ? '。两者都启用模式下，可逐行选择每个频道的接入方式：' : '：' }}
        </p>
        <el-input v-model="channelText" type="textarea" :rows="5"
                  placeholder="https://t.me/s/regeng115&#10;@some_channel" />
        <!-- both 模式：逐行指定接入方式（默认网页轮询；私有频道请选 Userbot） -->
        <div v-if="tgMode === 'both' && channelLines.length" class="ch-type-box">
          <p class="ch-type-tip">为每个频道选择接入方式（公开频道用网页轮询即可；私有频道必须选 Userbot）：</p>
          <div v-for="ln in channelLines" :key="ln" class="ch-type-row">
            <span class="ch-type-name mono">@{{ ln }}</span>
            <el-radio-group v-model="chTypes[ln]" size="small">
              <el-radio-button value="polling">网页轮询</el-radio-button>
              <el-radio-button value="userbot">Userbot</el-radio-button>
            </el-radio-group>
          </div>
        </div>
        <div class="btn-row end" style="margin-top: 16px">
          <el-button @click="step = 1">上一步</el-button>
          <el-button type="primary" :loading="adding" @click="addChannels">下一步</el-button>
        </div>
      </div>

      <!-- Step 3: 完成 -->
      <div v-else style="text-align: center; padding: 30px 0">
        <el-result icon="success" title="配置完成"
                   sub-title="默认分类规则和黑名单已就绪。启动前请确认下方就绪检查全部通过。">
          <template #extra>
            <div class="btn-stack" style="align-items: stretch; min-width: 300px">
              <!-- 就绪检查清单：全部通过才能点「开始运行」（服务端同样强制校验） -->
              <div v-loading="ready.loading" class="ready-box">
                <div class="ready-item">
                  <el-icon :size="16" :class="ready.data?.ok_115 ? 'ok' : 'bad'">
                    <CircleCheckFilled v-if="ready.data?.ok_115" /><CircleCloseFilled v-else />
                  </el-icon>
                  <span>115 登录（Cookie 有效）</span>
                  <el-button v-if="!ready.data?.ok_115" text type="primary" size="small"
                             @click="step = 0">去配置 →</el-button>
                </div>
                <div class="ready-item">
                  <el-icon :size="16" :class="ready.data?.channels ? 'ok' : 'bad'">
                    <CircleCheckFilled v-if="ready.data?.channels" /><CircleCloseFilled v-else />
                  </el-icon>
                  <span>已添加启用中的频道{{ ready.data ? `（${ready.data.channels} 个）` : '' }}</span>
                  <el-button v-if="!ready.data?.channels" text type="primary" size="small"
                             @click="step = 2">去添加 →</el-button>
                </div>
                <div v-if="ready.data?.userbot_needed" class="ready-item">
                  <el-icon :size="16" :class="ready.data?.userbot_ok ? 'ok' : 'bad'">
                    <CircleCheckFilled v-if="ready.data?.userbot_ok" /><CircleCloseFilled v-else />
                  </el-icon>
                  <span>Userbot 已连接（频道均为私有类型）</span>
                  <el-button v-if="!ready.data?.userbot_ok" text type="primary" size="small"
                             @click="step = 1">去连接 →</el-button>
                </div>
              </div>
              <el-button type="primary" size="large" :loading="starting"
                         :disabled="!ready.data?.ready" @click="startNow">🚀 开始运行</el-button>
              <el-button plain @click="skipFinish">稍后再启动（顶栏可随时点「开始运行」）</el-button>
            </div>
          </template>
        </el-result>
      </div>
    </el-card>

    <!-- 手动导入 Cookie（海外 VPS 扫码被风控时的备选登录方式） -->
    <CookieImportDialog v-model="cookieVisible" @success="onCookieImported" />

    <!-- Userbot 登录弹窗：选择 Userbot 模式后必须登录 TG 账号，否则校验不通过 -->
    <el-dialog v-model="ubDialog" title="登录 Telegram Userbot" width="500px"
               :close-on-click-modal="false">
      <UserbotLogin compact @update="onUbUpdate" />
      <template #footer>
        <el-button @click="ubDialog = false">{{ ub.connected ? '完成' : '稍后再说（下一步暂不可用）' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg } from '../api'
import CookieImportDialog from '../components/CookieImportDialog.vue'
import UserbotLogin from '../components/UserbotLogin.vue'

const router = useRouter()
const step = ref(0)
const tgMode = ref('polling')
const channelText = ref('')
const adding = ref(false)

// ---- 115 扫码 ----
const qr = reactive({ loading: false, svg: '', token: null, app: '', ok: false, scanned: false, err: '' })
const cookieVisible = ref(false)
let pollSeq = 0   // 轮询会话号：重新生成二维码/离开页面时 +1 使旧循环退出
let pollErrCount = 0

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const createQr = async () => {
  const my = ++pollSeq
  qr.loading = true
  qr.svg = ''
  qr.ok = false
  qr.scanned = false
  qr.err = ''
  pollErrCount = 0
  try {
    const { data } = await api.post('/api/account/115/qrcode')
    if (my !== pollSeq) return
    qr.svg = data.qr_svg
    qr.token = data.token
    qr.app = data.app
    pollLoop(my)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    qr.loading = false
  }
}

// 115 状态接口是长轮询（挂起最长 30s）：必须等上一次返回后再发下一次。
// 若用定时器并发轮询，手机确认瞬间会有多个挂起请求同时醒来、并发去取 cookie，
// 极易触发 115 风控导致全部失败（表现就是卡在"已扫描"，拿不到 cookie）。
const pollLoop = async (my) => {
  while (my === pollSeq && qr.token) {
    try {
      const { data } = await api.post('/api/account/115/qrcode/poll',
        { token: qr.token, app: qr.app }, { timeout: 45000 })
      if (my !== pollSeq) return
      if (data.status === 1) qr.scanned = true
      if (data.status === 2) {
        if (data.ok) {
          qr.ok = true
          ElMessage.success('115 登录成功')
          step.value = 1
        } else {
          // 确认了但取 cookie 失败（风控等）：展示原因，引导刷新二维码重试
          qr.err = data.error || '获取 Cookie 失败，请点击二维码刷新重试'
        }
        return
      }
      if (data.status < 0) {
        qr.svg = ''
        qr.err = '二维码已过期，请点击重新生成'
        return
      }
    } catch (e) {
      if (my !== pollSeq) return
      if (++pollErrCount === 3) {
        ElMessage.warning('登录状态查询失败：' + errMsg(e) + '（将继续自动重试）')
      }
      await sleep(3000)
      continue
    }
    await sleep(500) // 轻微节流，避免接口异常时紧密循环
  }
}
onUnmounted(() => { pollSeq++ })

// 手动导入 Cookie 成功：视为 115 登录完成，直接进入下一步
const onCookieImported = () => {
  pollSeq++ // 停掉可能还在跑的扫码轮询
  qr.ok = true
  qr.err = ''
  step.value = 1
}

// ---- TG 接入方式（第 2 步）：Userbot 模式必须登录 TG 账号 ----
const ubDialog = ref(false)
const ub = reactive({ connected: false })
const loadUbStatus = async () => {
  try { ub.connected = !!(await api.get('/api/tg/userbot/status')).data.connected } catch { /* ignore */ }
}
// 选择 Userbot/两者时自动弹窗，强制录入 TG 账号资料
watch(tgMode, (v) => {
  if (v !== 'polling') loadUbStatus().then(() => { if (!ub.connected) ubDialog.value = true })
})
watch(step, (v) => { if (v === 1) loadUbStatus() })
const onUbUpdate = async () => {
  await loadUbStatus()
  if (ub.connected) ubDialog.value = false
}

// ---- 就绪检查（第 4 步）：115 + 频道 +（需要时）Userbot，全部通过才能开始运行 ----
const ready = reactive({ loading: false, data: null })
const loadReadiness = async () => {
  ready.loading = true
  try { ready.data = (await api.get('/api/wizard/readiness')).data } catch { /* ignore */ }
  finally { ready.loading = false }
}
watch(step, (v) => { if (v === 3) loadReadiness() })

// 「稍后再启动」：配置不完整时弹窗提醒（不阻断以后在顶栏手动启动——服务端会再校验）
const skipFinish = async () => {
  await loadReadiness()
  if (ready.data?.ready) return finish()
  const reasons = (ready.data?.reasons || []).map((r) => `· ${r}`).join('<br/>')
  ElMessageBox.alert(
    `<b>必须设置 115 和 TG 频道才能正常运行本程序！</b><br/><br/>当前还缺：<br/>${reasons}` +
    `<br/><br/>可继续在向导中补全，或稍后到「账号管理」「频道管理」配置后再启动。`,
    '配置尚未完成',
    { dangerouslyUseHTMLString: true, confirmButtonText: '知道了', type: 'warning' },
  ).catch(() => {})
}

// ---- TG mode ----
const saveMode = async () => {
  try {
    await api.put('/api/settings', { tg_mode: tgMode.value })
    step.value = 2
  } catch (e) { ElMessage.error(errMsg(e)) }
}

// ---- channels ----
// both 模式下逐行选择接入方式：key 为解析出的频道名，默认网页轮询
const chTypes = reactive({})
const channelLines = computed(() => {
  const out = []
  for (const line of channelText.value.split('\n')) {
    const m = line.trim().match(/t\.me\/(?:s\/)?@?([0-9A-Za-z_]+)/)
    const name = (m ? m[1] : line.trim().replace(/^@/, '')).trim()
    if (name && !out.includes(name)) out.push(name)
    if (name && !(name in chTypes)) chTypes[name] = 'polling'
  }
  return out
})
const addChannels = async () => {
  adding.value = true
  let ok = 0
  const fails = []
  try {
    for (const line of channelText.value.split('\n')) {
      const name = line.trim()
      if (!name) continue
      // userbot 模式全部走 Userbot；both 模式按逐行选择；polling 模式全部网页轮询
      const type = tgMode.value === 'userbot' ? 'userbot'
        : tgMode.value === 'both' ? (chTypes[name] || 'polling') : 'polling'
      try {
        await api.post('/api/channels', { name, type })
        ok++
      } catch (e) {
        const msg = errMsg(e)
        // 重试时已添加成功的行会返回“已存在”，按成功处理
        if (msg.includes('已存在')) ok++
        else fails.push(`${name}（${msg}）`)
      }
    }
    if (fails.length) {
      ElMessageBox.alert(
        `以下频道未通过访问校验，请修正后重试：<br/><br/>${fails.join('<br/>')}`,
        '频道添加失败',
        { dangerouslyUseHTMLString: true, confirmButtonText: '知道了', type: 'error' },
      ).catch(() => {})
      return
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
  } catch (e) {
    // 向导完成失败：留在当前页，避免进入未完成初始化的仪表盘
    ElMessage.error(errMsg(e))
    starting.value = false
    return
  }
  try {
    await api.post('/api/system/start')
    ElMessage.success('🚀 系统已开始运行')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    starting.value = false
  }
  router.push('/dashboard')
}
const finish = async () => {
  try {
    await api.post('/api/wizard/complete')
    router.push('/dashboard')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
</script>

<style scoped>
.setup-bg { padding: 40px 0; }
.setup-card { width: min(640px, calc(100vw - 24px)); border-radius: 16px; border: none;
              box-shadow: 0 24px 70px rgba(5, 18, 48, .45) !important; }
.step-desc { text-align: center; color: #666; margin: 4px 0 18px; }
.qr-box { width: 200px; margin: 0 auto; cursor: pointer; background: #fff; padding: 8px; border-radius: 8px; }
.qr-box :deep(svg) { width: 100%; height: auto; display: block; }
.qr-err { color: #f56c6c; font-size: 13px; margin: 4px; }
.ub-wizard {
  border: 1px solid var(--el-color-primary-light-7); background: var(--el-color-primary-light-9);
  border-radius: 10px; padding: 14px 16px;
}
.ub-wizard-tip { margin: 0 0 10px; font-size: 13px; color: #606266; }
.ub-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.ub-warn { margin: 10px 0 0; font-size: 12px; color: #e6a23c; }
.ch-type-box {
  margin-top: 12px; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 14px; max-height: 220px; overflow-y: auto;
}
.ch-type-tip { margin: 0 0 10px; font-size: 12px; color: #909399; }
.ch-type-row { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
.ch-type-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.ready-box {
  display: flex; flex-direction: column; gap: 10px;
  border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 14px 16px;
  text-align: left;
}
.ready-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #303133; }
.ready-item .el-icon.ok { color: #67c23a; }
.ready-item .el-icon.bad { color: #f56c6c; }
.ready-item .el-button { margin-left: auto; }
</style>
