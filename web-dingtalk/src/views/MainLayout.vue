<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { isAgentNativeProduct } from '@/productLine'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()
const agentNative = isAgentNativeProduct()

const showTab = computed(() => route.name !== 'report')
const onDsh = computed(() => route.name === 'dsh')

const canDsh = computed(() => (auth.user?.permissions || []).includes('dsh.access'))

const active = computed(() => {
  if (route.name === 'todo') return 'todo'
  if (
    route.name === 'report' ||
    route.name === 'reports' ||
    route.name === 'report-person'
  ) {
    return 'report'
  }
  if (route.name === 'projects' || route.name === 'project-detail') return 'projects'
  if (route.name === 'dsh') return 'dsh'
  // 「我的」已移出底栏：头像进入时不误高亮首页
  if (route.name === 'mine' || route.name === 'reviews' || route.name === 'password') {
    return ''
  }
  return 'home'
})
</script>

<template>
  <div :class="{ 'layout-dsh': agentNative && onDsh }">
    <RouterView />
    <nav v-if="showTab" class="tab-bar" :class="{ 'tab-bar--compact': agentNative && onDsh }">
      <RouterLink
        v-if="canDsh && agentNative"
        class="tab-item"
        :class="{ active: active === 'dsh' }"
        to="/dsh"
      >
        <span class="tab-ico" aria-hidden="true">✦</span>
        助手
      </RouterLink>
      <RouterLink class="tab-item" :class="{ active: active === 'home' }" to="/">
        <span class="tab-ico" aria-hidden="true">⌂</span>
        {{ agentNative ? '看板' : '首页' }}
      </RouterLink>
      <RouterLink class="tab-item" :class="{ active: active === 'todo' }" to="/todo">
        <span class="tab-ico" aria-hidden="true">☑</span>
        任务
      </RouterLink>
      <RouterLink class="tab-item" :class="{ active: active === 'report' }" to="/reports">
        <span class="tab-ico" aria-hidden="true">☰</span>
        周报
      </RouterLink>
      <RouterLink class="tab-item" :class="{ active: active === 'projects' }" to="/projects">
        <span class="tab-ico" aria-hidden="true">▦</span>
        项目
      </RouterLink>
      <RouterLink
        v-if="canDsh && !agentNative"
        class="tab-item"
        :class="{ active: active === 'dsh' }"
        to="/dsh"
      >
        <span class="tab-ico" aria-hidden="true">✦</span>
        助手
      </RouterLink>
    </nav>
  </div>
</template>
