<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <span>监控频道</span>
        <el-button type="primary" @click="addVisible = true">添加频道</el-button>
      </div>
    </template>
    <el-table :data="channels" empty-text="还没有频道，点右上角添加">
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
      <el-table-column prop="enabled" label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="!!row.enabled" @change="(v) => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button @click="preview(row)">消息预览</el-button>
          <el-popconfirm title="确认删除该频道？删除后不再监控此频道。" confirm-button-text="删除"
                         cancel-button-text="取消" confirm-button-type="danger" width="240"
                         @confirm="del(row)">
            <template #reference><el-button type="danger" text>删除</el-button></template>
          </el-popconfirm>
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
    </el-form>
    <template #footer>
      <el-button @click="addVisible = false">取消</el-button>
      <el-button type="primary" @click="add">确定</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="previewVisible" :title="`@${previewCh} 最近消息`" :size="drawerSize">
    <el-empty v-if="!previewMsgs.length" description="暂无抓取到的消息" />
    <div v-for="m in previewMsgs" :key="m.message_id" class="msg-item">
      <div class="msg-head">
        <span class="mono">#{{ m.message_id }}</span>
        <el-tag size="small" :type="m.transfer_status ? statusTagType(m.transfer_status) : 'info'">
          {{ m.transfer_status ? statusLabel[m.transfer_status] : '非分享/待处理' }}
        </el-tag>
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
const addForm = reactive({ name: '', type: 'polling' })
const previewVisible = ref(false)
const previewCh = ref('')
const previewMsgs = ref([])
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
const add = async () => {
  try {
    await api.post('/api/channels', addForm)
    ElMessage.success('已添加')
    addVisible.value = false
    addForm.name = ''
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
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
// 切换频道接入方式：Userbot 未登录时禁止并回退+提醒；运行中修改会提示重启生效
const changeType = async (row, v) => {
  const old = row.type
  if (v === 'userbot') {
    await loadUb()  // 取最新登录状态再判定
    if (!ubConnected.value) {
      ElMessageBox.alert(
        '尚未登录 Telegram Userbot，无法使用 Userbot 接入方式，已为你回退到「网页轮询」。<br/>' +
        '如需实时监听私有频道，请先到「账号管理」完成 Userbot 登录，再回来修改接入方式。',
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
      `是否保存修改？（保存后请到顶栏「停止」再「开始运行」）`,
      '修改接入方式',
      { confirmButtonText: '保存并稍后重启', cancelButtonText: '取消', type: 'warning' }
    ).then(doSave).catch(() => {})
  } catch { doSave() }  // 状态查询失败时直接保存（服务端照常校验）
}
const del = async (row) => {
  try {
    await api.delete(`/api/channels/${row.id}`)
    ElMessage.success(`已删除频道 @${row.name}`)
    load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const preview = async (row) => {
  try {
    previewCh.value = row.name
    previewMsgs.value = (await api.get(`/api/channels/${row.id}/messages`)).data
    previewVisible.value = true
  } catch (e) { ElMessage.error(errMsg(e)) }
}
onMounted(load)
</script>

<style scoped>
.msg-item { border: 1px solid #e9eefb; border-radius: 10px; padding: 12px 14px; margin-bottom: 12px;
            background: #fff; }
.msg-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.msg-body { font-size: 13px; line-height: 1.8; color: #3a4661; white-space: pre-wrap;
            word-break: break-word; }
</style>
