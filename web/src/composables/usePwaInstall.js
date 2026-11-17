import { ref } from 'vue'

const deferredPrompt = ref(null)
const isInstalled = ref(false)
const canInstall = ref(false)

const checkStandalone = () => {
  if (typeof window === 'undefined') return false
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true ||
    document.referrer.includes('android-app://')
  )
}

if (typeof window !== 'undefined') {
  isInstalled.value = checkStandalone()

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferredPrompt.value = e
    canInstall.value = true
  })

  window.addEventListener('appinstalled', () => {
    canInstall.value = false
    isInstalled.value = true
    deferredPrompt.value = null
  })
}

export function usePwaInstall() {
  const isIos = typeof navigator !== 'undefined' && /iphone|ipad|ipod/i.test(navigator.userAgent || '')
  const isSafari = typeof navigator !== 'undefined' && /^((?!chrome|android).)*safari/i.test(navigator.userAgent || '')
  const isIosSafari = isIos && isSafari && !isInstalled.value

  const promptInstall = async () => {
    if (deferredPrompt.value) {
      deferredPrompt.value.prompt()
      const { outcome } = await deferredPrompt.value.userChoice
      if (outcome === 'accepted') {
        canInstall.value = false
      }
      deferredPrompt.value = null
    }
  }

  return {
    canInstall,
    isInstalled,
    isIosSafari,
    promptInstall,
  }
}
