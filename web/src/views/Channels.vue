<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>监控频道</span>
        <el-button type="primary" @click="addVisible = true">添加频道</el-button>
      </div>
    </template>
    <el-table :data="channels" empty-text="还没有频道，点击右上角「添加频道」">
      <el-table-column prop="name" label="频道" min-width="160">
        <template #default="{ row }"><span class="mono">@{{ row.name }}</span></template>
      </el-table-column>
      <el-table-column prop="type" label="接入方式" width="160">
        <template #default="{ row }">
          <el-select :model-value="row.type" style="width: 134px"
                     @change="(v) => changeType(row, v)">
            <el-option label="网页轮询" value="polling" />
            <el-option :label="ubConnected ? 'Userbot' : 'Userbot（未登录）'" value="userbot"
                       :disabled="!ubConnected" />
          </el-select>
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
          <el-switch :model-value="!!row.sub_only" @change="(v) => toggleSubOnly(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250">
        <template #default="{ row }">
          <div class="ch-actions">
            <el-button @click="preview(row)">消息预览</el-button>
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

  <el-dialog v-model="addVisible" title="添加频道" width="440px">
    <el-form label-width="90px">
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
          <div v-if="addForm.subOnly === null" class="warn-text" style="font-size: 12px; margin-top: 4px">
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
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtTime, highlightLinks, statusLabel, statusTagType } from '../api'

const channels = ref([])
const addVisible = ref(false)
const addForm = reactive({ name: '', type: 'polling', subOnly: null })  // subOnly 必选（与 Bot /add 的 on|off 一致）
const previewVisible = ref(false)
const previewCh = ref('')
const previewChId = ref('')
const previewMsgs = ref([])
const transferingId = ref('')
const drawerSize = ref(window.innerWidth <= 768 ? '94%' : '600px')
const onRz = () => { drawerSize.value = window.innerWidth <= 768 ? '94%' : '600px' }
onMounted(() => window.addEventListener('resize', onRz))
onUnmounted(() => window.removeEventListener('resize', onRz))

const load = async () => {
  channels.value = (await api.get('/api/channels')).data
  loadUb()
}
// Userbot 登录状态：决定「接入方式」下拉里能否选 Userbot
const ubConnected = ref(false)
const loadUb = async () => {
  try { ubConnected.value = !!(await api.get('/api/tg/userbot/status')).data.connected } catch { /* ignore */ }
}
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
    addForm.subOnly = null  // 下一次添加必须重新选择（与 Bot /add 每次都要带 on|off 一致）
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
  finally { adding.value = false }
}
const toggle = async (row, v) => {
  try {
    await api.put(`/api/channels/${row.id}`, { enabled: v })
    row.enabled = v ? 1 : 0
    ElMessage.success(v ? `已启用监控 @${row.name}` : `已停用监控 @${row.name}`)
  } catch (e) {
    row.enabled = v ? 0 : 1  // 保存失败时恢复开关显示
    ElMessage.error(errMsg(e))
  }
}
// 频道级订阅模式：开启后该频道仅转存命中订阅关键词的内容
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
// 切换频道接入方式：Userbot 未登录时禁止并回退+提醒；运行中修改会提示重启生效
const changeType = async (row, v) => {
  const old = row.type
  if (v === 'userbot') {
    await loadUb()  // 取最新登录状态再判定
    if (!ubConnected.value) {
      ElMessageBox.alert(
        '尚未登录 Telegram Userbot，无法使用 Userbot 接入方式，已回退到「网页轮询」。<br/>' +
        '如需实时监听私密频道，请先到「账号管理」完成 Userbot 登录，再回来修改接入方式。',
        'Userbot 未登录',
        { dangerouslyUseHTMLString: true, confirmButtonText: '知道了', type: 'warning' },
      ).catch(() => {})
      row.type = ''
      nextTick(() => { row.type = 'polling' })  // 强制回退下拉显示
      return
    }
  }
  const doSave = async () => {
    try {
      await api.put(`/api/channels/${row.id}`, { type: v })
      row.type = v
      ElMessage.success(`@${row.name} 接入方式已改为 ${v === 'polling' ? '网页轮询' : 'Userbot'}`)
    } catch (e) {
      ElMessage.error(errMsg(e))
    }
  }
  try {
    const st = (await api.get('/api/system/state')).data
    if (!st.running) return doSave()
    ElMessageBox.confirm(
      `系统正在运行中，修改「@${row.name}」的接入方式需要停止后再启动才能完全生效。` +
      `是否保存修改？（保存后请点击顶栏「停止」，再点击「开始运行」）`,
      '修改接入方式',
      { confirmButtonText: '保存并稍后重启', cancelButtonText: '取消', type: 'warning' }
    ).then(doSave).catch(() => {})
  } catch { doSave() }  // 状态查询失败时直接保存（服务端照常校验）
}
const reset = async (row) => {
  try {
    const { data } = await api.post(`/api/channels/${row.id}/reset`)
    ElMessage.success(`@${row.name} 已重置：清除 ${data.cleared} 条消息 ID 记录，将从最新消息重新获取`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const del = async (row) => {
  try {
    const { data } = await api.delete(`/api/channels/${row.id}`)
    ElMessage.success(`已删除频道 @${row.name}，并清除 ${data.cleared ?? 0} 条消息去重记录`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const preview = async (row) => {
  try {
    previewCh.value = row.name
    previewChId.value = row.id
    previewMsgs.value = (await api.get(`/api/channels/${row.id}/messages`)).data
    previewVisible.value = true
  } catch (e) { ElMessage.error(errMsg(e)) }
}
// 手动转存单条消息：清除去重记忆后重放管道，成功后提示入队数并刷新预览
const transferMsg = async (m) => {
  if (transferingId.value) return
  transferingId.value = m.message_id
  try {
    const { data } = await api.post(`/api/channels/${previewChId.value}/messages/${m.message_id}/transfer`)
    const r = data.result || {}
    const dup = r.duplicates ? `，重复跳过 ${r.duplicates} 条` : ''
    ElMessage.success(`已提交转存：入队 ${r.queued ?? 0} 条${dup}`)
    previewMsgs.value = (await api.get(`/api/channels/${previewChId.value}/messages`)).data
  } catch (e) { ElMessage.error(errMsg(e)) } finally { transferingId.value = '' }
}
onMounted(load)
</script>

<style scoped>
.ch-actions { display: flex; align-items: center; flex-wrap: nowrap; gap: 6px; white-space: nowrap; }
.ch-actions :deep(.el-button) { margin-left: 0 !important; }
.ch-actions :deep(.el-tooltip__trigger) { display: inline-flex; }
.msg-item { border: 1px solid #e9eefb; border-radius: 10px; padding: 12px 14px; margin-bottom: 12px;
            background: #fff; }
.msg-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.msg-head-right { display: flex; align-items: center; gap: 8px; }
.health-ok { color: #16a34a; font-size: 12px; font-weight: 600; }
.health-bad { color: #dc2626; font-size: 12px; font-weight: 600; }
.msg-body { font-size: 13px; line-height: 1.8; color: #3a4661; white-space: pre-wrap;
            word-break: break-word; }
</style>
