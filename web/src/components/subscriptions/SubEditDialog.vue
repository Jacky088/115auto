<template>
  <el-dialog
    :model-value="modelValue"
    :title="form.id ? '编辑订阅' : '配置并添加订阅'"
    width="min(520px, calc(100vw - 32px))"
    align-center
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <el-form :model="form" label-width="96px" label-position="top">
      <el-form-item label="主关键词" required>
        <el-input v-model="form.keyword" maxlength="100" placeholder="例如：庆余年" />
      </el-form-item>
      <el-form-item label="别名（每行或逗号分隔）">
        <el-input
          v-model="form.aliases"
          type="textarea"
          :rows="2"
          placeholder="例如：Joy of Life, 慶餘年（任一命中即算命中）"
        />
      </el-form-item>
      <el-form-item label="排除词（每行或逗号分隔）">
        <el-input
          v-model="form.exclude_keywords"
          type="textarea"
          :rows="2"
          placeholder="例如：花絮, 预告, 解说, 抢先版（命中则跳过本订阅）"
        />
      </el-form-item>
      <el-form-item v-if="qualityPolicyOn" label="最低画质">
        <el-select v-model="form.min_quality" style="width: 180px">
          <el-option v-for="o in QUALITY_OPTS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.note" maxlength="200" placeholder="可选备注说明" />
      </el-form-item>
      <el-form-item v-if="form.kind === 'movie'" label="电影完结策略">
        <el-switch v-model="form.auto_disable_movie" />
        <span class="tip-inline" style="margin-left: 8px">
          命中并成功入队后自动停用该电影订阅（避免后续重复命中）
        </span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">
        {{ form.id ? '保存修改' : '确认订阅' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'

const QUALITY_OPTS = [
  { label: '跟随全局', value: '' },
  { label: '720p', value: '720p' },
  { label: '1080p', value: '1080p' },
  { label: '2160p', value: '2160p' },
]

const props = defineProps({
  modelValue: Boolean,
  row: { type: Object, default: null },
  qualityPolicyOn: Boolean,
})
const emit = defineEmits(['update:modelValue', 'saved'])

const saving = ref(false)
const form = reactive({
  id: 0,
  tmdb_id: null,
  keyword: '',
  aliases: '',
  exclude_keywords: '',
  min_quality: '',
  note: '',
  kind: '',
  auto_disable_movie: true,
})

watch(
  () => props.row,
  (r) => {
    if (!r) return
    form.id = r.id || 0
    form.tmdb_id = r.tmdb_id || null
    form.keyword = r.keyword || ''
    form.aliases = Array.isArray(r.aliases) ? r.aliases.join(', ') : (r.aliases || '')
    form.exclude_keywords = Array.isArray(r.exclude_keywords)
      ? r.exclude_keywords.join(', ')
      : (r.exclude_keywords || '')
    form.min_quality = r.min_quality || ''
    form.note = r.note || ''
    form.kind = r.kind || ''
    form.auto_disable_movie = r.auto_disable_movie !== false
  },
  { immediate: true },
)

const save = async () => {
  if (!form.keyword.trim()) {
    ElMessage.warning('主关键词不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      keyword: form.keyword.trim(),
      aliases: form.aliases,
      exclude_keywords: form.exclude_keywords,
      min_quality: form.min_quality,
      note: form.note.trim(),
      auto_disable_movie: form.auto_disable_movie,
    }
    if (form.id) {
      await api.put(`/api/subscriptions/${form.id}`, payload)
      saveOk('订阅已更新')
    } else {
      if (form.tmdb_id) payload.tmdb_id = form.tmdb_id
      if (form.kind) payload.kind = form.kind
      await api.post('/api/subscriptions', payload)
      saveOk(`已添加订阅「${payload.keyword}」`)
    }
    emit('update:modelValue', false)
    emit('saved')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    saving.value = false
  }
}
</script>
