<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter, useRoute, RouterView } from 'vue-router'
import { isAgentNativeProduct } from '../productLine'
import { useUserStore } from '../stores/user'

const agentNative = isAgentNativeProduct()

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

onMounted(async () => {
  if (!userStore.user) await userStore.fetchMe()
})

const onDsh = computed(() => route.path === '/dsh')
const compactRail = computed(() => agentNative && onDsh.value)

async function logout() {
  await userStore.logout()
  router.push('/login')
}

function onUserMenuCommand(cmd: string | number) {
  if (cmd === 'password') {
    router.push('/change-password')
    return
  }
  if (cmd === 'logout') {
    logout()
  }
}

function navClass(path: string) {
  const p = route.path
  const key = path.replace(/^\//, '')
  return p.includes(key) ? 'nav-item active' : 'nav-item'
}

const taskSectionActive = computed(() => {
  const p = route.path
  return p === '/tasks' || p.startsWith('/tasks/') || p.startsWith('/write-task')
})

const reportSectionActive = computed(() => {
  const p = route.path
  return (
    p === '/reports' ||
    p.startsWith('/reports/') ||
    p.startsWith('/progress-report') ||
    p.startsWith('/report-board')
  )
})
</script>

<template>
  <div class="shell" :class="{ 'shell--native': agentNative, 'shell--dsh': compactRail }">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-icon" aria-hidden="true">
          <svg
            v-if="agentNative"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
          >
            <path d="M4 12a8 8 0 0 1 14.3-5.1L21 5v6h-6l2.2-2.2A6 6 0 1 0 18 13" />
            <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
          </svg>
          <svg
            v-else
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
          >
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
        </span>
        <span class="brand-text">{{ agentNative ? '我的助手' : '项目支撑工作台' }}</span>
      </div>

      <nav class="nav">
        <router-link
          v-if="agentNative && userStore.hasPermission('dsh.access')"
          to="/dsh"
          :class="navClass('/dsh')"
        >
          <el-icon><Monitor /></el-icon>
          <span>助手</span>
        </router-link>

        <p v-if="agentNative" class="nav-group">账本</p>

        <router-link to="/personal" :class="navClass('/personal')">
          <el-icon><User /></el-icon>
          <span>{{ agentNative ? '待办' : '个人工作台' }}</span>
        </router-link>
        <router-link to="/overview" :class="navClass('/overview')">
          <el-icon><DataBoard /></el-icon>
          <span>{{ agentNative ? '项目' : '项目管理' }}</span>
        </router-link>
        <router-link to="/tasks" :class="taskSectionActive ? 'nav-item active' : 'nav-item'">
          <el-icon><List /></el-icon>
          <span>{{ agentNative ? '任务' : '任务管理' }}</span>
        </router-link>
        <router-link to="/reports" :class="reportSectionActive ? 'nav-item active' : 'nav-item'">
          <el-icon><EditPen /></el-icon>
          <span>{{ agentNative ? '周报' : '周报管理' }}</span>
        </router-link>
        <router-link to="/my-reviews" :class="navClass('/my-reviews')">
          <el-icon><ChatDotRound /></el-icon>
          <span>{{ agentNative ? '评价' : '看评价' }}</span>
        </router-link>
        <router-link
          v-if="userStore.hasPermission('assist.analyze') || userStore.hasPermission('stats.view')"
          to="/analyze"
          :class="navClass('/analyze')"
        >
          <el-icon><DataAnalysis /></el-icon>
          <span>{{ agentNative ? '分析' : '分析管控' }}</span>
        </router-link>
        <router-link
          v-if="userStore.hasPermission('solution.view')"
          to="/solutions"
          :class="navClass('/solutions')"
        >
          <el-icon><FolderOpened /></el-icon>
          <span>{{ agentNative ? '案例' : '方案库' }}</span>
        </router-link>
        <router-link
          v-if="!agentNative && userStore.hasPermission('dsh.access')"
          to="/dsh"
          :class="navClass('/dsh')"
        >
          <el-icon><Monitor /></el-icon>
          <span>智能助手</span>
        </router-link>
        <router-link
          v-if="
            userStore.hasPermission('admin.user') ||
            userStore.hasPermission('admin.role') ||
            userStore.hasPermission('admin.model')
          "
          to="/admin"
          :class="navClass('/admin')"
        >
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </router-link>
      </nav>
    </aside>

    <div class="main-wrap">
      <header class="topbar">
        <div class="topbar-spacer" />
        <el-dropdown trigger="click" @command="onUserMenuCommand">
          <button type="button" class="user-trigger">
            <span class="user-name">{{ userStore.user?.name || '用户' }}</span>
            <el-icon class="user-caret"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出</el-dropdown-item>
              <el-dropdown-item disabled>更多功能即将开放</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>
      <main class="content" :class="{ 'content-flush': route.path === '/dsh' }">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  background: var(--wb-bg-page);
}

.sidebar {
  width: 240px;
  flex-shrink: 0;
  background: var(--wb-bg-sidebar);
  border-right: 1px solid var(--wb-border);
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  transition: width 0.18s ease;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px 20px;
}

.brand-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--wb-bg-active);
  color: var(--wb-accent);
  flex-shrink: 0;
}

.brand-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--wb-text-primary);
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-group {
  margin: 12px 14px 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--wb-text-muted);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--wb-radius-pill);
  color: var(--wb-text-secondary);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: rgba(0, 0, 0, 0.04);
  color: var(--wb-text-primary);
}

.nav-item.active {
  background: var(--wb-bg-active);
  color: #2d7a55;
}

.main-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  padding: 0 28px;
  background: var(--wb-bg-page);
  border-bottom: 1px solid var(--wb-border);
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--wb-font);
  font-size: 13px;
  color: var(--wb-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: var(--wb-radius-pill);
}

.user-trigger:hover {
  background: #f5f5f5;
  color: var(--wb-text-primary);
}

.user-name {
  font-size: 13px;
}

.user-caret {
  font-size: 12px;
}

.content {
  flex: 1;
  padding: 28px 32px 40px;
  background: var(--wb-bg-page);
  overflow: auto;
}
.content-flush {
  padding: 0;
  overflow: hidden;
}

.shell--native .sidebar {
  background: #151922;
  border-right-color: #222838;
}
.shell--native .brand-text,
.shell--native .nav-item {
  color: #c5cdd8;
}
.shell--native .nav-group {
  color: #6d7788;
}
.shell--native .nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #eef2f6;
}
.shell--native .nav-item.active {
  background: rgba(61, 179, 112, 0.18);
  color: #7ee0a8;
}
.shell--native .brand-icon {
  background: rgba(61, 179, 112, 0.16);
  color: #7ee0a8;
}

.shell--dsh {
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
}
.shell--dsh .main-wrap {
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.shell--dsh .sidebar {
  width: 88px;
  padding: 12px 8px;
}
.shell--dsh .brand {
  justify-content: center;
  padding: 4px 0 12px;
}
.shell--dsh .brand-text {
  display: none;
}
.shell--dsh .nav-group {
  text-align: center;
  margin: 10px 0 2px;
  font-size: 10px;
}
.shell--dsh .nav-item {
  flex-direction: column;
  gap: 4px;
  padding: 8px 4px;
  font-size: 11px;
  border-radius: 10px;
}
.shell--dsh .topbar {
  height: 40px;
  padding: 0 16px;
  background: #111;
  border-bottom-color: #222;
}
.shell--dsh .user-trigger {
  color: #c5cdd8;
}
.shell--dsh .user-trigger:hover {
  background: #1c1c1c;
  color: #fff;
}
.shell--dsh .content-flush {
  position: relative;
  background: #111;
  flex: 1;
  min-height: 0;
  height: auto;
  overflow: hidden;
}
</style>
