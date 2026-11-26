<template>
  <el-tooltip
    v-model:visible="visible"
    :virtual-ref="triggerEl"
    virtual-triggering
    :content="content"
    placement="top"
    effect="dark"
    popper-class="global-truncate-popper"
  />
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const visible = ref(false)
const triggerEl = ref(null)
const content = ref('')

const INTERACTIVE_SEL = [
  'button',
  'a',
  'input',
  'textarea',
  'select',
  '.el-button',
  '.el-switch',
  '.el-checkbox',
  '.el-radio',
  '.el-select',
  '.el-input',
  '.el-input-number',
  '.el-pagination',
  '.el-dropdown',
  '[role="button"]',
].join(',')

const TRUNCATE_SEL = [
  '.el-table__cell .cell',
  '.row-title',
  '.row-error',
  '.sub-alias',
  '.tmdb-name',
  '.tmdb-orig',
  '.tmdb-overview',
  '.manual-dir-text',
  '.route-opt-dir',
  '[data-truncate-tip]',
].join(',')

function isTruncated(el) {
  if (!el || el.nodeType !== 1) return false
  return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1
}

function getFullText(el) {
  return String(
    el.getAttribute('data-full-tip')
    || el.getAttribute('data-stashed-title')
    || el.getAttribute('title')
    || el.textContent
    || '',
  ).trim()
}

function findTruncatedTarget(startEl) {
  if (!startEl || startEl.nodeType !== 1) return null
  if (startEl.closest('.el-popper')) return null

  const interactive = startEl.closest(INTERACTIVE_SEL)
  const candidate = startEl.closest(TRUNCATE_SEL)
  if (!candidate) return null
  // 若交互控件位于候选文本节点内部，说明点的是按钮而非文本
  if (interactive && candidate.contains(interactive) && interactive !== candidate) return null

  // 优先检查更内层的溢出元素（例如表格单元格里的 .row-title / .row-error）
  if (candidate.classList.contains('cell')) {
    const inner = startEl.closest('.row-title, .row-error, .sub-alias, .hit-title')
    if (inner && candidate.contains(inner) && isTruncated(inner)) return inner
  }
  return isTruncated(candidate) ? candidate : null
}

function showFor(el) {
  const txt = getFullText(el)
  if (!txt) return
  if (el.hasAttribute('title')) {
    el.setAttribute('data-stashed-title', el.getAttribute('title') || '')
    el.removeAttribute('title')
  }
  triggerEl.value = el
  content.value = txt
  visible.value = true
}

function hide() {
  visible.value = false
}

function onClick(e) {
  const target = findTruncatedTarget(e.target)
  if (!target) {
    if (visible.value && !e.target?.closest?.('.el-popper')) hide()
    return
  }
  if (visible.value && triggerEl.value === target) {
    hide()
    return
  }
  showFor(target)
}

function onMouseOver(e) {
  if (window.matchMedia?.('(hover: none)').matches) return
  const target = findTruncatedTarget(e.target)
  if (!target) return
  // el-table 自带 show-overflow-tooltip 的普通单元格交给 Element Plus 原生 hover 处理，避免双气泡
  if (target.classList.contains('cell') || target.classList.contains('el-tooltip__trigger')) return
  showFor(target)
}

function onMouseOut(e) {
  if (!triggerEl.value) return
  if (e.target === triggerEl.value || triggerEl.value.contains(e.target)) {
    const rel = e.relatedTarget
    if (!rel || (!triggerEl.value.contains(rel) && !rel.closest?.('.global-truncate-popper'))) {
      hide()
    }
  }
}

onMounted(() => {
  document.addEventListener('click', onClick, true)
  document.addEventListener('mouseover', onMouseOver, true)
  document.addEventListener('mouseout', onMouseOut, true)
  window.addEventListener('scroll', hide, true)
})

onUnmounted(() => {
  document.removeEventListener('click', onClick, true)
  document.removeEventListener('mouseover', onMouseOver, true)
  document.removeEventListener('mouseout', onMouseOut, true)
  window.removeEventListener('scroll', hide, true)
})
</script>
