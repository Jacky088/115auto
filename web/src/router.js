import { createRouter, createWebHashHistory } from 'vue-router'
import { api } from './api'

const routes = [
  { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
  {
    path: '/', component: () => import('./components/Layout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('./views/Dashboard.vue') },
      { path: 'files', component: () => import('./views/Files.vue') },
      { path: 'channels', component: () => import('./views/Channels.vue') },
      { path: 'subscriptions', component: () => import('./views/Subscriptions.vue') },
      { path: 'dirs', component: () => import('./views/Dirs.vue') },
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

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!localStorage.getItem('token')) return '/login'
  // 首次使用 → 引导向导
  if (to.path !== '/setup') {
    try {
      const { data } = await api.get('/api/wizard/status')
      if (!data.completed) return '/setup'
    } catch { /* 网络错误放行，由页面自行提示 */ }
  }
  return true
})

export default router
