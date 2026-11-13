<template>
  <el-container style="height: 100%">
    <!-- 移动端：侧边栏抽屉化 -->
    <el-drawer v-model="menuOpen" direction="ltr" size="216px" :with-header="false"
               class="menu-drawer">
      <div class="aside" style="height: 100%">
        <div class="logo">
          <div class="logo-mark"><el-icon :size="17"><Box /></el-icon></div>
          <div class="logo-text">115 自动转存</div>
        </div>
        <el-menu :default-active="$route.path" router class="aside-menu" @select="menuOpen = false">
          <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon>仪表盘</el-menu-item>
          <el-menu-item index="/files"><el-icon><FolderOpened /></el-icon>网盘文件</el-menu-item>
          <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
          <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
          <el-menu-item index="/media"><el-icon><MagicStick /></el-icon>整理刮削</el-menu-item>
          <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
          <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
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
        <div class="logo-mark"><el-icon :size="17"><Box /></el-icon></div>
        <div class="logo-text">115 自动转存</div>
      </div>
      <el-menu :default-active="$route.path" router class="aside-menu">
        <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon>仪表盘</el-menu-item>
        <el-menu-item index="/files"><el-icon><FolderOpened /></el-icon>网盘文件</el-menu-item>
        <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
        <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
        <el-menu-item index="/media"><el-icon><MagicStick /></el-icon>整理刮削</el-menu-item>
        <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
        <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
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
        <el-button class="menu-btn" text @click="menuOpen = true">
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
          <el-tooltip :content="isDark ? '切换到浅色模式' : '切换到深色模式'" placement="bottom">
            <el-button text class="theme-btn" @click="toggleTheme">
              <el-icon :size="17"><Sunny v-if="isDark" /><Moon v-else /></el-icon>
            </el-button>
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
                style="margin: 0 10px" class="cookie-alert" />
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
      <el-main style="padding: 0; overflow-y: auto; overflow-x: hidden">
        <router-view />
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
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk, clearLoggedIn } from '../api'
import { resetWizardCache } from '../router'

const route = useRoute()
const router = useRouter()
const titles = { dashboard: '仪表盘', files: '网盘文件', channels: '账号管理', subscriptions: '订阅追剧',
                 dirs: '转存目录', media: '整理刮削', match: '匹配规则',
                 transfers: '转存记录', history: '日志查询',
                 account: '账号管理', settings: '系统设置', setup: '初始化向导' }
const title = computed(() => titles[route.path.slice(1)] || '')

// 移动端菜单抽屉
const menuOpen = ref(false)
const sysRunning = ref(false)
const sysPaused = ref(false)
const sysReady = ref(false)
const sysReasons = ref([])
const autoStop = ref('')
const toggling = ref(false)

const loadSys = async () => {
  try {
    const { data } = await api.get('/api/system/state')
    sysRunning.value = data.running
    // 暂停态与运行态独立展示：Bot 侧 /pause 后网页顶栏同步可见、可恢复
    sysPaused.value = !!data.paused
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
}

// ---- 版本号：取免登录的 /api/health，失败静默不显示 ----
const REPO_URL = 'https://github.com/Jacky088/115auto'  // 仓库地址统一常量（改名/转移只改这里）
const version = ref('')
const loadVersion = async () => {
  try { version.value = (await api.get('/api/health')).data.version || '' } catch { /* ignore */ }
}

// 定时刷新系统状态/ cookie 状态：随组件销毁一起清理，避免退出登录后定时器累积
let sysTimer = null
let cookieTimer = null
onMounted(() => {
  loadSys()
  check()
  loadUsername()
  loadVersion()
  sysTimer = setInterval(loadSys, 10000)
  cookieTimer = setInterval(check, 10000)  // Cookie 巡检与状态同频：恢复后 10 秒内消除警示
})
onUnmounted(() => {
  clearInterval(sysTimer)
  clearInterval(cookieTimer)
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
const onCommand = async (cmd) => {
  if (cmd === 'pwd') {
    pwdForm.username = username.value
    pwdForm.old = ''; pwdForm.new = ''; pwdForm.confirm = ''
    pwdVisible.value = true
    nextTick(() => pwdFormRef.value?.clearValidate())
  }
  if (cmd === 'logout') {
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
.auto-stop-body { font-size: var(--fs-caption); line-height: 1.7; color: #606266; margin-top: 2px; }
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
  width: 32px; height: 32px; border-radius: 10px; flex: none;
  background: var(--grad-brand);
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 4px 12px rgba(79, 107, 255, .45);
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

.theme-btn { padding: 8px; color: var(--saas-text-main); }
/* 告警条是 el-container（flex 列）的子项，而 el-alert 自带 overflow:hidden：
   顶栏 + 多条告警 + 内容超过视口高度时 flex 会把告警压缩裁掉正文（表现为"被折叠"）。
   flex:none 让告警永远按内容完整显示，高度缺口全部由可滚动的 el-main 吸收 */
.cookie-alert, .auto-stop-alert { flex: none; }
.header {
  display: flex; align-items: center; gap: 8px;
  background: var(--saas-card);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border); padding: 0 12px;
}
.header-left { flex: 1; display: flex; align-items: center; gap: 10px; min-width: 0; }
.header-right { display: flex; align-items: center; gap: 6px; flex: none; }
.header-title {
  font-weight: 600; font-size: var(--fs-title); color: var(--saas-text-main);
  flex: none; display: inline-flex; align-items: center; gap: 8px;
}
/* 标题文字不折行；窄屏时允许必要收缩 */
.header-title .title-text { flex: none; white-space: nowrap; }
.user-chip { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 6px; border-radius: 999px; transition: background .15s ease; }
.user-chip:hover { background: rgba(79, 107, 255, .14); }
.avatar {
  width: 30px; height: 30px; border-radius: 50%;
  background: var(--grad-brand);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: var(--fs-body); font-weight: 600;
  box-shadow: 0 3px 10px rgba(79, 107, 255, .35);
}
.user-name { color: var(--saas-text-main); font-size: var(--fs-body); }
.menu-btn { display: none; padding: 6px; }
.start-wrap { display: inline-flex; flex: none; align-items: center; }
.start-btn {
  width: 68px; min-width: 68px; height: 28px; padding: 0 8px;
  justify-content: center; flex: none; box-sizing: border-box;
  /* 仅过渡背景/边框/文字色，绝不过渡宽高/内外边距，杜绝点击伸缩跳动 */
  transition: background-color .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease !important;
}
.start-btn.start-btn--resume {
  width: 88px; min-width: 88px;
}

/* 移动端 */
@media (max-width: 768px) {
  .pc-only { display: none; }
  .menu-btn { display: inline-flex; }
  .header-title { font-size: var(--fs-title); }
  .user-name { display: none; }
  .page-card, :deep(.page-card) { margin: 10px !important; }
  /* 防顶栏重叠：间距收紧。标题可收缩省略（.header-title/.title-text），
     状态标在省略区外始终可见；任何屏宽下运行按钮都不会再被右侧压住 */
  .header, .header-left { gap: 5px; }
}
</style>

<style>
/* 全局：菜单抽屉内背景（非 scoped 以作用到 el-drawer 内部） */
.menu-drawer .el-drawer__body { padding: 0; background: transparent; }
.menu-drawer .aside {
  background: var(--saas-sidebar-1);
  border-right: 1px solid var(--glass-border);
}
@media (max-width: 768px) {
  .el-message { max-width: calc(100vw - 24px) !important; }
}
</style>
