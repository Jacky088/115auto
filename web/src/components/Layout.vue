<template>
  <el-container style="height: 100%">
    <!-- 移动端：侧边栏抽屉化 -->
    <el-drawer v-model="menuOpen" direction="ltr" size="216px" :with-header="false"
               class="menu-drawer">
      <div class="aside" style="height: 100%">
        <div class="logo">
          <img src="/icon-192.png" alt="115" class="logo-mark" />
          <div class="logo-text">115 自动转存</div>
        </div>
        <el-menu :default-active="$route.path" router class="aside-menu" @select="menuOpen = false">
          <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon>仪表盘</el-menu-item>
          <el-menu-item index="/files"><el-icon><FolderOpened /></el-icon>网盘文件</el-menu-item>
          <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
          <el-menu-item index="/transfers"><el-icon><List /></el-icon>分流记录</el-menu-item>
          <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
          <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
          <el-menu-item index="/media"><el-icon><MagicStick /></el-icon>整理刮削</el-menu-item>
          <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
          <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
          <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
        </el-menu>
        <a v-if="version" class="footer-ver aside-foot" :href="REPO_URL"
           target="_blank" rel="noopener" title="GitHub 仓库">
          <svg class="gh-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          <span>v{{ version }}</span>
        </a>
      </div>
    </el-drawer>
    <!-- 桌面端侧边栏 -->
    <el-aside width="216px" class="aside pc-only">
      <div class="logo">
        <img src="/icon-192.png" alt="115" class="logo-mark" />
        <div class="logo-text">115 自动转存</div>
      </div>
      <el-menu :default-active="$route.path" router class="aside-menu">
        <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon>仪表盘</el-menu-item>
        <el-menu-item index="/files"><el-icon><FolderOpened /></el-icon>网盘文件</el-menu-item>
        <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
        <el-menu-item index="/transfers"><el-icon><List /></el-icon>分流记录</el-menu-item>
        <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
        <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
        <el-menu-item index="/media"><el-icon><MagicStick /></el-icon>整理刮削</el-menu-item>
        <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
        <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
      </el-menu>
      <a v-if="version" class="footer-ver aside-foot" :href="REPO_URL"
         target="_blank" rel="noopener" title="GitHub 仓库">
        <svg class="gh-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
        <span>v{{ version }}</span>
      </a>
    </el-aside>
    <el-container>
      <el-header height="52px" class="header">
        <el-button class="menu-btn" text title="打开导航菜单" aria-label="打开导航菜单"
                   @click="menuOpen = true">
          <el-icon :size="20"><Fold /></el-icon>
        </el-button>
          <div class="header-left">
          <div class="header-title">
            <span class="title-text">{{ title }}</span>
            <el-tag disable-transitions v-if="sysRunning && !sysPaused" size="small" class="run-tag run-tag--live">
              <span class="live-dot"></span>运行中
            </el-tag>
            <el-tag disable-transitions v-else-if="sysRunning && sysPaused" size="small" class="run-tag run-tag--paused">
              <span class="paused-dot"></span>已暂停
            </el-tag>
            <el-tag disable-transitions v-else-if="sysReady" size="small" class="run-tag run-tag--ready">
              <span class="ready-dot"></span>待启动
            </el-tag>
            <el-tag disable-transitions v-else size="small" class="run-tag run-tag--not-ready">
              <span class="not-ready-dot"></span>未就绪
            </el-tag>
            <el-tooltip v-if="sysSpaceBlocked" placement="bottom"
                        :content="'115 空间不足（' + (sysSpaceReason || '剩余空间告急') + '）：触发本条已标记跳过且不再自动重试，后续任务保留排队、未被判失败，清理空间后自动继续；可在「设置 → 转存控制」关闭自动暂停'">
              <el-tag disable-transitions size="small" class="run-tag run-tag--space">
                <span class="space-dot"></span><span class="space-tag-full">空间不足·已暂停</span><span class="space-tag-short">空间不足</span>
              </el-tag>
            </el-tooltip>
          </div>
          <el-tooltip :disabled="sysRunning || sysReady" placement="bottom"
                      :content="sysReasons.length ? '无法开始运行：' + sysReasons.join('；') : ''">
            <span class="start-wrap">
              <el-button v-if="!sysRunning" type="primary" class="start-btn"
                         :disabled="!sysReady" :loading="toggling" @click="startSystem">运行</el-button>
              <el-button v-else-if="sysPaused" type="warning" class="start-btn start-btn--resume"
                         :loading="toggling" @click="resumeSystem">恢复转存</el-button>
              <el-button v-else type="danger" plain class="start-btn"
                         :loading="toggling" @click="stopSystem">停止</el-button>
            </span>
          </el-tooltip>
        </div>
        <div class="header-right">
          <!-- 下拉与快捷刷新按钮（移动端与“安装为应用”PWA 独立窗口常驻） -->
          <el-tooltip content="刷新当前数据" placement="bottom">
            <button class="theme-btn refresh-btn" :class="{ 'is-refreshing': isRefreshing }"
                    aria-label="刷新当前数据" title="刷新当前数据" @click="handlePageRefresh">
              <svg class="topbar-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                <path d="M3 3v5h5" />
                <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                <path d="M16 21h5v-5" />
              </svg>
            </button>
          </el-tooltip>
          <el-tooltip v-if="!isInstalled" content="安装为独立应用 (PWA)" placement="bottom">
            <button class="theme-btn" aria-label="安装为独立应用" @click="handleInstall">
              <svg class="topbar-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </button>
          </el-tooltip>
          <el-tooltip :content="isDark ? '切换到浅色模式' : '切换到深色模式'" placement="bottom">
            <button class="theme-btn"
                    :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
                    @click="toggleTheme">
              <svg v-if="isDark" class="topbar-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
              </svg>
              <svg v-else class="topbar-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
              </svg>
            </button>
          </el-tooltip>
          <el-dropdown @command="onCommand">
          <div class="user-chip">
            <div class="avatar">{{ (username || 'A').slice(0, 1).toUpperCase() }}</div>
            <span class="user-name">{{ username || 'admin' }}</span>
            <el-icon :size="12" color="#8a97b3"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="pwd">修改用户名 / 密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-alert v-if="cookieExpired" type="error" :closable="false" show-icon
                title="115 Cookie 已失效，转存已暂停，请前往「账号管理」重新扫码登录"
                style="margin: 0 10px" class="cookie-alert">
        <el-button size="small" type="danger" plain class="cookie-alert-btn"
                   @click="router.push('/account')">去账号管理</el-button>
      </el-alert>
      <el-alert v-if="autoStop" type="error" :closable="true" show-icon class="auto-stop-alert"
                style="margin: 8px 10px 0" @close="autoStop = ''">
        <template #title>
          系统已自动停止运行（运行期间检测到故障）
        </template>
        <div class="auto-stop-body">
          <div>原因：{{ autoStop }}</div>
          <div>处理：按上述原因修正后，点击网页顶栏「运行」恢复（队列与配置已保留，任务不会丢失）。</div>
        </div>
      </el-alert>
      <el-main ref="mainContainerRef" class="layout-main"
               style="padding: 0; overflow-y: auto; overflow-x: hidden; position: relative"
               @touchstart="handleTouchStart"
               @touchmove="handleTouchMove"
               @touchend="handleTouchEnd">
        <!-- 移动端下拉刷新极光玻璃指示器 -->
        <div class="pull-refresh-indicator"
             :class="{ 'is-refreshing': isRefreshing }"
             :style="{ transform: `translateY(${pullDistance}px)`, opacity: pullOpacity }">
          <div class="pull-refresh-bubble">
            <svg class="pull-refresh-icon"
                 :class="{ 'is-spinning': isRefreshing }"
                 :style="{ transform: isRefreshing ? '' : `rotate(${pullRotate}deg)` }"
                 viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 21h5v-5" />
            </svg>
            <span class="pull-refresh-text">{{ pullText }}</span>
          </div>
        </div>
        <router-view :key="routerKey" />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="pwdVisible" title="修改用户名 / 密码" :width="'min(440px, 94vw)'" class="pc-dialog">
    <el-form ref="pwdFormRef" :rules="pwdRules" :model="pwdForm" label-width="96px" @submit.prevent>
      <el-form-item label="用户名" prop="username">
        <el-input v-model="pwdForm.username" maxlength="32" placeholder="登录用户名" />
      </el-form-item>
      <el-form-item label="旧密码" prop="old">
        <el-input v-model="pwdForm.old" type="password" show-password placeholder="验证身份，必填" />
      </el-form-item>
      <el-form-item label="新密码" prop="new">
        <el-input v-model="pwdForm.new" type="password" show-password maxlength="128"
                  placeholder="不修改密码请留空（8~128 位）" />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirm">
        <el-input v-model="pwdForm.confirm" type="password" show-password
                  placeholder="再次输入新密码" />
      </el-form-item>
    </el-form>
    <div class="pwd-tips">
      <p class="pwd-tips-title">修改说明</p>
      <ul>
        <li>用户名：2~32 位，仅限中英文、数字与 @ . _ -；留空表示不修改用户名</li>
        <li>新密码：8~128 位，建议同时包含字母和数字；留空表示不修改密码</li>
        <li>两次新密码必须完全一致；修改任何一项都需先验证旧密码</li>
        <li>保存后立即生效，下次登录请使用新的用户名 / 密码</li>
      </ul>
    </div>
    <template #footer>
      <el-button @click="pwdVisible = false">取消</el-button>
      <el-button type="primary" @click="changePwd">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, provide, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, saveOk, clearLoggedIn } from '../api'
import { resetWizardCache } from '../router'
import { usePolling } from '../composables/usePolling'
import { usePwaInstall } from '../composables/usePwaInstall'
import { confirmAction } from '../composables/confirmDialog'

const route = useRoute()
const router = useRouter()
const titles = { dashboard: '仪表盘', files: '网盘文件', channels: '账号管理', subscriptions: '订阅追剧',
                 dirs: '转存目录', media: '整理刮削', match: '匹配规则',
                 transfers: '分流记录', history: '日志查询',
                 account: '账号管理', settings: '系统设置', setup: '初始化向导' }
const title = computed(() => titles[route.path.slice(1)] || '')

// 移动端菜单抽屉
const menuOpen = ref(false)
const sysRunning = ref(false)
const sysPaused = ref(false)
const sysReady = ref(false)
const sysReasons = ref([])
const sysSpaceBlocked = ref(false)
const sysSpaceReason = ref('')
const autoStop = ref('')
const toggling = ref(false)

// 页面刷新与手势下拉刷新状态
const routerKey = ref(0)
const isRefreshing = ref(false)
const mainContainerRef = ref(null)

const touchStartY = ref(0)
const pullDistance = ref(0)
const isPulling = ref(false)

const PULL_THRESHOLD = 50 // 触发刷新的临界位移
const PULL_MAX = 74       // 最大弹性拉伸位移

const pullOpacity = computed(() => {
  if (isRefreshing.value) return 1
  if (pullDistance.value <= 4) return 0
  return Math.min(1, pullDistance.value / 24)
})

const pullRotate = computed(() => pullDistance.value * 5.5)

const pullText = computed(() => {
  if (isRefreshing.value) return '正在刷新数据...'
  return pullDistance.value >= PULL_THRESHOLD ? '释放立即刷新' : '下拉刷新'
})

const handleTouchStart = (e) => {
  if (isRefreshing.value) return
  const container = mainContainerRef.value?.$el || mainContainerRef.value
  if (!container || container.scrollTop > 0) {
    isPulling.value = false
    return
  }
  if (e.touches && e.touches.length === 1) {
    touchStartY.value = e.touches[0].clientY
    isPulling.value = true
  }
}

const handleTouchMove = (e) => {
  if (!isPulling.value || isRefreshing.value) return
  const container = mainContainerRef.value?.$el || mainContainerRef.value
  if (!container || container.scrollTop > 0) {
    pullDistance.value = 0
    return
  }
  if (e.touches && e.touches.length === 1) {
    const deltaY = e.touches[0].clientY - touchStartY.value
    if (deltaY > 0) {
      const dist = Math.min(deltaY * 0.42, PULL_MAX)
      pullDistance.value = dist
      if (dist > 6 && e.cancelable) {
        e.preventDefault()
      }
    } else {
      pullDistance.value = 0
    }
  }
}

const handleTouchEnd = () => {
  if (!isPulling.value) return
  isPulling.value = false
  if (pullDistance.value >= PULL_THRESHOLD && !isRefreshing.value) {
    pullDistance.value = 46
    handlePageRefresh()
  } else {
    pullDistance.value = 0
  }
}

const handlePageRefresh = async () => {
  if (isRefreshing.value) return
  isRefreshing.value = true
  try {
    await Promise.allSettled([
      loadSys(),
      check(),
    ])
    window.dispatchEvent(new CustomEvent('app:refresh'))
  } catch { /* ignore */ }
  routerKey.value++
  setTimeout(() => {
    isRefreshing.value = false
    pullDistance.value = 0
    ElMessage.success({ message: '已刷新数据', duration: 1200, grouping: true })
  }, 650)
}

provide('refreshPage', handlePageRefresh)

const loadSys = async () => {
  try {
    const { data } = await api.get('/api/system/state')
    sysRunning.value = data.running
    // 暂停态与运行态独立展示：Bot 侧 /pause 后网页顶栏同步可见、可恢复
    sysPaused.value = !!data.paused
    // 空间门闸：队列因 115 空间不足自动挂起时，顶栏给出醒目提示（任务仍在排队，不会丢）
    sysSpaceBlocked.value = !!data.space_blocked
    sysSpaceReason.value = data.space_blocked_reason || ''
    // 就绪 = 115 登录 + 启用中频道 +（纯 Userbot 频道时）Userbot 在线；未就绪时按钮灰置并提示缺什么
    sysReady.value = !!data.ready
    sysReasons.value = data.reasons || []
    // 运行期健康监控自动停止的原因（运行中或已手动恢复时后端返回空）
    autoStop.value = data.auto_stop_reason || ''
  } catch { /* ignore */ }
}
const startSystem = async () => {
  toggling.value = true
  try {
    await api.post('/api/system/start')
    saveOk('系统已启动', '开始监控频道并自动转存')
    await loadSys()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { toggling.value = false }
}
const resumeSystem = async () => {
  toggling.value = true
  try {
    await api.post('/api/system/resume')
    saveOk('已恢复转存', '队列继续按优先级处理')
    await loadSys()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { toggling.value = false }
}
const stopSystem = async () => {
  if (!await confirmAction({
    title: '确定停止系统运行？',
    message: '停止后将暂停频道监听、自动转存与定时整理（配置与排队任务保留，随时可重新启动）。',
    confirmText: '确认停止',
    danger: true,
  })) return
  toggling.value = true
  try {
    await api.post('/api/system/stop')
    saveOk('系统已停止', '配置与队列保留')
    await loadSys()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { toggling.value = false }
}

const isMobile = ref(window.innerWidth <= 768)
const onResize = () => {
  isMobile.value = window.innerWidth <= 768
  if (!isMobile.value) menuOpen.value = false
}
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

const cookieExpired = ref(false)
let prevCookieOk = null  // 上一次巡检的已知状态：true=有效 / false=失效 / null=未知
const check = async () => {
  try {
    const { data } = await api.get('/api/wizard/status')
    const bad = data.cookie_ok === false
    cookieExpired.value = bad && data.completed
    // Cookie 从失效→有效的「状态翻转」那一拍才提示（仅一次，不会重复弹）
    if (prevCookieOk === false && !bad) {
      autoStop.value = ''
      ElMessage.success(sysRunning.value
        ? '✅ 115 Cookie 已恢复有效，转存继续进行'
        : '✅ 115 Cookie 已恢复有效；点顶栏「运行」恢复转存')
    }
    // 记录本次已知状态：只有确认有效/失效才更新，未知（null）保持原状
    if (bad) prevCookieOk = false
    else if (data.cookie_ok === true) prevCookieOk = true
  } catch { /* ignore */ }
}

// ---- 深色模式：偏好存 localStorage('theme')，初始类已在 main.js 挂载前应用 ----
const isDark = ref(localStorage.getItem('theme') === 'dark')
const toggleTheme = () => {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  try { window.AndroidBridge?.setDarkMode(isDark.value) } catch { /* ignore */ }
}

// ---- 版本号：取免登录的 /api/health，失败静默不显示 ----
const REPO_URL = 'https://github.com/Jacky088/115auto'  // 仓库地址统一常量（改名/转移只改这里）
const version = ref('')
const loadVersion = async () => {
  try { version.value = (await api.get('/api/health')).data.version || '' } catch { /* ignore */ }
}

// 定时刷新系统状态/ cookie 状态：页面不可见时暂停轮询，组件销毁时自动清理（避免退出登录后定时器累积）
const sysPoll = usePolling(loadSys, 10000)
const cookiePoll = usePolling(check, 10000)  // Cookie 巡检与状态同频：恢复后 10 秒内消除警示
onMounted(() => {
  try { window.AndroidBridge?.setDarkMode(isDark.value) } catch { /* ignore */ }
  loadSys()
  check()
  loadUsername()
  loadVersion()
  sysPoll.start()
  cookiePoll.start()
})

const pwdVisible = ref(false)
const pwdForm = reactive({ username: '', old: '', new: '', confirm: '' })
const pwdFormRef = ref()
// 与后端 /api/auth/password 的校验规则保持一致
const _USERNAME_RE = /^[\w@.\-一-龥]{2,32}$/
const pwdRules = {
  username: [{
    validator: (_r, v, cb) => {
      if (!v) return cb()  // 留空 = 不修改用户名
      if (!_USERNAME_RE.test(v)) return cb(new Error('2~32 位，仅限中英文、数字与 @ . _ -'))
      cb()
    }, trigger: 'blur',
  }],
  old: [{ required: true, message: '请输入旧密码验证身份', trigger: 'blur' }],
  new: [{
    validator: (_r, v, cb) => {
      if (!v) return cb()  // 留空 = 不修改密码
      if (v.length < 8) return cb(new Error('新密码至少 8 位'))
      if (v.length > 128) return cb(new Error('新密码最长 128 位'))
      if (pwdForm.confirm && v !== pwdForm.confirm) return cb(new Error('两次输入的新密码不一致'))
      cb()
    }, trigger: 'blur',
  }],
  confirm: [{
    validator: (_r, v, cb) => {
      if (!pwdForm.new) return cb()  // 未填写新密码时忽略
      if (!v) return cb(new Error('请再次输入新密码'))
      if (v !== pwdForm.new) return cb(new Error('两次输入的新密码不一致'))
      cb()
    }, trigger: 'blur',
  }],
}
const username = ref('')
const loadUsername = async () => {
  try { username.value = (await api.get('/api/auth/me')).data.username || 'admin' } catch { /* ignore */ }
}
const { canInstall, isInstalled, isIosSafari, promptInstall } = usePwaInstall()
const handleInstall = () => {
  if (canInstall.value) {
    promptInstall()
  } else if (isIosSafari) {
    ElMessageBox.alert(
      '在 iPhone / iPad Safari 上安装为独立 App：\n\n1. 点击 Safari 底部工具栏的【分享】图标（方框带向上箭头）；\n2. 向上滑动菜单，点击【添加到主屏幕】；\n3. 点击右上角【添加】即可！',
      '添加到主屏幕',
      { confirmButtonText: '我知道了' }
    )
  } else {
    ElMessageBox.alert(
      '将 115AutoSave 安装为桌面独立应用：\n\n1. Chrome / Edge 浏览器：点击地址栏最右侧的【安装】图标 ⊕，或点击右上角「···」菜单 →「应用」→「将此站点作为应用安装」；\n2. 局域网访问提示：浏览器规范要求仅在 HTTPS 或 localhost 下支持一键弹窗；局域网 IP 访问请通过上述浏览器菜单手动添加；\n3. 手机/平板设备：在浏览器菜单选择「添加到主屏幕」即可。',
      '安装为独立应用',
      { confirmButtonText: '我知道了' }
    )
  }
}

const onCommand = async (cmd) => {
  if (cmd === 'pwd') {
    pwdForm.username = username.value
    pwdForm.old = ''; pwdForm.new = ''; pwdForm.confirm = ''
    pwdVisible.value = true
    nextTick(() => pwdFormRef.value?.clearValidate())
  }
  if (cmd === 'logout') {
    if (!await confirmAction({
      title: '确认退出当前账号？',
      message: '退出后需重新输入用户名和密码登录管理面板（后台服务运行状态不受影响）。',
      confirmText: '确认退出',
      danger: true,
    })) return
    // 同时清除服务端海报 HttpOnly Cookie；网络失败不影响前端退出
    try { await api.post('/api/auth/logout') } catch { /* ignore */ }
    clearLoggedIn()  // 登出时服务端已吊销全部 token 并清除 HttpOnly Cookie
    resetWizardCache()  // 下次登录重新检查向导状态（可能换账号/重置数据）
    router.push('/login')
  }
}
const changePwd = async () => {
  try {
    await pwdFormRef.value.validate()
  } catch { return }  // 校验不通过：字段下方已显示红字原因，阻止修改
  if (!pwdForm.username.trim() && !pwdForm.new) { ElMessage.warning('新用户名与新密码至少填写一项'); return }
  if (pwdForm.confirm && !pwdForm.new) { ElMessage.warning('已填写确认新密码，请同时填写新密码'); return }
  try {
    await api.post('/api/auth/password', pwdForm)
    // 服务端已轮换 JWT 签名密钥：所有旧 token（含本会话）立即失效
    // 强制退出到登录页，必须用新用户名/新密码重新登录（安全要求，不做无感续期）
    clearLoggedIn()
    resetWizardCache()
    ElMessage.success('账号已更新，请使用新的用户名 / 密码重新登录')
    setTimeout(() => router.push('/login'), 800)
  } catch (e) { ElMessage.error(errMsg(e)) }
}
</script>

<style scoped>
.auto-stop-body { font-size: var(--fs-caption); line-height: 1.7; color: var(--el-text-color-regular); margin-top: 2px; }
.pwd-tips {
  margin-top: 4px; padding: 10px 12px; border-radius: 8px;
  background: #f6f9ff; border: 1px solid #e9eefb;
}
.pwd-tips-title { margin: 0 0 6px; font-size: var(--fs-caption); font-weight: 600; color: #3a4661; }
.pwd-tips ul { margin: 0; padding-left: 16px; }
.pwd-tips li { font-size: var(--fs-caption); line-height: 1.8; color: #7c8aa5; list-style: disc; }
.aside {
  height: 100%;
  display: flex; flex-direction: column;
  background: var(--saas-sidebar-1);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-right: 1px solid var(--glass-border);
}
.logo { display: flex; align-items: center; gap: 10px; padding: 20px 18px 16px; }
.logo-mark {
  width: 32px; height: 32px; border-radius: var(--r-md); flex: none;
  display: block; object-fit: contain;
  box-shadow: 0 4px 12px rgba(34, 120, 248, .28);
}
.logo-text { color: var(--saas-text-main); font-weight: 600; font-size: var(--fs-title); letter-spacing: .5px; }
/* 菜单撑满剩余空间，把版本号压到侧栏最底部；菜单过长时自身滚动 */
.aside-menu { flex: 1; min-height: 0; overflow-y: auto; }
/* 版本号 + GitHub 链接：固定在侧边栏最下方，水平居中 */
.aside-foot { flex: none; display: flex; justify-content: center; padding: 12px 0 14px; }
.footer-ver {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--saas-text-sub); font-size: var(--fs-caption);
  text-decoration: none; opacity: .75; transition: opacity .2s;
}
.footer-ver:hover { opacity: 1; color: var(--saas-text-main); }
.gh-icon { width: 14px; height: 14px; fill: currentColor; flex: none; }

.theme-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--saas-text-main);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  padding: 0;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.theme-btn:hover {
  color: var(--el-color-primary);
  background: rgba(79, 107, 255, 0.10);
  border-color: rgba(79, 107, 255, 0.20);
}
html.dark .theme-btn:hover {
  color: var(--el-color-primary-light-3);
  background: rgba(125, 144, 255, 0.16);
  border-color: rgba(125, 144, 255, 0.30);
}
.topbar-svg {
  display: block;
  flex-shrink: 0;
}
/* 告警条是 el-container（flex 列）的子项，而 el-alert 自带 overflow:hidden：
   顶栏 + 多条告警 + 内容超过视口高度时 flex 会把告警压缩裁掉正文（表现为"被折叠"）。
   flex:none 让告警永远按内容完整显示，高度缺口全部由可滚动的 el-main 吸收 */
.cookie-alert, .auto-stop-alert { flex: none; }
/* Cookie 失效横幅里的快捷跳转按钮：与告警正文留出间距，窄屏不挤压标题 */
.cookie-alert-btn { margin-top: 6px; }
.header {
  display: flex; align-items: center; gap: 8px;
  flex-wrap: nowrap !important; white-space: nowrap;
  background: var(--saas-card);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border); padding: 0 12px;
  box-sizing: border-box;
}
.header-left { flex: 1 1 auto; display: flex; align-items: center; gap: 10px; min-width: 0; overflow: hidden; white-space: nowrap; }
.header-right { display: flex; align-items: center; gap: 3px; flex-shrink: 0; margin-left: auto; white-space: nowrap; }
.header-title {
  font-weight: 600; font-size: var(--fs-title); color: var(--saas-text-main);
  flex: 0 1 auto; display: inline-flex; align-items: center; gap: 8px;
  min-width: 0; overflow: hidden;
}
/* 标题文字不折行；窄屏时允许必要收缩 */
.header-title .title-text { flex: 0 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.run-tag { flex-shrink: 0; white-space: nowrap; }
.space-tag-short { display: none; }
.user-chip { display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 2px 6px; border-radius: var(--r-pill); transition: background .15s ease; flex-shrink: 0; }
.user-chip:hover { background: rgba(79, 107, 255, .14); }
.avatar {
  width: 30px; height: 30px; border-radius: 50%;
  background: var(--grad-brand);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: var(--fs-body); font-weight: 600;
  box-shadow: 0 3px 10px rgba(79, 107, 255, .35);
  flex-shrink: 0;
}
.user-name { color: var(--saas-text-main); font-size: var(--fs-body); }
.menu-btn { display: none; padding: 6px; flex-shrink: 0; }
.start-wrap { display: inline-flex; flex-shrink: 0; align-items: center; }
.start-btn {
  width: 68px; min-width: 68px; height: 28px; padding: 0 8px;
  justify-content: center; flex: none; box-sizing: border-box;
  /* 仅过渡背景/边框/文字色，绝不过渡宽高/内外边距，杜绝点击伸缩跳动 */
  transition: background-color .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease !important;
}
.start-btn.start-btn--resume {
  width: 88px; min-width: 88px;
}

/* 移动端与自适应全屏防重叠 */
@media (max-width: 768px) {
  .pc-only { display: none; }
  .menu-btn { display: inline-flex; padding: 4px; margin-right: -2px; flex-shrink: 0; }
  .header { padding: 0 8px; gap: 4px; }
  .header-left { gap: 6px; min-width: 0; flex: 1 1 auto; overflow: hidden; }
  .header-title { font-size: var(--fs-title); gap: 5px; min-width: 0; flex: 0 1 auto; overflow: hidden; }
  .header-title .title-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 72px; }
  .run-tag {
    flex-shrink: 0;
    padding: 0 6px !important;
    height: 22px !important;
    line-height: 20px !important;
    font-size: 11px !important;
  }
  .space-tag-full { display: none; }
  .space-tag-short { display: inline; }
  .start-wrap { flex-shrink: 0; }
  .start-btn { width: 56px; min-width: 56px; height: 26px; padding: 0 5px; font-size: 12px; }
  .start-btn.start-btn--resume { width: 72px; min-width: 72px; }
  .user-name { display: none; }
  .user-chip { padding: 1px 2px; gap: 0; }
  .user-chip .el-icon { display: none !important; }
  .header-right { gap: 2px; flex-shrink: 0; margin-left: auto; }
  .theme-btn { width: 28px; height: 28px; padding: 2px; flex-shrink: 0; }
  .avatar { width: 26px; height: 26px; font-size: var(--fs-caption); }
  .page-card, :deep(.page-card) { margin: 10px !important; }
}

/* 针对小屏手机（iPhone SE、mini、360px 等屏幕）精细化阶梯防护，确保万无一失绝对不重叠 */
@media (max-width: 390px) {
  .header { padding: 0 6px; gap: 3px; }
  .header-left { gap: 4px; }
  .header-title { gap: 4px; }
  .header-title .title-text { max-width: 48px; font-size: 13px; }
  .start-btn { width: 52px; min-width: 52px; font-size: 11px; padding: 0 4px; }
  .start-btn.start-btn--resume { width: 66px; min-width: 66px; }
  .header-right { gap: 1px; }
}

@media (max-width: 340px) {
  /* 极窄屏（320px~340px）：隐藏冗余标题文本，状态胶囊与控制按钮绝对完整展示 */
  .header-title .title-text { display: none; }
}

/* 顶栏刷新按钮旋转动画 */
.refresh-btn.is-refreshing svg {
  animation: spin-refresh 0.75s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
@keyframes spin-refresh {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 移动端下拉刷新气泡指示器 */
.pull-refresh-indicator {
  position: absolute;
  top: -46px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  pointer-events: none;
  z-index: 99;
  transition: transform 0.16s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.2s ease;
}
.pull-refresh-indicator.is-refreshing {
  transition: transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.2s ease;
}
.pull-refresh-bubble {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 14px;
  background: var(--saas-card);
  border: 1px solid var(--glass-border);
  box-shadow: 0 4px 16px rgba(79, 107, 255, 0.16);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border-radius: 20px;
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 500;
}
.pull-refresh-icon {
  display: block;
  flex-shrink: 0;
  transition: transform 0.08s linear;
}
.pull-refresh-icon.is-spinning {
  animation: spin-refresh 0.75s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

/* PWA 独立全屏 / 移动端刘海与状态栏避让（兼容 iOS Safari 与 Android APK 沉浸全屏） */
.header {
  padding-top: var(--android-status-bar-height, env(safe-area-inset-top, 0px));
  height: calc(52px + var(--android-status-bar-height, env(safe-area-inset-top, 0px))) !important;
}
.menu-drawer .aside {
  padding-top: var(--android-status-bar-height, env(safe-area-inset-top, 0px));
  padding-bottom: env(safe-area-inset-bottom, 0px);
}
</style>

<style>
/* 全局：菜单抽屉内背景（非 scoped 以作用到 el-drawer 内部） */
.menu-drawer .el-drawer__body { padding: 0; background: transparent; }
.menu-drawer .aside {
  background: var(--saas-sidebar-1);
  border-right: 1px solid var(--glass-border);
}
</style>
