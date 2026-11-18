<template>
  <div class="dirs-page" v-loading="pageLoading">
    <CategoryRoutesCard ref="routesRef" @saved="onRoutesSaved" />
    <AutoMovePanel ref="moveRef" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import '../components/dirs/dirs.css'
import CategoryRoutesCard from '../components/dirs/CategoryRoutesCard.vue'
import AutoMovePanel from '../components/dirs/AutoMovePanel.vue'

const pageLoading = ref(true)
const routesRef = ref(null)
const moveRef = ref(null)

const onRoutesSaved = () => {
  moveRef.value?.loadAutoMove()
}

onMounted(async () => {
  try {
    await Promise.all([
      routesRef.value?.load(),
      moveRef.value?.loadAutoMove(),
    ])
  } finally {
    pageLoading.value = false
  }
})
</script>
