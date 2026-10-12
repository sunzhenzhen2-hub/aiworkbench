<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import ReportBoardView from './ReportBoardView.vue'
import MyReportsView from './MyReportsView.vue'

type HubTab = 'team' | 'mine'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const canViewAll = computed(() => (auth.user?.permissions || []).includes('report.view.all'))

const activeTab = computed<HubTab>(() => {
  if (!canViewAll.value) return 'mine'
  if (route.query.tab === 'mine') return 'mine'
  return 'team'
})

function setTab(tab: HubTab) {
  if (tab === 'team' && !canViewAll.value) return
  const nextQuery: Record<string, string> = { tab }
  if (tab === 'team' && typeof route.query.period === 'string' && route.query.period) {
    nextQuery.period = route.query.period
  }
  router.replace({ name: 'reports', query: nextQuery })
}

watch(
  () => [canViewAll.value, route.query.tab] as const,
  ([can, tab]) => {
    if (!can && tab === 'team') {
      router.replace({ name: 'reports', query: { tab: 'mine' } })
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="page hub">
    <div class="hub-head">
      <div class="hub-title-row">
        <h1 class="page-title">{{ canViewAll ? '周报' : '周报管理' }}</h1>
        <button
          v-if="!canViewAll || activeTab === 'mine'"
          type="button"
          class="head-plus"
          aria-label="写周报"
          @click="$router.push('/report')"
        >
          +
        </button>
      </div>
      <p class="page-desc">
        <template v-if="canViewAll && activeTab === 'team'">
          查看团队提交情况；点人员卡片看详情。写自己的周报请切到「我的周报」。
        </template>
        <template v-else>
          查看历史周报与评价；用筛选快速找到草稿、已交或已通过。
        </template>
      </p>

      <div v-if="canViewAll" class="hub-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          class="hub-tab"
          :class="{ on: activeTab === 'team' }"
          :aria-selected="activeTab === 'team'"
          @click="setTab('team')"
        >
          团队周报
        </button>
        <button
          type="button"
          role="tab"
          class="hub-tab"
          :class="{ on: activeTab === 'mine' }"
          :aria-selected="activeTab === 'mine'"
          @click="setTab('mine')"
        >
          我的周报
        </button>
      </div>
    </div>

    <ReportBoardView v-if="canViewAll && activeTab === 'team'" embedded />
    <MyReportsView v-else embedded />
  </div>
</template>

<style scoped>
.hub-head {
  position: sticky;
  top: 0;
  z-index: 30;
  background: var(--wb-bg);
  margin: -16px -16px 12px;
  padding: 16px 16px 10px;
}
.hub-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.hub-title-row .page-title { margin: 0; }
.head-plus {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: var(--wb-surface);
  font-size: 22px;
  line-height: 1;
  color: var(--wb-text);
}
.hub-head .page-desc {
  margin-bottom: 10px;
}
.hub-tabs {
  display: flex;
  gap: 8px;
  margin: 0;
  padding: 4px;
  background: var(--wb-surface);
  border-radius: 12px;
}
.hub-tab {
  flex: 1;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  color: var(--wb-muted);
}
.hub-tab.on {
  background: var(--wb-nav-active);
  color: #fff;
  font-weight: 600;
}
.hub :deep(.page) {
  padding: 0;
  min-height: 0;
}
.hub :deep(.list-head),
.hub :deep(.toolbar) {
  display: none;
}
</style>
