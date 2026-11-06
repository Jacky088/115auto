<template>
  <el-card shadow="never" class="page-card" v-if="loaded">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <b>系统设置</b>
        <el-tag v-if="dirty" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
        <span style="margin-left: auto" class="tip">修改需点击下方「保存设置」后生效</span>
      </div>
    </template>
    <el-form :label-width="isMobile ? 90 : 130" label-position="right" class="settings-form">
      <div class="settings-flow">
          <div class="section-card"><div class="section-title">功能开关</div>
          <el-form-item label="115 分享转存">
            <div class="switch-row">
              <el-switch v-model="f.enable_share_transfer" />
              <span class="tip">关闭后新消息中的分享链接不再转存，排队任务自动跳过</span>
            </div>
          </el-form-item>
          <el-form-item label="云下载（ED2K）">
            <div class="switch-row">
              <el-switch v-model="f.enable_offline_download" />
              <span class="tip">关闭后新消息中的电驴/磁力链接不再提交云下载，排队任务自动跳过</span>
            </div>
          </el-form-item>

          </div>

          <div class="section-card"><div class="section-title">Telegram</div>
          <el-form-item label="接入方式">
            <el-radio-group v-model="f.tg_mode">
              <el-radio value="polling">网页轮询</el-radio>
              <el-radio value="userbot">Userbot</el-radio>
              <el-radio value="both">两者</el-radio>
            </el-radio-group>
            <div class="tip" style="width: 100%">{{ tgModeTip }}<br />
              ⚠️ 接入方式的变更保存后需<b>重启程序</b>才会启停对应监听（重启容器：<code>docker restart autosave115</code>）</div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'userbot'" label="轮询间隔（秒）">
            <div class="range-row">
              <el-input-number v-model="f.poll_interval_min" :min="10" controls-position="right" />
              <span class="sep">~</span>
              <el-input-number v-model="f.poll_interval_max" :min="15" controls-position="right" />
              <span class="tip">区间内随机，降低被识别风险；仅作用于网页轮询频道</span>
            </div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'polling'" label="Userbot">
            <div class="ub-inline">
              <el-tag :type="ub.connected ? 'success' : 'danger'" size="small">
                {{ ub.connected ? '已连接' : '未连接' }}
              </el-tag>
              <span class="tip">{{ ub.connected
                ? 'TG 账号监听运行中（实时接收私有频道消息）'
                : '尚未登录：请到「账号管理」完成 Userbot 登录，否则纯 Userbot 频道无法收到消息' }}</span>
            </div>
          </el-form-item>

          <el-form-item label="TG 代理">
            <div class="range-row" style="width: 100%">
              <el-input v-model="f.proxy" placeholder="socks5://127.0.0.1:1080 或 http://127.0.0.1:7890（仅 TG 流量走代理，留空直连）" />
              <el-button :loading="proxyTesting" @click="testProxy">测试代理</el-button>
            </div>
            <div class="tip" style="width: 100%">
              国内部署必填：所有 TG 流量（频道抓取/补漏/按钮解析/通知/Bot 指令）都走此代理，
              115 直连不受影响。测试失败会提示具体原因。<b>注意</b>：代理变更对网页轮询/Bot 即时生效，
              但已连接的 Userbot 需重启程序或在「账号管理」重新登录后才会改走新代理。
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">转存控制</div>
          <el-form-item label="转存间隔（秒）">
            <div class="range-row">
              <el-input-number v-model="f.transfer_interval_min" :min="5" controls-position="right" />
              <span class="sep">~</span>
              <el-input-number v-model="f.transfer_interval_max" :min="10" controls-position="right" />
            </div>
          </el-form-item>
          <el-form-item label="每日转存上限">
            <div class="range-row">
              <el-input-number v-model="f.daily_limit" :min="0" controls-position="right" />
              <span class="tip">0 表示不限制</span>
            </div>
          </el-form-item>
          <el-form-item label="分享体积下限">
            <div class="range-row">
              <el-input-number v-model="f.min_size_gb" :min="0" :step="0.1" controls-position="right" />
              <span class="tip">GB，低于此值的分享视为广告自动跳过；0 表示关闭</span>
            </div>
          </el-form-item>
          <el-form-item label="重试退避（秒）">
            <div class="range-row">
              <el-input v-model="retryText" placeholder="60, 300, 1800" style="width: 220px" />
              <span class="tip">逗号分隔，失败后逐级延长重试间隔</span>
            </div>
          </el-form-item>
          <el-form-item label="定时计划">
            <div class="switch-row">
              <el-switch v-model="f.schedule_enabled" />
              <span class="tip">开启后仅在允许时段内转存/云下载（队列保留，时段外自动等待）</span>
            </div>
            <div class="range-row" style="margin-top: 8px" v-if="f.schedule_enabled">
              <el-time-select v-model="f.schedule_start" start="00:00" step="00:30" end="23:30"
                              placeholder="开始" style="width: 110px" />
              <span class="sep">~</span>
              <el-time-select v-model="f.schedule_end" start="00:00" step="00:30" end="23:30"
                              placeholder="结束" style="width: 110px" />
              <span class="tip">支持跨零点（如 22:00 ~ 08:00）</span>
            </div>
          </el-form-item>

          </div>

          <div class="section-card"><div class="section-title">通知</div>
          <el-form-item label="转存结果通知">
            <div class="switch-row">
              <el-switch v-model="f.notify_transfer_success" />
              <span class="tip">转存成功/失败、云下载提交/完成/失败都会推送 TG 消息（含具体原因）</span>
            </div>
          </el-form-item>
          <el-form-item label="TG Bot Token">
            <el-input v-model="f.notify_token" placeholder="从 @BotFather 获取" />
          </el-form-item>
          <el-form-item label="Chat ID">
            <div class="range-row">
              <el-input v-model="f.notify_chat_id" placeholder="你的用户 ID" style="width: 220px" />
              <el-button type="primary" plain :loading="testing" @click="testNotify">发送测试消息</el-button>
            </div>
            <div class="tip" style="width: 100%">
              填写后无需保存即可直接测试；测试失败会提示具体原因。收不到时检查：① Token 是否完整复制
              ② 是否先给 Bot 发送过 /start ③ 大陆服务器需在上方配置 TG 代理
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">转存增强</div>
          <el-form-item label="按文件名去重">
            <div class="switch-row">
              <el-switch v-model="f.dedup_by_name" />
              <span class="tip">开启后转存前再按文件名查重：网盘同名目录下已存在同名文件时自动跳过</span>
            </div>
          </el-form-item>
          <el-form-item label="自动整理">
            <div class="switch-row">
              <el-switch v-model="f.organize_enabled" />
              <span class="tip">开启后转存完成自动按「剧名/季/集」结构整理文件并重命名</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">TMDB 刮削</div>
          <el-form-item label="TMDB 刮削">
            <div class="switch-row">
              <el-switch v-model="f.tmdb_enabled" />
              <span class="tip">开启后自动从 TMDB 获取剧集/电影元数据与海报，配合「自动整理」使用</span>
            </div>
          </el-form-item>
          <el-form-item label="TMDB API Key">
            <el-input v-model="f.tmdb_api_key" placeholder="在 themoviedb.org 免费申请的 API Key（v3）" />
            <div class="tip" style="width: 100%">国内直连可能不稳定；已配置「TG 代理」时刮削流量同样走代理</div>
          </el-form-item>
          <el-form-item label="刮削语言">
            <el-input v-model="f.tmdb_language" placeholder="zh-CN" style="width: 180px" />
          </el-form-item>
          <el-form-item label="元数据子目录">
            <div class="switch-row">
              <el-switch v-model="f.tmdb_subdir" />
              <span class="tip">开启后将海报/NFO 等刮削产物放入媒体目录内的独立子目录</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">通知渠道扩展</div>
          <el-form-item label="Bark">
            <el-input v-model="f.bark_url" placeholder="https://api.day.app/你的key" />
            <div class="tip" style="width: 100%">iOS Bark 推送，形如 <code>https://api.day.app/你的key</code></div>
          </el-form-item>
          <el-form-item label="PushPlus">
            <el-input v-model="f.pushplus_token" placeholder="pushplus.plus 的 Token" />
          </el-form-item>
          <el-form-item label="ntfy">
            <el-input v-model="f.ntfy_url" placeholder="https://ntfy.sh/你的主题" />
            <div class="tip" style="width: 100%">完整的 ntfy 主题 URL，如 <code>https://ntfy.sh/my-115-topic</code></div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">每日摘要</div>
          <el-form-item label="每日摘要">
            <div class="switch-row">
              <el-switch v-model="f.digest_enabled" />
              <span class="tip">开启后每天定时把当日转存汇总推送到已配置的通知渠道（TG / Bark / PushPlus / ntfy）</span>
            </div>
          </el-form-item>
          <el-form-item v-if="f.digest_enabled" label="推送时间">
            <el-time-select v-model="f.digest_time" start="00:00" step="00:30" end="23:30"
                            placeholder="选择时间" style="width: 130px" />
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">定时自动备份</div>
          <el-form-item label="自动备份">
            <div class="switch-row">
              <el-switch v-model="f.auto_backup_enabled" />
              <span class="tip">开启后定时把设置/频道/规则/订阅/转存记录打包推送到 TG Bot，可直接转发回 Bot 恢复</span>
            </div>
          </el-form-item>
          <el-form-item label="备份间隔（天）">
            <div class="range-row">
              <el-input-number v-model="f.auto_backup_days" :min="1" :max="365" controls-position="right" />
              <span class="tip">1-365 天</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">对外 API</div>
          <el-form-item label="API Token">
            <div class="range-row" style="width: 100%">
              <el-input v-model="f.api_token" placeholder="留空关闭对外 API" clearable />
              <el-button @click="genToken">生成随机 Token</el-button>
            </div>
            <div class="tip" style="width: 100%">
              留空关闭。开启后调用方携带请求头 <code>X-API-Token</code> 访问 <code>/api/ext/v1/*</code> 接口
            </div>
          </el-form-item>
          </div>
      </div>

      <div class="btn-row" style="margin-top: 10px">
        <el-button type="primary" :disabled="!dirty" @click="save">保存设置</el-button>
        <el-button @click="backupExport">导出数据备份</el-button>
        <span class="tip" style="margin-left: 12px">
          备份含设置/频道/规则/黑名单/转存记录（JSON）；恢复时直接替换服务器 data 目录
        </span>
      </div>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'

const f = ref({})
const loaded = ref(false)
const retryText = ref('')
const testing = ref(false)
const dirty = ref(false)
const isMobile = ref(window.innerWidth <= 768)
const onRz = () => { isMobile.value = window.innerWidth <= 768 }
useUnsavedGuard(dirty)  // 有未保存更改时，离开页面弹窗确认
onMounted(() => window.addEventListener('resize', onRz))
onUnmounted(() => window.removeEventListener('resize', onRz))
let suppressWatch = false

// Userbot 连接状态（接入方式 ≠ 网页轮询时展示）
const ub = reactive({ connected: false })
const loadUb = async () => {
  try { ub.connected = !!(await api.get('/api/tg/userbot/status')).data.connected } catch { /* ignore */ }
}
watch(() => f.value.tg_mode, (v) => { if (v && v !== 'polling') loadUb() })

const tgModeTip = computed(() => ({
  polling: '当前模式：网页轮询——只抓取接入方式为「网页轮询」的频道（公开频道，免 TG 账号）',
  userbot: '当前模式：Userbot——用 TG 小号实时监听接入方式为「Userbot」的频道（含私有频道）',
  both: '当前模式：两者——按每个频道自身的接入方式分流处理，互不影响',
}[f.value.tg_mode] || ''))

watch([f, retryText], () => {
  if (!suppressWatch) dirty.value = true
}, { deep: true })

const load = async () => {
  suppressWatch = true
  f.value = (await api.get('/api/settings')).data
  retryText.value = (f.value.retry_backoff || []).join(', ')
  if (f.value.tg_mode && f.value.tg_mode !== 'polling') loadUb()
  loaded.value = true
  await nextTick()
  suppressWatch = false
  dirty.value = false
}
const save = async () => {
  const backoff = retryText.value.split(',').map((s) => parseInt(s.trim())).filter((n) => n > 0)
  if (!backoff.length) {
    ElMessage.error('重试退避至少需要填写一个大于 0 的秒数，例如 60, 300, 1800')
    return
  }
  try {
    await api.put('/api/settings', { ...f.value, retry_backoff: backoff })
    dirty.value = false
    saveOk('设置已保存', '转存间隔、限额、开关等已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const backupExport = async () => {
  try {
    const resp = await api.get('/api/backup/export', { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    a.download = m ? m[1] : 'autosave_backup.json'
    a.click()
    URL.revokeObjectURL(url)
    saveOk('备份已导出', '包含设置/频道/规则/黑名单/转存记录')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const proxyTesting = ref(false)
const testProxy = async () => {
  proxyTesting.value = true
  try {
    const { data } = await api.post('/api/settings/proxy/test', { proxy: f.value.proxy })
    saveOk('代理可用', data.detail)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { proxyTesting.value = false }
}
const testNotify = async () => {
  testing.value = true
  try {
    await api.post('/api/settings/notify/test', {
      token: f.value.notify_token,
      chat_id: f.value.notify_chat_id,
    })
    ElMessage.success('✅ 测试消息已发送，请查看 Telegram')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    testing.value = false
  }
}
// 生成随机对外 API Token（32 位十六进制），点击「保存设置」后才落库生效
const genToken = () => {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  f.value.api_token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  ElMessage.success('已生成随机 Token，点击「保存设置」后生效')
}
onMounted(load)
</script>

<style scoped>
.section-card {
  border: 1px solid var(--saas-line);
  border-radius: 12px;
  padding: 16px 20px 4px;
  margin-bottom: 16px;
  background: #fbfdff;
}
.section-title {
  font-weight: 600; font-size: 14px; color: var(--el-color-primary-dark-2);
  margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
}
.section-title::before {
  content: ""; width: 4px; height: 14px; border-radius: 2px;
  background: linear-gradient(180deg, #2f6bff, #5d8bff);
}
@media (max-width: 768px) {
  .settings-form :deep(.el-form-item__label) { font-size: 12px; line-height: 1.4; }
  .switch-row { flex-wrap: wrap; }
  .switch-row .tip { flex-basis: 100%; }
  .range-row { flex-wrap: wrap; }
}
.settings-form :deep(.el-form-item__content) { min-width: 0; }
.settings-flow { max-width: 980px; }
@media (max-width: 768px) { .section-card { padding: 12px 14px 2px; } }
.tip { color: #999; font-size: 12px; }
.switch-row { display: flex; align-items: center; gap: 10px; }
.ub-inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.switch-row .tip { flex: 1; min-width: 0; }
.range-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.range-row :deep(.el-input-number) { width: 130px; }
.sep { color: #999; }
</style>
