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
          <el-descriptions-item label="说明">转存通过 Cookie 完成，系统每 10 分钟自动巡检有效性。国内服务器推荐扫码登录；海外服务器扫码易被风控，可点击「手动导入 Cookie」</el-descriptions-item>
        </el-descriptions>
        <div class="btn-row" style="margin-top: 14px">
          <el-button type="primary" @click="openQr">扫码登录 / 重新登录</el-button>
          <el-button @click="cookieVisible = true">手动导入 Cookie</el-button>
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
          <el-descriptions-item label="说明">用于实时监听频道（含私密频道）。建议使用小号；api_id/api_hash 从 my.telegram.org 申请</el-descriptions-item>
        </el-descriptions>
        <div class="btn-row" style="margin-top: 14px">
          <el-button type="primary" @click="tgVisible = true">登录 / 重新登录</el-button>
          <el-button :loading="tgTesting" @click="runTgTest">测试连接与频道</el-button>
        </div>
        <div v-if="tgTestResult" class="tg-test">
          <el-alert v-if="!tgTestResult.ok && tgTestResult.err" :title="tgTestResult.err"
                    type="error" :closable="false" show-icon />
          <template v-else>
            <el-alert :title="tgTestResult.ok ? `连接与授权正常${tgTestResult.me ? '（' + tgTestResult.me + '）' : ''}${tgTestResult.channels.length ? '，已启用频道全部可访问' : ''}` : '部分频道无法访问，请检查账号是否已加入'"
                      :type="tgTestResult.ok ? 'success' : 'warning'" :closable="false" show-icon />
            <div v-for="c in tgTestResult.channels" :key="c.name" class="tg-ch">
              <el-icon :size="14" :class="c.ok ? 'ok' : 'bad'">
                <CircleCheckFilled v-if="c.ok" /><CircleCloseFilled v-else />
              </el-icon>
              <span class="tg-ch-name">{{ c.name }}</span>
              <span v-if="c.ok" class="tg-ch-sub">{{ c.title }}</span>
              <span v-else class="tg-ch-err">{{ c.err }}</span>
            </div>
          </template>
        </div>
      </el-card>
  </div>

  <!-- 115 扫码对话框 -->
  <el-dialog v-model="qrVisible" title="115 扫码登录" width="360px" @close="stopQrPoll">
    <div style="text-align: center; min-height: 220px">
      <template v-if="qr.svg">
        <div class="qr-box" v-html="qr.svg" @click="createQr"></div>
        <p v-if="qr.err" class="qr-err">❌ {{ qr.err }}</p>
        <p v-else-if="qr.scanned" style="color: #67c23a; margin: 4px">✅ 已扫描，请在手机上确认</p>
        <p v-else style="color: #999; font-size: var(--fs-caption)">请用 115 App 扫码，点击二维码可刷新</p>
      </template>
      <el-button v-else type="primary" :loading="qr.loading" @click="createQr">生成二维码</el-button>
    </div>
    <div v-if="qr.err" style="text-align: center">
      <el-button text type="primary" @click="cookieVisible = true; qrVisible = false">
        扫码被风控？改用手动导入 Cookie →
      </el-button>
    </div>
  </el-dialog>

  <!-- 手动导入 Cookie -->
  <CookieImportDialog v-model="cookieVisible" @success="load" />

  <!-- Userbot 登录对话框 -->
  <el-dialog v-model="tgVisible" title="Userbot 登录" width="480px" @close="load">
    <UserbotLogin compact @update="load" />
  </el-dialog>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'
import CookieImportDialog from '../components/CookieImportDialog.vue'
import UserbotLogin from '../components/UserbotLogin.vue'

const s115 = ref({})
const tg = ref({})
const qrVisible = ref(false)
const cookieVisible = ref(false)
const qr = reactive({ loading: false, svg: '', token: null, app: '', scanned: false, err: '' })
let pollSeq = 0   // 轮询会话号：关闭弹窗/重新生成二维码时 +1 使旧循环退出
let pollErrCount = 0

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const stopQrPoll = () => { pollSeq++ }

const load = async () => {
  s115.value = (await api.get('/api/account/115/status')).data
  tg.value = (await api.get('/api/tg/userbot/status')).data
}

// ---- 115 QR ----
const openQr = () => { qrVisible.value = true; qr.svg = ''; createQr() }
const createQr = async () => {
  const my = ++pollSeq
  qr.loading = true
  qr.scanned = false
  qr.svg = ''
  qr.err = ''
  pollErrCount = 0
  try {
    const { data } = await api.post('/api/account/115/qrcode')
    if (my !== pollSeq) return
    qr.svg = data.qr_svg
    qr.token = data.token
    qr.app = data.app
    pollLoop(my)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { qr.loading = false }
}

// 115 状态接口是长轮询（挂起最长 30s）：必须等上一次返回后再发下一次，
// 否则并发请求堆积，手机确认瞬间会同时触发多次取 cookie，触发 115 风控导致登录失败
const pollLoop = async (my) => {
  while (my === pollSeq && qr.token) {
    try {
      const { data } = await api.post('/api/account/115/qrcode/poll',
        { token: qr.token, app: qr.app }, { timeout: 45000 })
      if (my !== pollSeq) return
      if (data.status === 1) qr.scanned = true
      if (data.status === 2) {
        if (data.ok) {
          ElMessage.success('115 登录成功')
          qrVisible.value = false
          load()
        } else {
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

// ---- TG userbot：登录弹窗复用 UserbotLogin 组件；卡片级一键测试 ----
const tgVisible = ref(false)
const tgTesting = ref(false)
const tgTestResult = ref(null)

const runTgTest = async () => {
  tgTesting.value = true
  tgTestResult.value = null
  try {
    tgTestResult.value = (await api.post('/api/tg/userbot/test')).data
  } catch (e) {
    tgTestResult.value = { ok: false, err: errMsg(e) }
  } finally { tgTesting.value = false }
}

onMounted(load)
onUnmounted(stopQrPoll)
</script>

<style scoped>
.account-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: stretch; }
@media (max-width: 1100px) { .account-grid { grid-template-columns: 1fr; } }
/* 两张卡片撑满等高，视觉对齐 */
.account-grid > .el-card { height: 100%; }
/* 标签不换行（如「登录状态」），内容列自适应 */
.account-grid :deep(.el-descriptions__label) { white-space: nowrap; }
.qr-box { width: 200px; margin: 0 auto; cursor: pointer; background: #fff; padding: 8px; border-radius: 8px; }
.qr-box :deep(svg) { width: 100%; height: auto; display: block; }
.qr-err { color: #f56c6c; font-size: var(--fs-body); margin: 4px; }
.tg-test { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.tg-ch { display: flex; align-items: center; gap: 6px; font-size: var(--fs-body); }
.tg-ch .ok { color: #67c23a; }
.tg-ch .bad { color: #f56c6c; }
.tg-ch-name { font-weight: 600; }
.tg-ch-sub { color: #909399; }
.tg-ch-err { color: #f56c6c; font-size: var(--fs-caption); }
</style>
