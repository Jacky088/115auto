import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../../api'
import { confirmAction } from '../../composables/confirmDialog'

export function useFileOperations({
  cid,
  currentName,
  isSearching,
  selected,
  load,
  clearLocalDirStats,
}) {
  const busy = ref(false)
  const clipboard = ref(null)
  const movePickerVisible = ref(false)
  const moveTargets = ref([])

  const copyRows = (rows) => {
    if (!rows.length) return
    clipboard.value = {
      mode: 'copy',
      items: rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir })),
    }
    ElMessage.success(`已复制 ${rows.length} 项，请选择目标目录后粘贴`)
  }

  const cutRows = (rows) => {
    if (!rows.length) return
    clipboard.value = {
      mode: 'move',
      items: rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir })),
    }
    ElMessage.success(`已剪切 ${rows.length} 项，请选择目标目录后粘贴`)
  }

  const copySelected = () => copyRows(selected.value)
  const cutSelected = () => cutRows(selected.value)

  const openMovePicker = (rows) => {
    if (!rows || !rows.length) return
    moveTargets.value = rows.map((r) => ({ id: String(r.id), name: r.name, is_dir: r.is_dir }))
    movePickerVisible.value = true
  }

  const onMoveTargetPicked = async (node) => {
    if (!node || !moveTargets.value.length) return
    const targetCid = String(node.cid ?? '0')
    const ids = moveTargets.value.map((i) => i.id).filter(Boolean)
    if (!ids.length) return
    if (moveTargets.value.some((i) => i.is_dir && String(i.id) === targetCid)) {
      ElMessage.warning('不能将目录移动到其自身内部')
      return
    }
    if (!isSearching.value && targetCid === String(cid.value)) {
      ElMessage.info('目标目录即当前目录，无需移动')
      return
    }
    busy.value = true
    try {
      await api.post('/api/files/move', { ids, target_cid: targetCid })
      saveOk('移动完成', `${ids.length} 项已移动到「${node.path || node.name}」`)
      selected.value = []
      moveTargets.value = []
      clearLocalDirStats()
      load()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      busy.value = false
    }
  }

  const paste = async () => {
    if (!clipboard.value || isSearching.value) return
    const ids = clipboard.value.items.map((i) => i.id).filter(Boolean)
    if (!ids.length) return
    if (clipboard.value.items.some((i) => i.is_dir && String(i.id) === String(cid.value))) {
      ElMessage.warning('不能将目录粘贴到其自身内部')
      return
    }
    busy.value = true
    const moving = clipboard.value.mode === 'move'
    try {
      await api.post(moving ? '/api/files/move' : '/api/files/copy', {
        ids,
        target_cid: cid.value,
      })
      saveOk(
        moving ? '移动完成' : '复制完成',
        `${ids.length} 项已${moving ? '移动' : '复制'}到「${currentName.value}」`,
      )
      if (moving) clipboard.value = null
      selected.value = []
      clearLocalDirStats()
      load()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      busy.value = false
    }
  }

  const deleteRows = async (rows) => {
    if (!rows.length) return
    const ok = await confirmAction({
      title: `删除选中的 ${rows.length} 项内容？`,
      message: '删除后可在 115 回收站中找回。',
      confirmText: '删除',
      type: 'danger',
    })
    if (!ok) return

    const ids = rows.map((r) => String(r.id))
    busy.value = true
    try {
      const { data } = await api.post('/api/files/delete', { ids })
      saveOk('已移入回收站', `${data.count || rows.length} 项已删除`)
      if (clipboard.value && clipboard.value.items.some((i) => ids.includes(String(i.id)))) {
        clipboard.value = null
      }
      selected.value = []
      clearLocalDirStats()
      load()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      busy.value = false
    }
  }

  const deleteSelected = () => deleteRows(selected.value)

  return {
    busy,
    clipboard,
    movePickerVisible,
    moveTargets,
    copyRows,
    cutRows,
    copySelected,
    cutSelected,
    openMovePicker,
    onMoveTargetPicked,
    paste,
    deleteRows,
    deleteSelected,
  }
}
