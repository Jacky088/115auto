<template>
  <!-- 组件按需注册后，语言包改由 ConfigProvider 提供给内部组件（分页/表格/时间选择等） -->
  <el-config-provider :locale="zhCn">
    <router-view />
    <GlobalTruncateTooltip />
  </el-config-provider>
</template>

<script setup>
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import GlobalTruncateTooltip from './components/GlobalTruncateTooltip.vue'
</script>
