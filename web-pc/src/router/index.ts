import { createRouter, createWebHistory } from 'vue-router'
import { homePathAfterLogin } from '../productLine'
import { useUserStore } from '../stores/user'

function defaultHomePath(hasDsh: boolean): string {
  return homePathAfterLogin(hasDsh)
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', component: () => import('../views/LoginView.vue') },
    {
      path: '/change-password',
      component: () => import('../views/ChangePasswordView.vue'),
      meta: { requiresAuth: true, allowPasswordChange: true },
    },
    {
      path: '/',
      component: () => import('../layouts/MainLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: () => defaultHomePath(true),
        },
        { path: 'overview', component: () => import('../views/OverviewView.vue') },
        {
          path: 'stats',
          redirect: () => ({ path: '/analyze', query: { tab: 'stats' } }),
        },
        { path: 'projects/:id', component: () => import('../views/ProjectDetailView.vue') },
        { path: 'personal', component: () => import('../views/PersonalView.vue') },
        { path: 'tasks', component: () => import('../views/TaskManageView.vue') },
        {
          path: 'write-task',
          redirect: (to) => ({
            path: '/tasks',
            query: { ...to.query, tab: 'write' },
          }),
        },
        { path: 'tasks/:id', component: () => import('../views/TaskDetailView.vue') },
        { path: 'my-reviews', component: () => import('../views/MyReviewsView.vue') },
        { path: 'reports', component: () => import('../views/ReportManageView.vue') },
        {
          path: 'reports/person/:userId',
          component: () => import('../views/ReportPersonView.vue'),
        },
        // 旧路径兼容
        {
          path: 'progress-report',
          redirect: (to) => ({
            path: '/reports',
            query: { ...to.query, tab: 'write' },
          }),
        },
        {
          path: 'report-board',
          redirect: (to) => ({
            path: '/reports',
            query: { ...to.query },
          }),
        },
        {
          path: 'report-board/person/:userId',
          redirect: (to) => ({
            path: `/reports/person/${to.params.userId}`,
            query: { ...to.query },
          }),
        },
        { path: 'solutions', component: () => import('../views/SolutionLibraryView.vue') },
        { path: 'dsh', component: () => import('../views/DshView.vue') },
        {
          path: 'analyze',
          component: () => import('../views/AnalyzeControlView.vue'),
          meta: { requiresAnalyzeAccess: true },
        },
        { path: 'admin', component: () => import('../views/AdminView.vue') },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const token = localStorage.getItem('token')

  // Vue Router 已设置 base=/workbench/，这里只能返回路由 path（如 /login），
  // 不能再拼 base，否则会变成 /workbench/workbench/login 白屏。
  if (to.meta.requiresAuth && !token) {
    return '/login'
  }

  if (!token) return true

  const userStore = useUserStore()
  if (!userStore.user) {
    try {
      await userStore.fetchMe()
    } catch {
      userStore.logout()
      return '/login'
    }
  }

  const mustChange = userStore.user?.must_change_password


  if (mustChange && !to.meta.allowPasswordChange) {
    return '/change-password'
  }

  if (to.path === '/dsh' && !userStore.hasPermission('dsh.access')) {
    return '/personal'
  }
  if (
    (to.path === '/analyze' || to.meta.requiresAnalyzeAccess) &&
    !userStore.hasPermission('assist.analyze') &&
    !userStore.hasPermission('stats.view')
  ) {
    return '/personal'
  }
  if (to.path === '/login') {
    if (mustChange) return '/change-password'
    return defaultHomePath(userStore.hasPermission('dsh.access'))
  }
  if (to.path === '/' || to.path === '') {
    return defaultHomePath(userStore.hasPermission('dsh.access'))
  }
})

export default router
