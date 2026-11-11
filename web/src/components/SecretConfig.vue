<template>
  <div class="secret-cfg">
    <!-- 已配置：隐藏输入框，只显示状态 + 更换按钮（紧凑单行） -->
    <template v-if="configured && !editing">
      <div class="sc-ok">
        <el-tag type="success" effect="plain" size="small">✓ 已配置</el-tag>
        <el-button text type="primary" size="small" @click="$emit('open'); editing = true">更换</el-button>
      </div>
    </template>
    <!-- 未配置 / 更换中：显示输入框 + 独立保存按钮（不联动页面底部保存） -->
    <template v-else>
      <div class="sc-fields"><slot /></div>
      <div class="sc-btns">
        <el-button type="primary" size="small" :loading="saving" @click="onSave">保存</el-button>
        <el-button v-if="configured" size="small" @click="onCancel">取消</el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  configured: Boolean,
  // 父组件提供的异步保存函数；抛错时保持编辑态
  save: { type: Function, required: true },
})
const emit = defineEmits(['open', 'cancel'])

const editing = ref(false)
const saving = ref(false)

const onSave = async () => {
  saving.value = true
  try {
    await props.save()
    editing.value = false
  } catch { /* 错误提示由父组件负责 */ } finally {
    saving.value = false
  }
}
const onCancel = () => {
  editing.value = false
  emit('cancel')
}
</script>

<style scoped>
.secret-cfg { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.sc-ok { display: flex; align-items: center; gap: 8px; }
.sc-ok .el-button { padding: 4px 8px; }
.sc-fields { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.sc-btns { display: flex; align-items: center; gap: 10px; }
.sc-btns .el-button + .el-button { margin-left: 0; }
</style>
