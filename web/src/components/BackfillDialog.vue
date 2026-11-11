<template>
  <el-dialog
    v-model="visible"
    title="向前补全早期分集"
    :width="'min(480px, 94vw)'"
    destroy-on-close
  >
    <el-form label-width="96px">
      <el-form-item label="剧名关键词">
        <el-input v-model="form.keyword" placeholder="用于匹配频道历史帖子的剧名" />
      </el-form-item>

      <!-- 转存记录模式：支持手动校准当前集数与频道 -->
      <template v-if="form.mode === 'transfer'">
        <el-form-item label="当前集数">
          <el-input-number v-model="form.ep" :min="1" style="width: 130px" />
          <span class="bf-line" style="margin-left: 8px">{{ epRange }}</span>
        </el-form-item>
        <el-form-item label="频道">
          <span class="bf-line">@{{ form.channel }}</span>
        </el-form-item>
      </template>

      <!-- 订阅模式：从最近成功记录自动锚定 -->
      <template v-else>
        <el-form-item label="锚点记录">
          <span class="bf-line">将自动查找该剧最近一次成功转存，以其来源频道与消息为向前翻页起点</span>
        </el-form-item>
      </template>
    </el-form>

    <div class="bf-tip">
      将向前翻找频道历史里同一剧集的早期分集（最多 50 页）并加入转存队列；
      补全任务优先消耗每日额度，已存过的集数不会重复。
      早期链接已失效的集数会失败并注明原因，之后补发的新链接会照常转存。
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">开始补全</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'

const visible = ref(false)
const loading = ref(false)
const form = ref({
  mode: 'subscription', // 'subscription' | 'transfer'
  keyword: '',
  transferId: null,
  channel: '',
  ep: 1
})

const epRange = computed(() => {
  const n = parseInt(form.value.ep, 10)
  if (!n || n <= 1) return '（将补第 1 集）'
  return `（将向前翻找第 1~${n - 1} 集）`
})

const stripEpisode = (t) => t
  .replace(/【.*?】|\[.*?\]/g, ' ')
  .replace(/[Ss]\d{1,2}\s?[Ee]\d{1,3}/g, '')
  .replace(/[Ee][Pp]\s?\d{1,3}/g, '')
  .replace(/第\s?\d{1,3}\s?[集话話期]/g, '')
  .replace(/[（(]\d{4}[）)]?/g, '')
  .trim()

const openForTransfer = (row) => {
  const t = row.title || ''
  const m = t.match(/[Ss]\d{1,2}\s?[Ee](\d{1,3})/) || t.match(/第\s?(\d{1,3})\s?[集话話期]/)
            || t.match(/[Ee][Pp]\s?(\d{1,3})/)
  form.value = {
    mode: 'transfer',
    transferId: row.id,
    keyword: stripEpisode(t) || t,
    channel: row.channel || '',
    ep: m ? parseInt(m[1], 10) : 1
  }
  visible.value = true
}

const openForSubscription = (row) => {
  form.value = {
    mode: 'subscription',
    transferId: null,
    keyword: row.keyword || '',
    channel: '',
    ep: 1
  }
  visible.value = true
}

const submit = async () => {
  if (!form.value.keyword.trim()) {
    ElMessage.warning('请填写剧名关键词')
    return
  }
  if (form.value.mode === 'transfer' && !form.value.ep) {
    ElMessage.warning('请填写当前集数（标题未识别出集数时需手动填写）')
    return
  }
  loading.value = true
  try {
    const payload = form.value.mode === 'transfer'
      ? {
          transfer_id: form.value.transferId,
          keyword: form.value.keyword.trim(),
          current_ep: form.value.ep
        }
      : { keyword: form.value.keyword.trim() }

    const { data } = await api.post('/api/backfill/episodes', payload)
    saveOk('已开始向前补全', `《${data.base}》${data.episodes}，任务将优先入队`)
    visible.value = false
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}

defineExpose({
  openForTransfer,
  openForSubscription
})
</script>

<style scoped>
.bf-tip {
  font-size: var(--fs-caption);
  line-height: 1.8;
  color: var(--saas-text-sub);
  background: rgba(79, 107, 255, .05);
  border: 1px solid var(--saas-line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-top: 10px;
}
.bf-line {
  color: var(--saas-text-main);
  font-size: var(--fs-body);
}
</style>
