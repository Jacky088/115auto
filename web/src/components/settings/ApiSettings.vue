<template>
  <div class="api-settings-group">
    <div class="section-card">
      <div class="section-title">对外 API</div>
      <el-form-item label="API Token">
        <div class="range-row input-btn-row" style="width: 100%">
          <el-input
            v-model="f.api_token"
            type="password"
            show-password
            :placeholder="f.api_token === SECRET_MASK ? '已配置（保持不变即继续生效；清空并保存则关闭对外 API）' : '未设置时对外 API 关闭'"
            clearable
          />
          <el-button @click="genToken">生成随机 Token</el-button>
        </div>
        <div class="tip" style="width: 100%">
          留空关闭。开启后调用方携带请求头 <code>X-API-Token</code> 访问 <code>/api/ext/v1/*</code> 接口
        </div>
      </el-form-item>
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus'
import { SECRET_MASK } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'

const props = defineProps({
  f: {
    type: Object,
    required: true,
  },
})

const genToken = async () => {
  if (props.f.api_token && !await confirmAction({
    title: '确认重新生成 API Token？',
    message: '将覆盖当前已配置的 Token；保存设置后旧 Token 立即失效，外部调用方需同步更新。',
    confirmText: '重新生成',
    danger: true,
  })) return
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  props.f.api_token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  ElMessage.success('已生成随机 Token，点击「保存设置」后生效')
}
</script>
