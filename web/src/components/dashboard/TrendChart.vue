<template>
  <el-card shadow="never" class="page-card trend-card">
    <template #header>
      <div class="trend-head">
        <div class="trend-title-wrap">
          <span>转存与体积趋势</span>
          <span class="trend-summary">
            成功 <b>{{ summary.success }}</b> 笔 · 失败/拦截 <b>{{ summary.failed }}</b> 笔 · 累计 <b>{{ summary.gb }} GB</b>
          </span>
        </div>
        <div class="capsule-toggle">
          <button
            type="button"
            class="capsule-seg"
            :class="{ 'is-active': range === 7 }"
            @click="range = 7"
          >近 7 天</button>
          <button
            type="button"
            class="capsule-seg"
            :class="{ 'is-active': range === 30 }"
            @click="range = 30"
          >近 30 天</button>
        </div>
      </div>
    </template>
    <div ref="chartEl" class="trend-chart"></div>
  </el-card>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  trend: { type: Array, default: () => [] },
})

const range = ref(7)
const chartEl = ref(null)
let chart = null
let ro = null
let darkObs = null

const sliced = computed(() => {
  const arr = Array.isArray(props.trend) ? props.trend : []
  return arr.slice(-range.value)
})

const summary = computed(() => {
  let success = 0
  let failed = 0
  let gb = 0
  for (const d of sliced.value) {
    success += Number(d.success || 0)
    failed += Number(d.failed || 0) + Number(d.blocked || 0)
    gb += Number(d.size_gb || 0)
  }
  return { success, failed, gb: gb.toFixed(2) }
})

const renderChart = () => {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const dark = document.documentElement.classList.contains('dark')
  const textCol = dark ? '#9da4b3' : '#64748b'
  const lineCol = dark ? 'rgba(255,255,255,0.08)' : 'rgba(15,23,42,0.08)'
  const dates = sliced.value.map((d) => d.date)
  const okData = sliced.value.map((d) => Number(d.success || 0))
  const badData = sliced.value.map((d) => Number(d.failed || 0) + Number(d.blocked || 0))
  const gbData = sliced.value.map((d) => Number(d.size_gb || 0))

  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: {
      top: 0,
      textStyle: { color: textCol, fontSize: 12 },
      data: ['转存成功', '失败/拦截', '体积 (GB)'],
    },
    grid: { left: 38, right: 42, top: 34, bottom: 24 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: textCol, fontSize: 11 },
      axisLine: { lineStyle: { color: lineCol } },
    },
    yAxis: [
      {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: textCol, fontSize: 11 },
        splitLine: { lineStyle: { color: lineCol } },
      },
      {
        type: 'value',
        axisLabel: { color: textCol, fontSize: 11, formatter: '{value}G' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '转存成功',
        type: 'bar',
        stack: 'cnt',
        barMaxWidth: 18,
        itemStyle: { color: '#256bf5', borderRadius: [3, 3, 0, 0] },
        data: okData,
      },
      {
        name: '失败/拦截',
        type: 'bar',
        stack: 'cnt',
        barMaxWidth: 18,
        itemStyle: { color: '#ef4444', borderRadius: [3, 3, 0, 0] },
        data: badData,
      },
      {
        name: '体积 (GB)',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbolSize: 5,
        itemStyle: { color: '#10b981' },
        lineStyle: { width: 2, color: '#10b981' },
        data: gbData,
      },
    ],
  })
}

watch([sliced], () => renderChart(), { deep: true })

onMounted(() => {
  renderChart()
  if (typeof ResizeObserver !== 'undefined' && chartEl.value) {
    ro = new ResizeObserver(() => chart?.resize())
    ro.observe(chartEl.value)
  }
  darkObs = new MutationObserver(() => renderChart())
  darkObs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

onBeforeUnmount(() => {
  ro?.disconnect()
  darkObs?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.trend-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.trend-title-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.trend-summary {
  font-size: var(--fs-caption);
  color: var(--saas-text-sub);
  font-weight: 400;
}
.trend-chart {
  width: 100%;
  height: 220px;
}
.capsule-toggle {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--saas-line);
}
.capsule-seg {
  border: 0;
  background: transparent;
  padding: 4px 12px;
  font-size: var(--fs-caption);
  border-radius: 6px;
  cursor: pointer;
  color: var(--saas-text-sub);
  transition: all .15s ease;
}
.capsule-seg.is-active {
  background: var(--el-color-primary, #256bf5);
  color: #ffffff;
  font-weight: 600;
}
</style>
