<template>
  <div>
    <!-- 新建文件夹 -->
    <el-dialog v-model="mkdirVisible" title="新建文件夹" :width="'min(420px, 94vw)'" align-center>
      <el-form label-position="top" @submit.prevent="saveMkdir">
        <el-form-item label="文件夹名称">
          <el-input v-model="mkdirName" placeholder="输入文件夹名称" autofocus @keyup.enter="saveMkdir" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mkdirVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveMkdir">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重命名 -->
    <el-dialog v-model="renameVisible" title="重命名" :width="'min(420px, 94vw)'" align-center>
      <el-form label-position="top" @submit.prevent="saveRename">
        <el-form-item label="新名称">
          <el-input v-model="renameForm.name" placeholder="输入新名称" autofocus @keyup.enter="saveRename" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveRename">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'

const props = defineProps({
  cid: { type: String, default: '0' },
})

const emit = defineEmits(['saved'])

const busy = ref(false)
const mkdirVisible = ref(false)
const mkdirName = ref('')

const renameVisible = ref(false)
const renameForm = reactive({ id: '', name: '' })

const openMkdir = () => {
  mkdirName.value = ''
  mkdirVisible.value = true
}

const openRename = (row) => {
  if (!row) return
  renameForm.id = String(row.id)
  renameForm.name = row.name
  renameVisible.value = true
}

const saveMkdir = async () => {
  const name = mkdirName.value.trim()
  if (!name) {
    ElMessage.warning('请输入文件夹名称')
    return
  }
  busy.value = true
  try {
    await api.post('/api/files/mkdir', { pid: props.cid, name })
    saveOk('文件夹已创建', `「${name}」，位于当前目录`)
    mkdirVisible.value = false
    mkdirName.value = ''
    emit('saved')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    busy.value = false
  }
}

const saveRename = async () => {
  const name = renameForm.name.trim()
  if (!name) {
    ElMessage.warning('请输入新名称')
    return
  }
  busy.value = true
  try {
    await api.post('/api/files/rename', { id: renameForm.id, name })
    saveOk('重命名成功', `已重命名为「${name}」`)
    renameVisible.value = false
    emit('saved')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    busy.value = false
  }
}

defineExpose({
  openMkdir,
  openRename,
})
</script>
