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
          <el-menu-item index="/channels"><el-icon><Connection /></el-icon>频道管理</el-menu-item>
          <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
          <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
          <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
          <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
          <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
          <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
          <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
        </el-menu>
        <div class="aside-footer">
          <div>Telegram → 115 自动化</div>
          <div v-if="version" class="aside-ver">v{{ version }}</div>
        </div>
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
        <el-menu-item index="/channels"><el-icon><Connection /></el-icon>频道管理</el-menu-item>
        <el-menu-item index="/subscriptions"><el-icon><VideoPlay /></el-icon>订阅追剧</el-menu-item>
        <el-menu-item index="/dirs"><el-icon><Collection /></el-icon>转存目录</el-menu-item>
        <el-menu-item index="/match"><el-icon><Sort /></el-icon>匹配规则</el-menu-item>
        <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
        <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
        <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
      </el-menu>
      <div class="aside-footer">
        <div>Telegram → 115 自动化</div>
        <div v-if="version" class="aside-ver">v{{ version }}</div>
      </div>
    </el-aside>
    <el-container>
      <el-header height="52px" class="header">
        <el-button class="menu-btn" text @click="menuOpen = true">
          <el-icon :size="20"><Fold /></el-icon>
        </el-button>
        <div class="header-title">
          {{ title }}
          <el-tag v-if="sysRunning" type="success" size="small" effect="dark" class="run-tag">运行中</el-tag>
          <el-tag v-else-if="sysReady" type="warning" size="small" effect="dark" class="run-tag">待启动</el-tag>
          <el-tag v-else type="info" size="small" effect="dark" class="run-tag">未就绪</el-tag>
        </div>
        <el-tooltip :disabled="sysRunning || sysReady" placement="bottom"
                    :content="sysReasons.length ? '无法开始运行：' + sysReasons.join('；') : ''">
          <span class="start-wrap">
            <el-button v-if="!sysRunning" type="primary" class="start-btn"
                       :disabled="!sysReady" :loading="toggling" @click="startSystem">开始运行</el-button>
            <el-button v-else type="danger" plain class="start-btn"
                       :loading="toggling" @click="stopSystem">停止</el-button>
          </span>
        </el-tooltip>
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
      </el-header>
      <el-alert v-if="cookieExpired" type="error" :closable="false" show-icon
                title="115 Cookie 已失效，转存已暂停 —— 请前往「账号管理」重新扫码登录"
                style="margin: 0 10px" class="cookie-alert" />
      <el-alert v-if="autoStop" type="error" :closable="true" show-icon class="auto-stop-alert"
                style="margin: 8px 10px 0" @close="autoStop = ''">
        <template #title>
          ⛔ 系统已自动停止运行（运行期间检测到故障）
        </template>
        <div class="auto-stop-body">
          <div>❓ {{ autoStop }}</div>
          <div>🔧 请按上述提示修正后，点击顶栏「开始运行」恢复；停止期间队列与配置保留，任务不会丢失。</div>
        </div>
      </el-alert>
      <el-main style="padding: 0; overflow-y: auto; overflow-x: hidden">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="pwdVisible" title="修改用户名 / 密码" width="420px" class="pc-dialog">
    <el-form ref="pwdFormRef" :rules="pwdRules" :model="pwdForm" label-width="80px" @submit.prevent>
      <el-form-item label="用户名" prop="username">
        <el-input v-model="pwdForm.username" maxlength="32" placeholder="登录用户名" />
      </el-form-item>
      <el-form-item label="旧密码" prop="old">
        <el-input v-model="pwdForm.old" type="password" show-password placeholder="验证身份，必填" />
      </el-form-item>
      <el-form-item label="新密码" prop="new">
        <el-input v-model="pwdForm.new" type="password" show-password maxlength="128"
                  placeholder="不修改密码请留空（8-128 位）" />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirm">
        <el-input v-model="pwdForm.confirm" type="password" show-password
                  placeholder="再次输入新密码" />
      </el-form-item>
    </el-form>
    <div class="pwd-tips">
      <p class="pwd-tips-title">修改说明</p>
      <ul>
        <li>用户名：2-32 位，仅限中英文、数字与 @ . _ -；留空表示不修改用户名</li>
        <li>新密码：8-128 位，建议同时包含字母和数字；留空表示不修改密码</li>
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
import { api, errMsg, saveOk } from '../api'

const route = useRoute()
const router = useRouter()
const titles = { dashboard: '仪表盘', files: '网盘文件', channels: '频道管理', subscriptions: '订阅追剧',
                 dirs: '转存目录', match: '匹配规则',
                 transfers: '转存记录', history: '日志查询',
                 account: '账号管理', settings: '系统设置', setup: '初始化向导' }
const title = computed(() => titles[route.path.slice(1)] || '')

// 移动端菜单抽屉
const menuOpen = ref(false)
const sysRunning = ref(false)
const sysReady = ref(false)
const sysReasons = ref([])
const autoStop = ref('')
const toggling = ref(false)

const loadSys = async () => {
  try {
    const { data } = await api.get('/api/system/state')
    sysRunning.value = data.running
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
    loadSys()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { toggling.value = false }
}
const stopSystem = async () => {
  toggling.value = true
  try {
    await api.post('/api/system/stop')
    ElMessage.success('系统已停止，配置与队列保留')
    loadSys()
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
const check = async () => {
  try {
    const { data } = await api.get('/api/wizard/status')
    cookieExpired.value = data.cookie_ok === false && data.completed
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
  cookieTimer = setInterval(check, 60000)
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
      if (!_USERNAME_RE.test(v)) return cb(new Error('2-32 位，仅限中英文、数字与 @ . _ -'))
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
const onCommand = (cmd) => {
  if (cmd === 'pwd') {
    pwdForm.username = username.value
    pwdForm.old = ''; pwdForm.new = ''; pwdForm.confirm = ''
    pwdVisible.value = true
    nextTick(() => pwdFormRef.value?.clearValidate())
  }
  if (cmd === 'logout') { localStorage.removeItem('token'); router.push('/login') }
}
const changePwd = async () => {
  try {
    await pwdFormRef.value.validate()
  } catch { return }  // 校验不通过：字段下方已显示红字原因，阻止修改
  if (!pwdForm.username.trim() && !pwdForm.new) { ElMessage.warning('新用户名与新密码至少填一项'); return }
  if (pwdForm.confirm && !pwdForm.new) { ElMessage.warning('已填写确认新密码，请同时填写新密码'); return }
  try {
    await api.post('/api/auth/password', pwdForm)
    // 服务端已轮换 JWT 签名密钥：所有旧 token（含本会话）立即失效
    // 强制退出到登录页，必须用新用户名/新密码重新登录（安全要求，不做无感续期）
    localStorage.removeItem('token')
    ElMessage.success('账号已更新，请使用新的用户名 / 密码重新登录')
    setTimeout(() => router.push('/login'), 800)
  } catch (e) { ElMessage.error(errMsg(e)) }
}
</script>

<style scoped>
.auto-stop-body { font-size: 12px; line-height: 1.7; color: #606266; margin-top: 2px; }
.pwd-tips {
  margin-top: 4px; padding: 10px 12px; border-radius: 8px;
  background: #f6f9ff; border: 1px solid #e9eefb;
}
.pwd-tips-title { margin: 0 0 6px; font-size: 12px; font-weight: 600; color: #3a4661; }
.pwd-tips ul { margin: 0; padding-left: 16px; }
.pwd-tips li { font-size: 12px; line-height: 1.8; color: #7c8aa5; list-style: disc; }
.aside { background: linear-gradient(180deg, var(--saas-sidebar-1) 0%, var(--saas-sidebar-2) 100%); }
.logo { display: flex; align-items: center; gap: 10px; padding: 20px 18px 16px; }
.logo-mark {
  width: 32px; height: 32px; border-radius: 9px; flex: none;
  background: linear-gradient(135deg, #2f6bff, #5d8bff);
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 4px 10px rgba(47, 107, 255, .4);
}
.logo-text { color: #fff; font-weight: 600; font-size: 15px; letter-spacing: .5px; }
.aside-menu { flex: 1; }
.aside-footer {
  color: rgba(174, 189, 223, .45); font-size: 11px; text-align: center; padding: 14px 0;
}
.aside-ver { margin-top: 3px; letter-spacing: .5px; }
.theme-btn { padding: 8px; color: #44506b; }
.header {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  background: #fff; border-bottom: 1px solid var(--saas-line); padding: 0 12px;
}
.header-title { font-weight: 600; font-size: 15px; color: var(--saas-text-main); flex: 1; }
.user-chip { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 6px; border-radius: 8px; }
.user-chip:hover { background: #f4f7fe; }
.avatar {
  width: 30px; height: 30px; border-radius: 50%;
  background: linear-gradient(135deg, #2f6bff, #5d8bff);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600;
}
.user-name { color: #44506b; font-size: 13px; }
.menu-btn { display: none; padding: 6px; }
.run-tag { margin-left: 8px; vertical-align: 1px; }
.start-wrap { display: inline-flex; flex: none; }
.start-btn { flex: none; }

/* 移动端 */
@media (max-width: 768px) {
  .pc-only { display: none; }
  .menu-btn { display: inline-flex; }
  .header-title { font-size: 14px; }
  .user-name { display: none; }
  .page-card, :deep(.page-card) { margin: 10px !important; }
}
</style>

<style>
/* 全局：菜单抽屉内背景（非 scoped 以作用到 el-drawer 内部） */
.menu-drawer .el-drawer__body { padding: 0; background: #16397f; }
.menu-drawer .aside { background: linear-gradient(180deg, #0c2456 0%, #16397f 100%); }
@media (max-width: 768px) {
  .el-message { max-width: calc(100vw - 24px) !important; }
}
</style>
