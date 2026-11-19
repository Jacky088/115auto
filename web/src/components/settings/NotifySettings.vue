<template>
  <div class="notify-settings-group">
    <!-- TG 通知 -->
    <div class="section-card">
      <div class="section-title">TG 通知</div>
      <el-form-item label="转存结果通知">
        <div class="switch-row">
          <el-switch v-model="f.notify_transfer_success" />
          <span class="tip">转存成功/失败、云下载提交/完成/失败都会推送通知（TG / Bark / PushPlus / ntfy，含具体原因）</span>
        </div>
      </el-form-item>
      <el-form-item label="TG 推送">
        <SecretConfig
          :configured="!!(f.notify_token && f.notify_chat_id)"
          :save="saveNotify"
          @open="openNotify"
          @cancel="resetNotify"
        >
          <div class="range-row" style="width: 100%">
            <el-input
              v-model="nf.token"
              type="password"
              show-password
              placeholder="TG Bot Token（@BotFather 获取）"
            />
          </div>
          <div class="range-row" style="width: 100%">
            <el-input v-model="nf.chat_id" placeholder="Chat ID（你的用户 ID）" style="width: 260px" />
          </div>
          <div class="range-row" style="width: 100%">
            <el-button
              type="primary"
              plain
              size="small"
              :loading="testing"
              @click="testNotify(nf.token, nf.chat_id)"
            >
              发送测试消息
            </el-button>
          </div>
          <div class="tip" style="width: 100%">
            填写后无需保存即可直接测试；测试失败会提示具体原因。收不到时检查：① Token 是否完整复制
            ② 是否已给 Bot 发送 /start ③ 国内服务器需在上方「TG 代理」处填写代理地址
          </div>
        </SecretConfig>
      </el-form-item>
      <el-form-item label="直发链接格式">
        <div class="tip" style="width: 100%">
          直接发给 Bot 的链接需以指令头开头，程序据此判断进哪个队列（不带会回复格式说明）：<br />
          <code>转存 [分类] &lt;115 分享链接&gt;</code> → 进入分享转存队列<br />
          <code>云下载 [分类] &lt;磁力/电驴链接&gt;</code> → 进入云下载队列<br />
          空格不影响识别，链接可紧贴指令头；分类可省略（如 <code>云下载 动漫 magnet:?xt=…</code>），
          写了分类就用该分类目录，省略时按标题规则识别、识别不出用「未分类动作」；<br />
          分类名须与「转存目录」页的「分类 → 目录映射」完全一致，写错会拒绝入队并回复全部可选分类
        </div>
      </el-form-item>
    </div>

    <!-- 通知渠道扩展 -->
    <div class="section-card">
      <div class="section-title">通知渠道扩展</div>
      <el-form-item label="Bark（iOS）">
        <SecretConfig
          :configured="!!f.bark_url"
          :save="saveBark"
          @open="openBark"
          @cancel="resetBark"
        >
          <div class="range-row" style="width: 100%">
            <el-input
              v-model="bf.url"
              type="password"
              show-password
              placeholder="https://api.day.app/你的key"
            />
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
        <SecretConfig
          :configured="!!f.pushplus_token"
          :save="savePushPlus"
          @open="openPushPlus"
          @cancel="resetPushPlus"
        >
          <div class="range-row" style="width: 100%">
            <el-input
              v-model="pf.token"
              type="password"
              show-password
              placeholder="pushplus.plus 的 Token"
            />
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
        <SecretConfig
          :configured="!!f.ntfy_url"
          :save="saveNtfy"
          @open="openNtfy"
          @cancel="resetNtfy"
        >
          <div class="range-row" style="width: 100%">
            <el-input v-model="tf.url" placeholder="https://ntfy.sh/你的主题" />
          </div>
          <div class="range-row" style="width: 100%">
            <el-input
              v-model="tf.token"
              type="password"
              show-password
              placeholder="访问令牌（可选，自建 ntfy 开启鉴权时填）"
            />
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

    <!-- 每日摘要 -->
    <div class="section-card">
      <div class="section-title">每日摘要</div>
      <el-form-item label="每日摘要">
        <div class="switch-row">
          <el-switch v-model="f.digest_enabled" />
          <el-tooltip :disabled="!!f.digest_enabled" content="请先开启「每日摘要」总开关" placement="top">
            <span>
              <el-button :loading="digestTesting" :disabled="!f.digest_enabled" @click="testDigest">立即推送一次</el-button>
            </span>
          </el-tooltip>
        </div>
        <div class="tip" style="width: 100%">
          开启后每天定时把当日转存汇总推送到已配置的通知渠道（TG / Bark / PushPlus / ntfy）；
          「立即推送一次」用与定时推送完全相同的内容验证渠道是否可达
        </div>
      </el-form-item>
      <el-form-item v-if="f.digest_enabled" label="推送时间">
        <el-time-select
          v-model="f.digest_time"
          start="00:00"
          step="00:30"
          end="23:30"
          placeholder="选择时间"
          style="width: 130px"
        />
      </el-form-item>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk, SECRET_MASK } from '../../api'
import SecretConfig from '../SecretConfig.vue'

const props = defineProps({
  f: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['sync-f'])

const nf = reactive({ token: '', chat_id: '' })   // TG 通知
const bf = reactive({ url: '' })                  // Bark
const pf = reactive({ token: '', topic: '' })     // PushPlus
const tf = reactive({ url: '', token: '' })       // ntfy

const testing = ref(false)
const chTesting = ref('')
const digestTesting = ref(false)

const openNotify = () => { nf.token = ''; nf.chat_id = props.f.notify_chat_id || '' }
const resetNotify = () => { nf.token = ''; nf.chat_id = props.f.notify_chat_id || '' }
const saveNotify = async () => {
  if (!nf.token.trim() || !nf.chat_id.trim()) {
    ElMessage.error('请完整填写 Bot Token 和 Chat ID')
    throw new Error('invalid')
  }
  try {
    await api.put('/api/settings', { notify_token: nf.token.trim(), notify_chat_id: nf.chat_id.trim() })
    emit('sync-f', { notify_token: SECRET_MASK, notify_chat_id: nf.chat_id.trim() })
    saveOk('TG 通知配置已保存', '已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
    throw e
  }
}

const openBark = () => { bf.url = '' }
const resetBark = () => { bf.url = '' }
const saveBark = async () => {
  if (!bf.url.trim()) {
    ElMessage.error('请填写 Bark 推送 URL')
    throw new Error('invalid')
  }
  try {
    await api.put('/api/settings', { bark_url: bf.url.trim() })
    emit('sync-f', { bark_url: SECRET_MASK })
    saveOk('Bark 配置已保存', '已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
    throw e
  }
}

const openPushPlus = () => { pf.token = ''; pf.topic = props.f.pushplus_topic || '' }
const resetPushPlus = () => { pf.token = ''; pf.topic = props.f.pushplus_topic || '' }
const savePushPlus = async () => {
  if (!pf.token.trim()) {
    ElMessage.error('请填写 PushPlus Token')
    throw new Error('invalid')
  }
  try {
    await api.put('/api/settings', { pushplus_token: pf.token.trim(), pushplus_topic: pf.topic.trim() })
    emit('sync-f', { pushplus_token: SECRET_MASK, pushplus_topic: pf.topic.trim() })
    saveOk('PushPlus 配置已保存', '已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
    throw e
  }
}

const openNtfy = () => { tf.url = ''; tf.token = '' }
const resetNtfy = () => { tf.url = ''; tf.token = '' }
const saveNtfy = async () => {
  if (!tf.url.trim()) {
    ElMessage.error('请填写 ntfy 主题 URL')
    throw new Error('invalid')
  }
  try {
    await api.put('/api/settings', { ntfy_url: tf.url.trim(), ntfy_token: tf.token.trim() })
    emit('sync-f', { ntfy_url: tf.url.trim(), ntfy_token: SECRET_MASK })
    saveOk('ntfy 配置已保存', '已即时生效')
  } catch (e) {
    ElMessage.error(errMsg(e))
    throw e
  }
}

const testNotify = async (token, chatId) => {
  testing.value = true
  try {
    await api.post('/api/settings/notify/test', {
      token: (token && token !== SECRET_MASK) ? token : '',
      chat_id: chatId ?? props.f.notify_chat_id,
    })
    saveOk('测试消息已发送', '请到 Telegram 查看')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    testing.value = false
  }
}

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
</script>
