import { createRouter, createWebHistory } from 'vue-router'
import { isAgentNativeProduct } from '@/productLine'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: () => import('@/views/MainLayout.vue'),
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        { path: 'todo', name: 'todo', component: () => import('@/views/TodoView.vue') },
        { path: 'reviews', name: 'reviews', component: () => import('@/views/ReviewsView.vue') },
        { path: 'report', name: 'report', component: () => import('@/views/ReportView.vue') },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('@/views/ReportsHubView.vue'),
        },
        {
          path: 'my-reports',
          redirect: { name: 'reports', query: { tab: 'mine' } },
        },
        {
          path: 'report-board',
          redirect: (to) => ({
            name: 'reports',
            query: {
              tab: 'team',
              ...(typeof to.query.period === 'string' && to.query.period
                ? { period: to.query.period }
                : {}),
            },
          }),
        },
        {
          path: 'report-board/person/:userId',
          name: 'report-person',
          component: () => import('@/views/ReportPersonView.vue'),
        },
        { path: 'mine', name: 'mine', component: () => import('@/views/ProfileView.vue') },
        {
          path: 'projects',
          name: 'projects',
          component: () => import('@/views/ProjectListView.vue'),
        },
        {
          path: 'projects/:id',
          name: 'project-detail',
          component: () => import('@/views/ProjectDetailView.vue'),
        },
        {
          path: 'dsh',
          name: 'dsh',
          component: () => import('@/views/DshView.vue'),
        },
      ],
    },
    { path: '/tasks/new', name: 'task-create', component: () => import('@/views/TaskCreateView.vue') },
    { path: '/tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue') },
    { path: '/blocker/new', name: 'blocker-new', component: () => import('@/views/BlockerView.vue') },
    { path: '/bind', name: 'bind', component: () => import('@/views/BindView.vue'), meta: { public: true } },
    { path: '/password', name: 'password', component: () => import('@/views/PasswordView.vue') },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true

  // 已有 token 先拉用户；无 token 走钉钉免登
  const me = await auth.ensureLogin()

  if (to.name === 'bind') return true

  if (!me && auth.bindTicket) {
    return { name: 'bind' }
  }
  if (!me) {
    // 免登失败也允许进入首页看提示；业务页回首页
    if (to.name === 'home') return true
    return { name: 'home' }
  }
  if (to.name === 'dsh' && !(me.permissions || []).includes('dsh.access')) {
    return { name: 'home' }
  }
  if (to.name === 'home' && isAgentNativeProduct() && (me.permissions || []).includes('dsh.access')) {
    return { name: 'dsh' }
  }
  // 钉钉免登不强制改密；允许从「我的」主动进入改密页
  return true
})

export default router
