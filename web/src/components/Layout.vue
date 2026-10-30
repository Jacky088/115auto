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
          <el-menu-item index="/channels"><el-icon><Connection /></el-icon>频道管理</el-menu-item>
          <el-menu-item index="/rules"><el-icon><Sort /></el-icon>分类规则</el-menu-item>
          <el-menu-item index="/blacklist"><el-icon><CircleClose /></el-icon>黑名单</el-menu-item>
          <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
          <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
          <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
          <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
        </el-menu>
        <div class="aside-footer">Telegram → 115 自动化</div>
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
        <el-menu-item index="/channels"><el-icon><Connection /></el-icon>频道管理</el-menu-item>
        <el-menu-item index="/rules"><el-icon><Sort /></el-icon>分类规则</el-menu-item>
        <el-menu-item index="/blacklist"><el-icon><CircleClose /></el-icon>黑名单</el-menu-item>
        <el-menu-item index="/transfers"><el-icon><List /></el-icon>转存记录</el-menu-item>
        <el-menu-item index="/history"><el-icon><Tickets /></el-icon>日志查询</el-menu-item>
        <el-menu-item index="/account"><el-icon><User /></el-icon>账号管理</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
      </el-menu>
      <div class="aside-footer">Telegram → 115 自动化</div>
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
        <el-button v-if="!sysRunning && sysReady" type="primary" size="small" class="start-btn"
                   :loading="toggling" @click="startSystem">开始运行</el-button>
        <el-button v-else-if="sysRunning" type="danger" size="small" plain class="start-btn"
                   :loading="toggling" @click="stopSystem">停止</el-button>
        <el-dropdown @command="onCommand">
          <div class="user-chip">
            <div class="avatar">A</div>
            <span class="user-name">admin</span>
            <el-icon :size="12" color="#8a97b3"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="pwd">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-alert v-if="cookieExpired" type="error" :closable="false" show-icon
                title="115 Cookie 已失效，转存已暂停 —— 请前往「账号管理」重新扫码登录"
                style="margin: 0 10px" class="cookie-alert" />
      <el-main style="padding: 0; overflow-y: auto; overflow-x: hidden">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="pwdVisible" title="修改密码" width="400px" class="pc-dialog">
    <el-form label-width="80px">
      <el-form-item label="旧密码"><el-input v-model="pwdForm.old" type="password" show-password /></el-form-item>
      <el-form-item label="新密码"><el-input v-model="pwdForm.new" type="password" show-password /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="pwdVisible = false">取消</el-button>
      <el-button type="primary" @click="changePwd">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'

const route = useRoute()
const router = useRouter()
const titles = { dashboard: '仪表盘', channels: '频道管理', rules: '分类规则', blacklist: '黑名单',
                 transfers: '转存记录', history: '日志查询', account: '账号管理', settings: '系统设置', setup: '初始化向导' }
const title = computed(() => titles[route.path.slice(1)] || '')

// 移动端菜单抽屉
const menuOpen = ref(false)
const sysRunning = ref(false)
const sysReady = ref(false)
const toggling = ref(false)

const loadSys = async () => {
  try {
    const { data } = await api.get('/api/system/state')
    sysRunning.value = data.running
    sysReady.value = data.ready && data.setup_completed
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
loadSys()
setInterval(loadSys, 10000)

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
check()
setInterval(check, 60000)

const pwdVisible = ref(false)
const pwdForm = reactive({ old: '', new: '' })
const onCommand = (cmd) => {
  if (cmd === 'pwd') { pwdForm.old = ''; pwdForm.new = ''; pwdVisible.value = true }
  if (cmd === 'logout') { localStorage.removeItem('token'); router.push('/login') }
}
const changePwd = async () => {
  try {
    await api.post('/api/auth/password', pwdForm)
    saveOk('密码已修改', '下次登录请使用新密码')
    pwdVisible.value = false
  } catch (e) { ElMessage.error(errMsg(e)) }
}
</script>

<style scoped>
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
