<template>
  <el-card shadow="never" class="page-card" v-if="loaded">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <b>系统设置</b>
        <el-tag v-if="dirty" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
        <span style="margin-left: auto" class="tip">修改需点击「保存设置」后生效</span>
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
              <span class="warn-text">⚠️ 接入方式的变更保存后需重启程序，才会开启/停止对应监听（重启容器：<code>docker restart autosave115</code>）</span>
            </div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'userbot'" label="轮询间隔">
            <div class="range-row">
              <el-select v-model="pollPreset" style="width: 220px">
                <el-option v-for="p in POLL_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip">每轮轮询在区间内随机等待，降低被识别风险；仅作用于网页轮询频道</span>
            </div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'polling'" label="Userbot">
            <div class="ub-inline">
              <el-tag :type="ub.connected ? 'success' : 'danger'" size="small">
                {{ ub.connected ? '已连接' : '未连接' }}
              </el-tag>
              <span class="tip">{{ ub.connected
                ? 'TG 账号监听运行中（实时接收私密频道消息）'
                : '尚未登录：请到「账号管理」完成 Userbot 登录，否则接入方式为 Userbot 的频道无法收到消息' }}</span>
            </div>
          </el-form-item>

          <el-form-item label="TG 代理">
            <div class="range-row" style="width: 100%">
              <el-input v-model="f.proxy" placeholder="socks5://127.0.0.1:1080 或 http://127.0.0.1:7890（仅 TG 流量走代理，留空直连）" />
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

          <div class="section-card"><div class="section-title">转存控制</div>
          <el-form-item label="转存间隔">
            <div class="range-row">
              <el-select v-model="transferPreset" style="width: 220px">
                <el-option v-for="p in TRANSFER_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip">每笔转存在区间内随机等待；频繁触发风控请选「稳健」</span>
            </div>
          </el-form-item>
          <el-form-item label="每日转存上限">
            <div class="range-row">
              <el-input-number v-model="f.daily_limit" :min="1" :max="50" controls-position="right" />
              <span class="tip">1~50；115 单账号每日转存的安全上限，不建议设为最大值</span>
            </div>
          </el-form-item>
          <el-form-item label="分享体积下限">
            <div class="range-row">
              <el-input-number v-model="f.min_size_gb" :min="0" :step="0.1" controls-position="right" />
              <span class="tip">单位 GB；低于此值的分享视为广告并自动跳过，0 表示关闭</span>
            </div>
          </el-form-item>
          <el-form-item label="失败重试">
            <div class="range-row">
              <el-select v-model="retryPreset" style="width: 220px">
                <el-option v-for="p in RETRY_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip">失败后按预设间隔逐级重试（共 3 次）</span>
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

          <div class="section-card"><div class="section-title">TG 通知</div>
          <el-form-item label="转存结果通知">
            <div class="switch-row">
              <el-switch v-model="f.notify_transfer_success" />
              <span class="tip">转存成功/失败、云下载提交/完成/失败都会推送 TG 消息（含具体原因）</span>
            </div>
          </el-form-item>
          <el-form-item label="TG 推送">
            <SecretConfig :configured="!!(f.notify_token && f.notify_chat_id)" :save="saveNotify"
                          @open="openNotify" @cancel="resetNotify">
              <div class="range-row" style="width: 100%">
                <el-input v-model="nf.token" type="password" show-password
                          placeholder="TG Bot Token（@BotFather 获取）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="nf.chat_id" placeholder="Chat ID（你的用户 ID）" style="width: 220px" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button type="primary" plain size="small" :loading="testing"
                           @click="testNotify(nf.token, nf.chat_id)">发送测试消息</el-button>
              </div>
              <div class="tip" style="width: 100%">
                填写后无需保存即可直接测试；测试失败会提示具体原因。收不到时检查：① Token 是否完整复制
                ② 是否已给 Bot 发送 /start ③ 国内服务器需在上方「TG 代理」处填写代理地址
              </div>
            </SecretConfig>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">转存增强</div>
          <el-form-item label="分享追更">
            <div class="switch-row">
              <el-switch v-model="f.share_update_enabled" />
              <span class="tip">同一分享链接再次出现时不再判重：自动比对目标目录，只转存新增的集数文件，已存过的不重复（适配分享方往同一链接持续加更的追剧场景）</span>
            </div>
          </el-form-item>
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

          <div class="section-card"><div class="section-title">通知渠道扩展</div>
          <el-form-item label="Bark（iOS）">
            <SecretConfig :configured="!!f.bark_url" :save="saveBark"
                          @open="openBark" @cancel="resetBark">
              <div class="range-row" style="width: 100%">
                <el-input v-model="bf.url" type="password" show-password
                          placeholder="https://api.day.app/你的key" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'bark'" @click="testChannel('bark')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                iOS Bark 推送：App Store 安装 Bark 后，复制 App 内显示的完整推送 URL
                （形如 <code>https://api.day.app/你的key</code>，自建服务器换域名即可）
              </div>
            </SecretConfig>
          </el-form-item>
          <el-form-item label="PushPlus">
            <SecretConfig :configured="!!f.pushplus_token" :save="savePushPlus"
                          @open="openPushPlus" @cancel="resetPushPlus">
              <div class="range-row" style="width: 100%">
                <el-input v-model="pf.token" type="password" show-password
                          placeholder="pushplus.plus 的 Token" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="pf.topic" placeholder="群组编码（可选，留空发本人）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'pushplus'" @click="testChannel('pushplus')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                微信推送：官网 pushplus.plus 微信扫码登录后复制 Token，默认发给本人微信；
                需一对多群发时填「一对一推送 → 群组管理」里创建的群组编码
              </div>
            </SecretConfig>
          </el-form-item>
          <el-form-item label="ntfy">
            <SecretConfig :configured="!!f.ntfy_url" :save="saveNtfy"
                          @open="openNtfy" @cancel="resetNtfy">
              <div class="range-row" style="width: 100%">
                <el-input v-model="tf.url" placeholder="https://ntfy.sh/你的主题" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="tf.token" type="password" show-password
                          placeholder="访问令牌（可选，自建 ntfy 开启鉴权时填）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'ntfy'" @click="testChannel('ntfy')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                完整的 ntfy 主题 URL，如 <code>https://ntfy.sh/my-115-topic</code>（自建服务器换域名）；
                手机装 ntfy App 订阅同一主题即可收推送。主题名建议加随机后缀防止被他人订阅；
                使用官方 ntfy.sh 时访问令牌留空即可
              </div>
            </SecretConfig>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">每日摘要</div>
          <el-form-item label="每日摘要">
            <div class="switch-row">
              <el-switch v-model="f.digest_enabled" />
              <el-button :loading="digestTesting" @click="testDigest">立即推送一次</el-button>
            </div>
            <div class="tip" style="width: 100%">
              开启后每天定时把当日转存汇总推送到已配置的通知渠道（TG / Bark / PushPlus / ntfy）；
              「立即推送一次」用与定时推送完全相同的内容验证渠道是否可达
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
              <span class="tip">开启后定时把设置/频道/规则/黑名单/订阅/转存记录打包推送到 TG Bot，可直接转发回 Bot 恢复</span>
            </div>
          </el-form-item>
          <el-form-item label="备份间隔（天）">
            <div class="range-row">
              <el-input-number v-model="f.auto_backup_days" :min="1" :max="365" controls-position="right" />
              <span class="tip">1~365 天</span>
            </div>
          </el-form-item>
          <el-form-item label="从备份恢复">
            <div class="range-row" style="width: 100%">
              <input type="file" ref="restoreInput" accept=".json,application/json"
                     style="display: none" @change="onRestoreFile" />
              <el-button @click="$refs.restoreInput.click()">选择备份 JSON 文件</el-button>
              <span v-if="restoreFile" class="selected-count">
                {{ restoreFile.name }}（{{ Math.max(1, Math.round(restoreFile.size / 1024)) }} KB）
              </span>
              <el-button type="danger" plain :loading="restoring" :disabled="!restoreFile"
                         @click="doRestore">恢复数据</el-button>
            </div>
            <div class="tip" style="width: 100%">
              用备份文件<b class="warn-text">整体覆盖</b>当前的设置/频道/规则/黑名单/订阅/转存记录
              （登录密码与通知密钥不变），恢复后系统回到停止状态。也可把 TG 收到的备份文件发给 Bot，
              回复 <code>/restore confirm</code> 恢复
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">对外 API</div>
          <el-form-item label="API Token">
            <div class="range-row" style="width: 100%">
              <el-input v-model="f.api_token" type="password" show-password
                        placeholder="未设置时对外 API 关闭" clearable />
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
          备份含设置/频道/规则/黑名单/订阅/转存记录（JSON，不含登录密码等敏感信息）
        </span>
      </div>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, saveOk } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'

const f = ref({})
const loaded = ref(false)
// 失败重试只提供预设档位（不开放自定义秒数，避免误配过密触发风控）
const RETRY_PRESETS = [
  { label: '快速（30秒/2分/10分）', sec: [30, 120, 600] },
  { label: '标准（1分/5分/30分）', sec: [60, 300, 1800] },
  { label: '稳健（5分/30分/2小时）', sec: [300, 1800, 7200] },
]
const retryPreset = ref('标准（1分/5分/30分）')
// 轮询/转存间隔同样只提供档位：区间随档位固定，防止自定义过密触发风控
const POLL_PRESETS = [
  { label: '快速（15~45秒）', min: 15, max: 45 },
  { label: '标准（30秒~2分）', min: 30, max: 120 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]
const pollPreset = ref('标准（30秒~2分）')
const TRANSFER_PRESETS = [
  { label: '快速（30秒~1分）', min: 30, max: 60 },
  { label: '标准（1~3分）', min: 60, max: 180 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]
const transferPreset = ref('标准（1~3分）')
const testing = ref(false)
const chTesting = ref('') // 正在测试的通知渠道（bark/pushplus/ntfy）
const dirty = ref(false)

// ---- 密钥类配置的独立编辑/保存（参考 TMDB API Key 的「已配置 + 更换」模式）----
// 每块用独立 draft，编辑时不联动底部表单，保存成功后同步回 f（避免底部保存把旧值覆盖回去）
import SecretConfig from '../components/SecretConfig.vue'
const nf = reactive({ token: '', chat_id: '' })   // TG 通知
const bf = reactive({ url: '' })                  // Bark
const pf = reactive({ token: '', topic: '' })     // PushPlus
const tf = reactive({ url: '', token: '' })       // ntfy

const syncF = (patch) => {
  suppressWatch = true
  Object.assign(f.value, patch)
  nextTick(() => { suppressWatch = false })
}

const openNotify = () => { nf.token = ''; nf.chat_id = f.value.notify_chat_id || '' }
const resetNotify = () => { nf.token = ''; nf.chat_id = f.value.notify_chat_id || '' }
const saveNotify = async () => {
  if (!nf.token.trim() || !nf.chat_id.trim()) { ElMessage.error('请完整填写 Bot Token 和 Chat ID'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { notify_token: nf.token.trim(), notify_chat_id: nf.chat_id.trim() })
    syncF({ notify_token: nf.token.trim(), notify_chat_id: nf.chat_id.trim() })
    saveOk('TG 通知配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openBark = () => { bf.url = '' }
const resetBark = () => { bf.url = '' }
const saveBark = async () => {
  if (!bf.url.trim()) { ElMessage.error('请填写 Bark 推送 URL'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { bark_url: bf.url.trim() })
    syncF({ bark_url: bf.url.trim() })
    saveOk('Bark 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openPushPlus = () => { pf.token = ''; pf.topic = f.value.pushplus_topic || '' }
const resetPushPlus = () => { pf.token = ''; pf.topic = f.value.pushplus_topic || '' }
const savePushPlus = async () => {
  if (!pf.token.trim()) { ElMessage.error('请填写 PushPlus Token'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { pushplus_token: pf.token.trim(), pushplus_topic: pf.topic.trim() })
    syncF({ pushplus_token: pf.token.trim(), pushplus_topic: pf.topic.trim() })
    saveOk('PushPlus 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openNtfy = () => { tf.url = ''; tf.token = '' }
const resetNtfy = () => { tf.url = ''; tf.token = '' }
const saveNtfy = async () => {
  if (!tf.url.trim()) { ElMessage.error('请填写 ntfy 主题 URL'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { ntfy_url: tf.url.trim(), ntfy_token: tf.token.trim() })
    syncF({ ntfy_url: tf.url.trim(), ntfy_token: tf.token.trim() })
    saveOk('ntfy 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

// ---- 从备份恢复（网页上传）----
const restoreInput = ref(null)
const restoreFile = ref(null)
const restoring = ref(false)
const onRestoreFile = (ev) => {
  const file = ev.target.files?.[0]
  restoreFile.value = null
  if (!file) return
  if (file.size > 20 * 1024 * 1024) { ElMessage.error('文件超过 20MB 上限，无法作为备份导入'); return }
  restoreFile.value = file
  ev.target.value = '' // 允许重复选择同一文件
}
const doRestore = async () => {
  const file = restoreFile.value
  if (!file) return
  restoring.value = true
  try {
    const text = await file.text()
    let data
    try { data = JSON.parse(text) } catch { throw new Error('文件不是合法 JSON') }
    if (!data?._meta || !data.settings) throw new Error('不是本程序的备份文件（缺少备份标记）')
    const kb = Math.max(1, Math.round(file.size / 1024))
    await ElMessageBox.confirm(
      `备份导出于 ${data._meta.exported_at || '未知时间'}（${kb} KB），包含：` +
      `频道 ${(data.channels || []).length}、规则 ${(data.rules || []).length}、` +
      `订阅 ${(data.subscriptions || []).length}、转存记录 ${(data.transfers || []).length}。` +
      `恢复将覆盖当前全部业务数据（登录密码不变），且系统回到停止状态。确定继续？`,
      '确认恢复备份', { confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'warning' })
    const { data: resp } = await api.post('/api/settings/backup/restore', data)
    await ElMessageBox.alert(
      `恢复完成：设置 ${resp.stats.settings} 项、频道 ${resp.stats.channels} 个、` +
      `规则 ${resp.stats.rules} 条、订阅 ${resp.stats.subscriptions} 条、转存记录 ${resp.stats.transfers} 条。` +
      `页面即将刷新以加载恢复后的数据。`, '恢复成功', { confirmButtonText: '刷新页面' })
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(errMsg(e))
  } finally {
    restoring.value = false
  }
}
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
  userbot: '当前模式：Userbot——用 TG 小号实时监听接入方式为「Userbot」的频道（含私密频道）',
  both: '当前模式：两者——按每个频道自身的接入方式分流处理，互不影响',
}[f.value.tg_mode] || ''))

watch([f, retryPreset, pollPreset, transferPreset], () => {
  if (!suppressWatch) dirty.value = true
}, { deep: true })

const load = async () => {
  suppressWatch = true
  f.value = (await api.get('/api/settings')).data
  // 回填三组预设：与已存值完全一致的档位；对不上时回落到标准档（保存时生效）
  const hit = (arr, cur, pick) => {
    const m = arr.find((p) => pick(p).every((n, i) => n === cur[i]))
    return (m || arr[1]).label
  }
  retryPreset.value = hit(RETRY_PRESETS, f.value.retry_backoff || [], (p) => p.sec)
  pollPreset.value = hit(POLL_PRESETS, [f.value.poll_interval_min, f.value.poll_interval_max],
                         (p) => [p.min, p.max])
  transferPreset.value = hit(TRANSFER_PRESETS, [f.value.transfer_interval_min, f.value.transfer_interval_max],
                             (p) => [p.min, p.max])
  if (f.value.tg_mode && f.value.tg_mode !== 'polling') loadUb()
  loaded.value = true
  await nextTick()
  suppressWatch = false
  dirty.value = false
}
const save = async () => {
  const backoff = (RETRY_PRESETS.find((p) => p.label === retryPreset.value) || RETRY_PRESETS[1]).sec
  const poll = (POLL_PRESETS.find((p) => p.label === pollPreset.value) || POLL_PRESETS[1])
  const trans = (TRANSFER_PRESETS.find((p) => p.label === transferPreset.value) || TRANSFER_PRESETS[1])
  try {
    await api.put('/api/settings', {
      ...f.value,
      retry_backoff: backoff,
      poll_interval_min: poll.min, poll_interval_max: poll.max,
      transfer_interval_min: trans.min, transfer_interval_max: trans.max,
    })
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
    saveOk('备份已导出', '包含设置/频道/规则/黑名单/订阅/转存记录')
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
const testNotify = async (token, chatId) => {
  testing.value = true
  try {
    await api.post('/api/settings/notify/test', {
      token: token ?? f.value.notify_token,
      chat_id: chatId ?? f.value.notify_chat_id,
    })
    saveOk('测试消息已发送', '请到 Telegram 查看')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    testing.value = false
  }
}
// 扩展渠道测试：用已保存的配置向指定渠道发一条测试消息（需先保存设置）
const testChannel = async (ch) => {
  chTesting.value = ch
  try {
    const { data } = await api.post(`/api/settings/notify/test/${ch}`)
    saveOk(`${data.detail || '测试消息已发送'}`, '请打开对应 App 确认收到')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    chTesting.value = ''
  }
}
// 立即推送一次每日摘要（内容与定时推送一致），用于验证摘要功能是否正常
const digestTesting = ref(false)
const testDigest = async () => {
  digestTesting.value = true
  try {
    const { data } = await api.post('/api/settings/digest/test')
    if (data.ok) {
      saveOk('摘要已推送', '请到已配置的通知渠道查看')
    } else {
      const fail = Object.entries(data.detail || {}).filter(([, v]) => !v).map(([k]) => k).join('、')
      ElMessage.error(`推送失败：${fail} 未成功（未配置或发送失败，请检查通知配置）`)
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    digestTesting.value = false
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
/* 左右两栏瀑布流布局：各分区卡片按高度自动分配到两列，窄屏回落单列 */
.settings-flow { column-count: 2; column-gap: 16px; }
.settings-flow > .section-card { break-inside: avoid; }
@media (max-width: 1100px) { .settings-flow { column-count: 1; } }
@media (max-width: 768px) { .section-card { padding: 12px 14px 2px; } }
.tip { color: #999; font-size: 12px; }
/* 警示性提示文字：加粗 + 琥珀色，一眼可见（全局样式 .warn-text 的本地兜底） */
.warn-text { font-weight: 600; color: #b45309; }
.switch-row { display: flex; align-items: center; gap: 10px; }
.ub-inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.switch-row .tip { flex: 1; min-width: 0; }
.range-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.range-row :deep(.el-input-number) { width: 130px; }
.sep { color: #999; }
</style>
