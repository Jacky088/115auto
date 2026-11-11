<template>
  <div class="account-page">
    <!-- 顶部账户卡片：115 网盘 与 Telegram Userbot -->
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

    <!-- Telegram 监控频道（原频道管理并入） -->
    <el-card shadow="never" class="page-card" style="margin-top: 16px">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <div style="display: flex; align-items: center; gap: 8px">
            <b>Telegram 监控频道</b>
            <el-tag size="small" class="count-tag">共 {{ channels.length }} 个频道</el-tag>
          </div>
          <el-button type="primary" @click="addVisible = true">
            <el-icon><Plus /></el-icon>
            <span>添加频道</span>
          </el-button>
        </div>
      </template>
      <el-table :data="channels">
        <template #empty>
          <el-empty :image-size="60" description="还没有监控频道，点击右上角「添加频道」开始添加" />
        </template>
        <el-table-column prop="name" label="频道" min-width="160">
          <template #default="{ row }"><span class="mono">@{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column prop="type" label="接入方式" width="160">
          <template #default="{ row }">
            <el-tooltip
              :disabled="!!row.enabled"
              content="频道已停用，请先开启「启用」开关"
              placement="top"
            >
              <span>
                <el-select :model-value="row.type" style="width: 134px"
                           :disabled="!row.enabled"
                           @change="(v) => changeType(row, v)">
                  <el-option label="网页轮询" value="polling" />
                  <el-option :label="ubConnected ? 'Userbot' : 'Userbot（未登录）'" value="userbot"
                             :disabled="!ubConnected" />
                </el-select>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="msg_count" label="消息数" width="90" />
        <el-table-column prop="last_fetch_at" label="最近抓取" width="160">
          <template #default="{ row }">{{ row.last_fetch_at ? fmtTime(row.last_fetch_at) : '-' }}</template>
        </el-table-column>
        <el-table-column label="健康" width="130">
          <template #default="{ row }">
            <span v-if="row.fail_count > 0" class="health-bad">连续失败 {{ row.fail_count }} 轮</span>
            <span v-else class="health-ok">正常</span>
          </template>
        </el-table-column>
        <el-table-column prop="enabled" label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" @change="(v) => toggle(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="仅订阅" width="90">
          <template #header>
            <el-tooltip content="开启后该频道只转存命中订阅关键词的内容；关闭则全盘转存。每个频道独立设置"
                        placement="top">
              <span>仅订阅 ⓘ</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-tooltip
              :disabled="!!row.enabled"
              content="频道已停用，请先开启「启用」开关"
              placement="top"
            >
              <span>
                <el-switch :model-value="!!row.sub_only" :disabled="!row.enabled" @change="(v) => toggleSubOnly(row, v)" />
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button type="primary" text @click="preview(row)">预览</el-button>
              <el-popconfirm
                title="重置后该频道将清除已处理的消息去重记录，从最新消息重新获取；已成功的分享不会重复转存。确认重置？"
                confirm-button-text="重置" cancel-button-text="取消" confirm-button-type="warning"
                width="300" @confirm="reset(row)">
                <template #reference><el-button type="warning" text>重置</el-button></template>
              </el-popconfirm>
              <el-popconfirm title="删除后将不再监控该频道，并清除其消息去重记录；重新添加会按新频道重新抓取。确认删除？"
                             confirm-button-text="删除" cancel-button-text="取消" confirm-button-type="danger"
                             width="300" @confirm="del(row)">
                <template #reference><el-button type="danger" text>删除</el-button></template>
              </el-popconfirm>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 115 扫码对话框 -->
    <el-dialog v-model="qrVisible" title="115 扫码登录" :width="'min(380px, 94vw)'" @close="stopQrPoll">
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
    <el-dialog v-model="tgVisible" title="Userbot 登录" :width="'min(480px, 94vw)'" @close="load">
      <UserbotLogin compact @update="load" />
    </el-dialog>

    <!-- 添加频道对话框 -->
    <el-dialog v-model="addVisible" title="添加监控频道" :width="'min(460px, 94vw)'">
      <el-form label-width="96px">
        <el-form-item label="频道">
          <el-input v-model="addForm.name" placeholder="@channel_name 或 https://t.me/s/xxx" />
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="addForm.type">
            <el-radio value="polling">网页轮询</el-radio>
            <el-radio value="userbot">Userbot</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="订阅模式">
          <div style="width: 100%">
            <el-radio-group v-model="addForm.subOnly">
              <el-radio :value="false">全盘转存</el-radio>
              <el-radio :value="true">仅订阅</el-radio>
            </el-radio-group>
            <div class="tip" style="margin-top: 4px">
              全盘转存 = 该频道的所有资源链接都转存；仅订阅 = 只转存命中订阅关键词的内容
              （与 Bot 指令 /add 的 on|off 一致）
            </div>
            <div v-if="addForm.subOnly === null" class="warn-text" style="font-size: var(--fs-caption); margin-top: 4px">
              ⚠️ 必须选择订阅模式（全盘转存 或 仅订阅），选择后才能确定
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="adding" :disabled="addForm.subOnly === null"
                   @click="add">确定</el-button>
      </template>
    </el-dialog>

    <!-- 消息预览抽屉 -->
    <el-drawer v-model="previewVisible" :title="`@${previewCh} 最近消息`" :size="drawerSize">
      <el-empty v-if="!previewMsgs.length" description="暂无抓取到的消息" />
      <div v-for="m in previewMsgs" :key="m.message_id" class="msg-item">
        <div class="msg-head">
          <span class="mono">#{{ m.message_id }}</span>
          <div class="msg-head-right">
            <el-tag size="small" :type="m.transfer_status ? statusTagType(m.transfer_status) : 'info'">
              {{ m.transfer_status ? statusLabel[m.transfer_status] : '非分享/待处理' }}
            </el-tag>
            <el-button size="small" type="primary" plain :loading="transferingId === m.message_id"
                       @click="transferMsg(m)">转存此消息</el-button>
          </div>
        </div>
        <div class="msg-body" v-html="highlightLinks(m.text)"></div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, CircleCheckFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import { api, errMsg, fmtTime, highlightLinks, statusLabel, statusTagType } from '../api'
import CookieImportDialog from '../components/CookieImportDialog.vue'
import UserbotLogin from '../components/UserbotLogin.vue'

// ---------- 115 与 Userbot 账号状态 ----------
const s115 = ref({})
const tg = ref({})
const qrVisible = ref(false)
const cookieVisible = ref(false)
const qr = reactive({ loading: false, svg: '', token: null, app: '', scanned: false, err: '' })
let pollSeq = 0   // 轮询会话号：关闭弹窗/重新生成二维码时 +1 使旧循环退出
let pollErrCount = 0

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const stopQrPoll = () => { pollSeq++ }

// ---------- 频道管理状态与逻辑 ----------
const channels = ref([])
const addVisible = ref(false)
const addForm = reactive({ name: '', type: 'polling', subOnly: null })
const previewVisible = ref(false)
const previewCh = ref('')
const previewChId = ref('')
const previewMsgs = ref([])
const transferingId = ref('')
const drawerSize = ref(window.innerWidth <= 768 ? '94%' : '600px')
const onRz = () => { drawerSize.value = window.innerWidth <= 768 ? '94%' : '600px' }

const ubConnected = computed(() => !!tg.value.connected)

const load = async () => {
  // 各面板独立结算并先到先渲染：防止 115 状态检测慢/失败时拖垮或隐藏 TG 频道列表
  await Promise.allSettled([
    api.get('/api/account/115/status').then((res) => { s115.value = res.data }).catch(() => {}),
    api.get('/api/tg/userbot/status').then((res) => { tg.value = res.data }).catch(() => {}),
    api.get('/api/channels').then((res) => { channels.value = res.data }).catch(() => {}),
  ])
}

const loadChannels = async () => {
  try {
    channels.value = (await api.get('/api/channels')).data
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
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
    await sleep(500)
  }
}

// ---- TG userbot ----
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

// ---- 频道增删改查操作 ----
const adding = ref(false)
const add = async () => {
  if (adding.value) return
  if (addForm.subOnly === null) { ElMessage.warning('请先选择订阅模式（全盘转存 / 仅订阅）'); return }
  adding.value = true
  try {
    const { data } = await api.post('/api/channels', {
      name: addForm.name,
      type: addForm.type,
      sub_only: addForm.subOnly,
    })
    ElMessage.success(data.title ? `已添加 @${data.name}（${data.title}）` : `已添加 @${data.name}`)
    addVisible.value = false
    addForm.name = ''
    addForm.subOnly = null
    loadChannels()
  } catch (e) { ElMessage.error(errMsg(e)) }
  finally { adding.value = false }
}

const toggle = async (row, v) => {
  const isEn = v ? 1 : 0
  const oldSubOnly = row.sub_only
  try {
    if (!isEn) {
      await api.put(`/api/channels/${row.id}`, { enabled: 0, sub_only: 0 })
      row.enabled = 0
      row.sub_only = 0
    } else {
      await api.put(`/api/channels/${row.id}`, { enabled: 1 })
      row.enabled = 1
    }
    ElMessage.success(isEn ? `已启用监控 @${row.name}` : `已停用监控 @${row.name}（订阅模式已自动联动关闭）`)
  } catch (e) {
    row.enabled = v ? 0 : 1
    if (!isEn) row.sub_only = oldSubOnly
    ElMessage.error(errMsg(e))
  }
}

const toggleSubOnly = async (row, v) => {
  try {
    await api.put(`/api/channels/${row.id}`, { sub_only: !!v })
    row.sub_only = v ? 1 : 0
    ElMessage.success(v
      ? `@${row.name} 已开启「仅订阅」：只转存命中订阅关键词的内容`
      : `@${row.name} 已关闭「仅订阅」：全盘转存`)
  } catch (e) {
    row.sub_only = v ? 0 : 1
    ElMessage.error(errMsg(e))
  }
}

const changeType = async (row, v) => {
  const old = row.type
  if (v === 'userbot') {
    if (!ubConnected.value) {
      ElMessageBox.alert(
        '尚未登录 Telegram Userbot，无法使用 Userbot 接入方式，已回退到「网页轮询」。<br/>' +
        '如需实时监听私密频道，请先在上方完成 Userbot 登录，再修改接入方式。',
        'Userbot 未登录',
        { dangerouslyUseHTMLString: true, confirmButtonText: '知道了', type: 'warning' },
      ).catch(() => {})
      row.type = old
      return
    }
  }
  try {
    await api.put(`/api/channels/${row.id}`, { type: v })
    row.type = v
    const label = { polling: '网页轮询', userbot: 'Userbot' }
    ElMessageBox.alert(
      `已将 @${row.name} 的接入方式改为「${label[v]}」。<br/><br/>` +
      `<b>⚠️ 注意：</b>如系统当前正在运行，需点击顶栏的「停止」再「运行」（或重启容器）以重启监听线程。`,
      '修改成功（需重启监听生效）',
      { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' },
    ).catch(() => {})
  } catch (e) {
    row.type = old
    ElMessage.error(errMsg(e))
  }
}

const reset = async (row) => {
  try {
    const { data } = await api.post(`/api/channels/${row.id}/reset`)
    ElMessage.success(`已重置 @${row.name}：清除已处理记录，从最新消息开始抓取`)
    row.msg_count = data.msg_count ?? 0
    row.fail_count = 0
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const del = async (row) => {
  try {
    await api.delete(`/api/channels/${row.id}`)
    ElMessage.success(`已删除频道 @${row.name}`)
    loadChannels()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const preview = async (row) => {
  previewCh.value = row.name
  previewChId.value = row.id
  previewMsgs.value = []
  previewVisible.value = true
  try {
    previewMsgs.value = (await api.get(`/api/channels/${row.id}/messages`)).data
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const transferMsg = async (m) => {
  transferingId.value = m.message_id
  try {
    const { data } = await api.post(`/api/channels/${previewChId.value}/messages/${m.message_id}/transfer`)
    ElMessage.success(data.queued ? `已将 #${m.message_id} 的链接加入转存队列` : `该消息已处理过或无有效链接`)
    previewMsgs.value = (await api.get(`/api/channels/${previewChId.value}/messages`)).data
  } catch (e) { ElMessage.error(errMsg(e)) } finally { transferingId.value = '' }
}

onMounted(() => {
  load()
  window.addEventListener('resize', onRz)
})

onUnmounted(() => {
  stopQrPoll()
  window.removeEventListener('resize', onRz)
})
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

/* 消息预览卡片 */
.msg-item {
  border: 1px solid var(--saas-line); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px;
  background: var(--glass-bg-strong); color: var(--saas-text-main);
}
.msg-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.msg-head-right { display: flex; align-items: center; gap: 10px; }
.health-ok { color: #16a34a; font-size: var(--fs-caption); font-weight: 600; }
.health-bad { color: #dc2626; font-size: var(--fs-caption); font-weight: 600; }
</style>
