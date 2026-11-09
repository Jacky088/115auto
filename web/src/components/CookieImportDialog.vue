<template>
  <el-dialog :model-value="modelValue" title="手动导入 115 Cookie" width="600px" top="6vh"
             :close-on-click-modal="false" @update:model-value="emitVisible" @open="cookie = ''">
    <el-alert type="info" :closable="false" show-icon class="ci-scene">
      <template #title>适用场景：服务器在海外/机房，扫码确认后被 115 风控拦截</template>
      <div class="ci-steps">
        <p>1. 在<b>本地电脑</b>的浏览器打开 <a href="https://115.com" target="_blank" rel="noopener">115.com</a>
          并登录（家庭宽带 IP 一般不会被风控）；</p>
        <p>2. 按 <b>F12</b> 打开开发者工具 → 网络（Network）→ 刷新页面 → 点击任意请求 →
          在请求头 <b>Cookie</b> 里复制 <code>UID=...</code>、<code>CID=...</code>、<code>SEID=...</code> 三段；</p>
        <p>3. 粘贴到下方（整段 Cookie 也可以），点击「导入并校验」。</p>
      </div>
    </el-alert>
    <el-input v-model="cookie" type="textarea" :rows="5"
              placeholder="UID=xxx;CID=xxx;SEID=xxx" class="ci-input" />
    <p class="ci-tip">⚠️ Cookie 等同于账号登录凭证，仅保存在你服务器的 data/ 目录，请勿泄露给他人。</p>
    <template #footer>
      <div class="ci-footer">
        <el-button @click="emitVisible(false)">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submit">导入并校验</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'success'])

const cookie = ref('')
const busy = ref(false)

const emitVisible = (v) => emit('update:modelValue', v)

const submit = async () => {
  if (!cookie.value.trim()) { ElMessage.warning('请先粘贴 Cookie'); return }
  busy.value = true
  try {
    const { data } = await api.post('/api/account/115/cookie', { cookie: cookie.value })
    ElMessage.success(`Cookie 已导入，账号「${data.name || '未知'}」校验通过`)
    emitVisible(false)
    emit('success')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally { busy.value = false }
}
</script>

<style scoped>
.ci-scene :deep(.el-alert__description) { margin: 0; }
.ci-steps { font-size: var(--fs-body); color: #606266; line-height: 1.8; }
.ci-steps p { margin: 0; }
.ci-steps code {
  background: #f4f4f5; padding: 0 4px; border-radius: 4px;
  font-family: Consolas, Monaco, monospace; font-size: var(--fs-caption);
}
.ci-input { margin-top: 4px; }
.ci-tip { font-size: var(--fs-caption); color: #b45309; font-weight: 600; margin: 8px 0 0; }
.ci-footer { display: flex; justify-content: flex-end; gap: 10px; }
</style>
