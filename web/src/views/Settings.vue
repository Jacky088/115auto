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
            <div class="tip" style="width: 100%">修改接入方式后需重启容器生效</div>
          </el-form-item>
          <el-form-item label="TG 代理">
            <div class="range-row" style="width: 100%">
              <el-input v-model="f.proxy" placeholder="socks5://127.0.0.1:1080 或 http://127.0.0.1:7890（仅 TG 流量走代理，留空直连）" />
              <el-button :loading="proxyTesting" @click="testProxy">测试代理</el-button>
            </div>
            <div class="tip" style="width: 100%">
              国内部署必填：所有 TG 流量（频道抓取/补漏/按钮解析/通知/Bot 指令）都走此代理，
              115 直连不受影响。测试失败会提示具体原因。
            </div>
          </el-form-item>
          <el-form-item label="轮询间隔（秒）">
            <div class="range-row">
              <el-input-number v-model="f.poll_interval_min" :min="10" controls-position="right" />
              <span class="sep">~</span>
              <el-input-number v-model="f.poll_interval_max" :min="15" controls-position="right" />
              <span class="tip">区间内随机，降低被识别风险</span>
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
      </div>

      <div style="margin-top: 10px">
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
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'

const f = ref({})
const loaded = ref(false)
const retryText = ref('')
const testing = ref(false)
const dirty = ref(false)
const isMobile = ref(window.innerWidth <= 768)
window.addEventListener('resize', () => { isMobile.value = window.innerWidth <= 768 })
let suppressWatch = false

watch([f, retryText], () => {
  if (!suppressWatch) dirty.value = true
}, { deep: true })

const load = async () => {
  suppressWatch = true
  f.value = (await api.get('/api/settings')).data
  retryText.value = (f.value.retry_backoff || []).join(', ')
  loaded.value = true
  await nextTick()
  suppressWatch = false
  dirty.value = false
}
const save = async () => {
  const backoff = retryText.value.split(',').map((s) => parseInt(s.trim())).filter((n) => n > 0)
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
.switch-row .tip { flex: 1; min-width: 0; }
.range-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.range-row :deep(.el-input-number) { width: 130px; }
.sep { color: #999; }
</style>
