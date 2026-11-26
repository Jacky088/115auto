import { onBeforeUnmount, ref } from 'vue'

/**
 * 操作防抖与安全冷却倒计时组合式函数（防止高频连击触发风控）
 * @param {number} defaultSeconds 冷却秒数，默认 5 秒
 */
export const useCooldown = (defaultSeconds = 5) => {
  const cooldown = ref(0)
  let timer = null

  const startCooldown = (sec = defaultSeconds) => {
    if (timer) clearInterval(timer)
    cooldown.value = sec
    timer = setInterval(() => {
      cooldown.value--
      if (cooldown.value <= 0) {
        clearInterval(timer)
        timer = null
      }
    }, 1000)
  }

  onBeforeUnmount(() => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  })

  return { cooldown, startCooldown }
}
