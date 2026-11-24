<template>
  <div class="overview-grid page-card">
    <!-- 左卡：115 空间（胶囊进度条 + 三项结构化指标） -->
    <el-card shadow="never" class="ov-card space-card">
      <template #header>
        <div class="ov-header">
          <span class="ov-title">115 空间</span>
          <span v-if="space.total" class="space-badge" :class="spaceLevel">
            <span class="space-badge-dot"></span>
            {{ spaceRatioLabel }}
          </span>
        </div>
      </template>

      <div v-if="space.total" class="space-body">
        <div class="space-hero">
          <div class="space-pct-wrap">
            <span class="space-pct" :style="{ color: spaceColor }">{{ spaceRatioText }}%</span>
            <span class="space-pct-sub">已使用</span>
          </div>
          <span class="space-free-pct">剩余 {{ spaceFreePct }}%</span>
        </div>

        <div class="space-track" role="progressbar" :aria-valuenow="spaceRatio" aria-valuemin="0" aria-valuemax="100">
          <div
            class="space-fill"
            :class="spaceLevel"
            :style="{ width: `${spaceBarWidth}%` }"
          ></div>
        </div>

        <div class="space-metrics">
          <div class="space-metric">
            <span class="sm-label">已用空间</span>
            <span class="sm-val">{{ fmtSize(space.use) }}</span>
          </div>
          <div class="space-metric">
            <span class="sm-label">剩余可用</span>
            <span class="sm-val sm-val-free">{{ fmtSize(spaceFree) }}</span>
          </div>
          <div class="space-metric">
            <span class="sm-label">总容量</span>
            <span class="sm-val">{{ fmtSize(space.total) }}</span>
          </div>
        </div>
      </div>

      <div v-else class="ov-empty">
        {{ spaceLoaded ? '未登录或读取失败' : '正在读取空间信息…' }}
      </div>
    </el-card>

    <!-- 右卡：转存分类占比（左紧凑圆环 + 右分类明细榜 + 时间范围切换） -->
    <el-card shadow="never" class="ov-card cat-card">
      <template #header>
        <div class="ov-header">
          <div class="ov-title-group">
            <span class="ov-title">转存分类占比</span>
            <el-tooltip content="统计已成功转存及已提交云下载的有效记录" placement="top" effect="dark">
              <el-icon class="info-tip-icon" :size="14" tabindex="0"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="range-switch" role="group" aria-label="统计时间范围">
            <button
              v-for="opt in RANGE_OPTIONS"
              :key="opt.key"
              type="button"
              class="range-btn"
              :class="{ active: range === opt.key }"
              :aria-pressed="range === opt.key"
              @click="range = opt.key"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
      </template>

      <div v-if="currentBucket.total > 0" class="cat-body">
        <div class="cat-main">
          <div ref="pieRef" class="cat-donut"></div>

          <div class="cat-list">
            <div
              v-for="(item, idx) in rankedItems"
              :key="item.category"
              class="cat-row"
              :class="{ hovered: hoveredIdx === idx }"
              @mouseenter="onRowEnter(idx)"
              @mouseleave="onRowLeave(idx)"
            >
              <div class="cat-row-top">
                <span class="cat-name-wrap">
                  <span class="cat-dot" :style="{ background: item.color }"></span>
                  <span class="cat-name" :title="item.category">{{ item.category }}</span>
                </span>
                <span class="cat-meta">
                  <strong class="cat-count">{{ item.count }} 条</strong>
                  <span class="cat-sep">·</span>
                  <span class="cat-pct">{{ item.pctText }}%</span>
                  <template v-if="item.size > 0">
                    <span class="cat-sep">·</span>
                    <span class="cat-size">{{ fmtSize(item.size) }}</span>
                  </template>
                </span>
              </div>
              <div class="cat-bar-track">
                <div
                  class="cat-bar-fill"
                  :style="{ width: `${item.barWidth}%`, background: item.gradient }"
                ></div>
              </div>
            </div>
          </div>
        </div>

        <div class="cat-footer">
          <span>共 {{ rankedItems.length }} 个分类</span>
          <span v-if="currentBucket.total_size > 0" class="cat-footer-size">
            累计体积 {{ fmtSize(currentBucket.total_size) }}
          </span>
        </div>
      </div>

      <div v-else class="ov-empty">
        {{ emptyText }}
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { fmtSize } from '../../api'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([PieChart, TitleComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  space: {
    type: Object,
    default: () => ({ total: 0, use: 0, free: 0, ratio: 0 }),
  },
  spaceLoaded: {
    type: Boolean,
    default: false,
  },
  categories: {
    type: Object,
    default: () => ({}),
  },
})

const PALETTE = [
  { color: '#4f6bff', gradient: 'linear-gradient(90deg, #4f6bff 0%, #6f87ff 100%)' },
  { color: '#8b5cf6', gradient: 'linear-gradient(90deg, #8b5cf6 0%, #a78bfa 100%)' },
  { color: '#06b6d4', gradient: 'linear-gradient(90deg, #06b6d4 0%, #22d3ee 100%)' },
  { color: '#10b981', gradient: 'linear-gradient(90deg, #10b981 0%, #34d399 100%)' },
  { color: '#f59e0b', gradient: 'linear-gradient(90deg, #f59e0b 0%, #fbbf24 100%)' },
  { color: '#ec4899', gradient: 'linear-gradient(90deg, #ec4899 0%, #f472b6 100%)' },
  { color: '#6366f1', gradient: 'linear-gradient(90deg, #6366f1 0%, #818cf8 100%)' },
  { color: '#14b8a6', gradient: 'linear-gradient(90deg, #14b8a6 0%, #2dd4bf 100%)' },
]
const RANGE_OPTIONS = [
  { key: 'all', label: '全部', subtext: '累计条数' },
  { key: '30d', label: '近30天', subtext: '近30天条数' },
  { key: '7d', label: '近7天', subtext: '近7天条数' },
]

const range = ref('all')
const hoveredIdx = ref(-1)
const pieRef = ref(null)
let pieChart = null
let ro = null
let roTimer = null
let renderRetryTimer = null
let themeObs = null

// ---- 115 空间计算 ----
const spaceRatio = computed(() => {
  const r = Number(props.space?.ratio || 0)
  return Math.min(100, Math.max(0, r))
})
const spaceRatioText = computed(() => spaceRatio.value.toFixed(1))
const spaceFreePct = computed(() => Math.max(0, 100 - spaceRatio.value).toFixed(1))
const spaceBarWidth = computed(() => (spaceRatio.value > 0 ? Math.max(2, spaceRatio.value) : 0))
const spaceFree = computed(() => {
  if (props.space?.free) return Number(props.space.free)
  return Math.max(0, Number(props.space?.total || 0) - Number(props.space?.use || 0))
})
const spaceLevel = computed(() => (spaceRatio.value >= 90 ? 'danger' : spaceRatio.value >= 70 ? 'warn' : 'ok'))
const spaceColor = computed(() => (spaceRatio.value >= 90 ? '#ef4444' : spaceRatio.value >= 70 ? '#f59e0b' : '#4f6bff'))
const spaceRatioLabel = computed(() => (
  spaceRatio.value >= 90 ? '空间不足' : spaceRatio.value >= 70 ? '空间偏满' : '空间充足'
))

// ---- 分类占比计算 ----
const currentRangeMeta = computed(() => RANGE_OPTIONS.find((o) => o.key === range.value) || RANGE_OPTIONS[0])
const currentBucket = computed(() => {
  const b = props.categories?.[range.value]
  if (b && Array.isArray(b.items)) return b
  return { total: 0, total_size: 0, items: [] }
})
const rankedItems = computed(() => {
  const total = currentBucket.value.total || 0
  if (!total) return []
  return currentBucket.value.items.map((item, idx) => {
    const pct = (item.count / total) * 100
    const tone = PALETTE[idx % PALETTE.length]
    return {
      ...item,
      color: tone.color,
      gradient: tone.gradient,
      pct,
      pctText: pct >= 10 || Number.isInteger(pct) ? pct.toFixed(1).replace(/\.0$/, '') : pct.toFixed(1),
      barWidth: Math.max(2, pct),
    }
  })
})
const emptyText = computed(() => (
  range.value === 'all' ? '暂无有效转存记录' : `${currentRangeMeta.value.label}暂无有效转存记录`
))

// ---- ECharts 紧凑圆环渲染 ----
const chartSizeOk = (el) => !!(el && el.clientWidth > 0 && el.clientHeight > 0)
const chartIsDark = () => document.documentElement.classList.contains('dark')
const chartMainColor = () => (chartIsDark() ? '#e6eefc' : '#1e2a44')
const chartSubColor = () => (chartIsDark() ? '#8a97b3' : '#6b7a99')
const chartBorderColor = () => (chartIsDark() ? '#1b2438' : '#ffffff')

const renderPie = () => {
  const el = pieRef.value
  const items = rankedItems.value
  if (!el || !chartSizeOk(el) || !items.length) return
  if (!pieChart) {
    pieChart = echarts.init(el)
    pieChart.on('mouseover', (params) => {
      if (typeof params.dataIndex === 'number') hoveredIdx.value = params.dataIndex
    })
    pieChart.on('mouseout', () => {
      hoveredIdx.value = -1
    })
  }
  const total = currentBucket.value.total
  const totalText = total >= 10000 ? `${(total / 10000).toFixed(1)}w` : String(total)
  pieChart.setOption({
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (p) => {
        const raw = items[p.dataIndex]
        const sz = raw && raw.size > 0 ? ` · ${fmtSize(raw.size)}` : ''
        return `${p.name}：${p.value} 条（${p.percent}%）${sz}`
      },
    },
    title: {
      text: totalText,
      subtext: currentRangeMeta.value.subtext,
      left: 'center',
      top: '36.5%',
      itemGap: 4,
      textStyle: {
        fontSize: totalText.length >= 5 ? 18 : 23,
        fontWeight: 700,
        color: chartMainColor(),
      },
      subtextStyle: {
        fontSize: 11.5,
        color: chartSubColor(),
      },
    },
    series: [{
      type: 'pie',
      radius: ['54%', '88%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      label: { show: false },
      emphasis: {
        scale: true,
        scaleSize: 4,
      },
      itemStyle: {
        borderRadius: 7,
        borderColor: chartBorderColor(),
        borderWidth: 2.5,
      },
      data: items.map((it) => ({
        name: it.category,
        value: it.count,
        itemStyle: { color: it.color },
      })),
    }],
  }, true)
}

const retryRender = (tries = 5) => {
  clearTimeout(renderRetryTimer)
  if (pieChart && chartSizeOk(pieRef.value)) pieChart.resize()
  renderPie()
  if (rankedItems.value.length && !chartSizeOk(pieRef.value) && tries > 0) {
    renderRetryTimer = setTimeout(() => retryRender(tries - 1), 150)
  }
}

const onRowEnter = (idx) => {
  hoveredIdx.value = idx
  if (pieChart) pieChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx })
}
const onRowLeave = (idx) => {
  hoveredIdx.value = -1
  if (pieChart) pieChart.dispatchAction({ type: 'downplay', seriesIndex: 0, dataIndex: idx })
}

watch(pieRef, (el) => {
  if (!el) {
    if (pieChart) {
      pieChart.dispose()
      pieChart = null
    }
    return
  }
  if (ro) ro.observe(el)
  nextTick(() => retryRender())
})

watch([() => props.categories, range], () => {
  hoveredIdx.value = -1
  nextTick(() => retryRender())
}, { deep: true })

const onResize = () => {
  if (pieChart && chartSizeOk(pieRef.value)) pieChart.resize()
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  ro = new ResizeObserver(() => {
    clearTimeout(roTimer)
    roTimer = setTimeout(() => retryRender(), 100)
  })
  if (pieRef.value) ro.observe(pieRef.value)
  nextTick(() => retryRender())
  themeObs = new MutationObserver(() => renderPie())
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

onUnmounted(() => {
  clearTimeout(roTimer)
  clearTimeout(renderRetryTimer)
  window.removeEventListener('resize', onResize)
  if (ro) ro.disconnect()
  if (themeObs) themeObs.disconnect()
  if (pieChart) {
    pieChart.dispose()
    pieChart = null
  }
})
</script>

<style scoped>
.overview-grid {
  display: grid;
  grid-template-columns: 1fr 1.45fr;
  gap: 16px;
  align-items: stretch;
}
@media (max-width: 960px) {
  .overview-grid { grid-template-columns: 1fr; }
}

.ov-card {
  display: flex;
  flex-direction: column;
}
.ov-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 18px 20px;
}

.ov-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 26px;
}
.ov-title-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.ov-title {
  font-weight: 600;
}

.ov-empty {
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-body);
  padding: 46px 0;
}

/* ---- 左卡：115 空间 ---- */
.space-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: var(--r-pill);
  font-size: var(--fs-caption);
  font-weight: 600;
  line-height: 1.5;
}
.space-badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.space-badge.ok {
  color: #3b63f6;
  background: rgba(79, 107, 255, .12);
}
.space-badge.warn {
  color: #d97706;
  background: rgba(245, 158, 11, .14);
}
.space-badge.danger {
  color: #dc2626;
  background: rgba(239, 68, 68, .14);
}
html.dark .space-badge.ok { color: #8ba3ff; background: rgba(79, 107, 255, .22); }
html.dark .space-badge.warn { color: #fbbf24; background: rgba(245, 158, 11, .2); }
html.dark .space-badge.danger { color: #f87171; background: rgba(239, 68, 68, .22); }

.space-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.space-hero {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.space-pct-wrap {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}
.space-pct {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -.3px;
  font-variant-numeric: tabular-nums;
}
.space-pct-sub {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
}
.space-free-pct {
  font-size: var(--fs-caption);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.space-track {
  height: 18px;
  border-radius: var(--r-pill);
  background: rgba(79, 107, 255, .13);
  box-shadow: inset 0 1px 2px rgba(31, 45, 90, .06);
  overflow: hidden;
  padding: 2px;
}
html.dark .space-track {
  background: rgba(148, 163, 184, .18);
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, .25);
}
.space-fill {
  height: 100%;
  border-radius: var(--r-pill);
  transition: width .6s cubic-bezier(.22, 1, .36, 1);
}
.space-fill.ok {
  background: var(--grad-brand);
  box-shadow: 0 2px 12px rgba(79, 107, 255, .36), inset 0 1px 0 rgba(255, 255, 255, .32);
}
.space-fill.warn {
  background: linear-gradient(90deg, #f59e0b 0%, #fb923c 100%);
  box-shadow: 0 2px 12px rgba(245, 158, 11, .34), inset 0 1px 0 rgba(255, 255, 255, .32);
}
.space-fill.danger {
  background: linear-gradient(90deg, #ef4444 0%, #f43f5e 100%);
  box-shadow: 0 2px 12px rgba(239, 68, 68, .36), inset 0 1px 0 rgba(255, 255, 255, .32);
}

.space-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.space-metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--subcard-bg);
  border: 1.5px solid var(--saas-line);
}
.sm-label {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
}
.sm-val {
  font-size: 14px;
  font-weight: 700;
  color: var(--saas-text-main);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sm-val-free {
  color: #16a34a;
}
html.dark .sm-val-free {
  color: #4ade80;
}
@media (max-width: 480px) {
  .space-metrics { gap: 6px; }
  .space-metric { padding: 8px 8px; }
  .sm-val { font-size: 13px; }
}

/* ---- 右卡：转存分类占比 ---- */
.range-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--r-pill);
  background: rgba(79, 107, 255, .07);
  border: 1px solid var(--saas-line);
}
html.dark .range-switch {
  background: rgba(15, 23, 42, .45);
}
.range-btn {
  border: none;
  background: transparent;
  color: var(--saas-text-sub);
  font-family: inherit;
  font-size: var(--fs-caption);
  line-height: 1;
  padding: 5px 11px;
  border-radius: var(--r-pill);
  cursor: pointer;
  transition: all .18s ease;
  white-space: nowrap;
}
.range-btn:hover {
  color: var(--el-color-primary);
}
.range-btn.active {
  background: var(--grad-brand);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(79, 107, 255, .32);
}

.cat-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cat-main {
  display: flex;
  align-items: center;
  gap: 22px;
}
.cat-donut {
  width: 162px;
  height: 162px;
  flex: none;
}
.cat-list {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 168px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: rgba(126, 146, 196, .35) transparent;
}
.cat-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  border-radius: var(--r-sm);
  transition: background .15s ease;
}
.cat-row.hovered,
.cat-row:hover {
  background: var(--saas-hover);
}
.cat-row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: var(--fs-caption);
}
.cat-name-wrap {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.cat-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex: none;
}
.cat-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--saas-text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cat-meta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  color: var(--saas-text-sub);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.cat-count {
  color: var(--saas-text-main);
  font-weight: 600;
}
.cat-sep {
  opacity: .5;
}
.cat-size {
  color: var(--text-muted);
}

.cat-bar-track {
  height: 10px;
  border-radius: var(--r-pill);
  background: rgba(79, 107, 255, .12);
  box-shadow: inset 0 1px 2px rgba(31, 45, 90, .05);
  overflow: hidden;
}
html.dark .cat-bar-track {
  background: rgba(148, 163, 184, .18);
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, .24);
}
.cat-bar-fill {
  height: 100%;
  min-width: 10px;
  border-radius: var(--r-pill);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .28);
  transition: width .45s cubic-bezier(.22, 1, .36, 1);
}

.cat-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1.5px dashed var(--saas-line);
  font-size: var(--fs-caption);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.cat-footer-size {
  font-weight: 600;
  color: var(--saas-text-sub);
}

@media (max-width: 600px) {
  .cat-main {
    flex-direction: column;
    gap: 14px;
  }
  .cat-donut {
    width: 146px;
    height: 146px;
  }
  .cat-list {
    width: 100%;
    max-height: 196px;
  }
}
</style>
