import { createRouter, createWebHashHistory } from 'vue-router'
import { api, isLoggedInFlag } from './api'

const routes = [
  { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
  {
    path: '/', component: () => import('./components/Layout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('./views/Dashboard.vue') },
      { path: 'files', component: () => import('./views/Files.vue') },
      { path: 'channels', redirect: '/account' },
      { path: 'subscriptions', component: () => import('./views/Subscriptions.vue') },
      { path: 'dirs', component: () => import('./views/Dirs.vue') },
      { path: 'media', component: () => import('./views/OrganizeScrape.vue') },
      { path: 'match', component: () => import('./views/MatchRules.vue') },
      { path: 'rules', redirect: '/dirs' },       // 旧地址兼容
      { path: 'blacklist', redirect: '/match' },  // 旧地址兼容
      { path: 'transfers', component: () => import('./views/Transfers.vue') },
      { path: 'history', component: () => import('./views/HistoryLogs.vue') },
      { path: 'account', component: () => import('./views/Account.vue') },
      { path: 'settings', component: () => import('./views/Settings.vue') },
      { path: 'setup', component: () => import('./views/Setup.vue') },
    ],
  },
]

const router = createRouter({ history: createWebHashHistory(), routes })

// 向导完成状态缓存：完成后不再每次切页都等 /api/wizard/status 返回才渲染
// （每次导航同步等接口是「切换菜单偶发卡顿」的原因之一）
let wizardDone = null  // null=未检查
export function resetWizardCache() { wizardDone = null }

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  // 登录态凭证在 HttpOnly Cookie 中（JS 不可读）：本地只存非敏感的「已登录」标记用于快速拦截；
  // 标记存在但 Cookie 已过期时，由 api.js 的 401 拦截器兜底跳转登录页
  if (!isLoggedInFlag()) return '/login'
  // 首次使用 → 引导向导（未完成时每次导航都复查，完成后走缓存不再请求）
  if (to.path !== '/setup' && wizardDone !== true) {
    try {
      const { data } = await api.get('/api/wizard/status')
      wizardDone = !!data.completed
      if (!wizardDone) return '/setup'
    } catch { /* 网络错误放行，由页面自行提示 */ }
  }
  return true
})

export default router
