<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { useUserStore } from '../stores/user'
import ProgressReportView from './ProgressReportView.vue'
import ReportBoardView from './ReportBoardView.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const canViewAll = computed(() => userStore.hasPermission('report.view.all'))
const canWrite = computed(() => userStore.hasPermission('report.submit'))

/** 写 Tab 随 view/周期参数变化时重新挂载，避免懒加载残留旧内容 */
const writePaneKey = computed(() => {
  const view = typeof route.query.view === 'string' ? route.query.view : ''
  const pt = typeof route.query.period_type === 'string' ? route.query.period_type : ''
  const ps = typeof route.query.period_start === 'string' ? route.query.period_start : ''
  return `${view}|${pt}|${ps}`
})

type ReportTab = 'view' | 'write'

const activeTab = computed<ReportTab>(() => {
  if (canWrite.value && route.query.tab === 'write') return 'write'
  return 'view'
})

function onTabChange(name: string | number) {
  const tab = String(name) as ReportTab
  if (tab === 'write' && canWrite.value) {
    // 保留 view 等查询参数，便于从看板点开只读周报
    const nextQuery: Record<string, string> = { tab: 'write' }
    for (const key of [
      'view',
      'period_type',
      'period_start',
      'period_end',
      'selectProjectId',
      'itemIndex',
      'edit',
      'prefill',
      'paste',
    ]) {
      const v = route.query[key]
      if (typeof v === 'string' && v) nextQuery[key] = v
    }
    router.replace({ path: '/reports', query: nextQuery })
    return
  }
  const nextQuery: Record<string, string> = {}
  for (const key of ['period', 'period_type']) {
    const v = route.query[key]
    if (typeof v === 'string' && v) nextQuery[key] = v
  }
  router.replace({ path: '/reports', query: nextQuery })
}

/** 无写权限却带了 tab=write 时，清掉 query */
watch(
  () => [canWrite.value, route.query.tab] as const,
  ([can, tab]) => {
    if (!can && tab === 'write') {
      router.replace({ path: '/reports' })
    }
  },
  { immediate: true },
)

/** 普通成员「看」Tab：本人历史周期 */
const minePeriodType = ref<'week' | 'day'>('week')
const minePeriods = ref<
  Array<{ period_start: string; period_end?: string; status: string; item_count: number }>
>([])
const mineLoading = ref(false)

async function loadMinePeriods() {
  if (canViewAll.value) return
  mineLoading.value = true
  try {
    const { data } = await api.get('/progress-reports/my-periods', {
      params: { period_type: minePeriodType.value },
    })
    minePeriods.value = data || []
  } catch {
    minePeriods.value = []
  } finally {
    mineLoading.value = false
  }
}

function openMinePeriod(p: { period_start: string; period_end?: string }) {
  router.replace({
    path: '/reports',
    query: {
      tab: 'write',
      period_type: minePeriodType.value,
      period_start: p.period_start,
      period_end: p.period_end || p.period_start,
    },
  })
}

function goWriteNew() {
  router.replace({ path: '/reports', query: { tab: 'write' } })
}

watch(minePeriodType, () => {
  if (!canViewAll.value) loadMinePeriods()
})

onMounted(() => {
  if (!canViewAll.value) loadMinePeriods()
})
</script>

<template>
  <div>
    <h1 class="wb-page-title">周报管理</h1>
    <p class="wb-page-desc">
      <template v-if="activeTab === 'write'">
        一次写一条任务：新建进入单页，保存本条后回到列表，再写下一条。
      </template>
      <template v-else-if="canViewAll">
        可查看全部周报/日报提交情况；点开某人看详情。他人周报仅可查看，不可修改。
      </template>
      <template v-else>
        这里只显示你自己的周报/日报。点某一期可进入「写」Tab 查看或修改。
      </template>
    </p>

    <el-tabs :model-value="activeTab" class="report-tabs" @tab-change="onTabChange">
      <el-tab-pane label="看周报/日报" name="view">
        <ReportBoardView v-if="canViewAll" embedded />
        <div v-else v-loading="mineLoading" class="panel mine-panel">
          <div class="mine-toolbar">
            <el-radio-group v-model="minePeriodType" size="small">
              <el-radio-button value="week">周报</el-radio-button>
              <el-radio-button value="day">日报</el-radio-button>
            </el-radio-group>
            <el-button type="primary" size="small" @click="goWriteNew">去写本期</el-button>
          </div>
          <div v-if="minePeriods.length" class="period-chips">
            <el-tag
              v-for="p in minePeriods"
              :key="p.period_start + (p.period_end || '')"
              class="chip"
              effect="plain"
              type="info"
              style="cursor: pointer; margin: 0 6px 6px 0"
              @click="openMinePeriod(p)"
            >
              {{ p.period_start
              }}{{ p.period_end && p.period_end !== p.period_start ? ' ~ ' + p.period_end : '' }}
              · {{ p.status === 'submitted' ? '已交' : '草稿' }}
              · {{ p.item_count }}条
            </el-tag>
          </div>
          <p v-else class="hint">
            还没有{{ minePeriodType === 'day' ? '日报' : '周报' }}记录，点「去写本期」开始填写。
          </p>
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="canWrite" label="写周报/日报" name="write" lazy>
        <ProgressReportView
          :key="writePaneKey"
          embedded
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.report-tabs {
  margin-top: 4px;
}

.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px;
  box-shadow: var(--wb-shadow-soft);
}

.mine-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.period-chips {
  display: flex;
  flex-wrap: wrap;
}

.hint {
  color: var(--wb-text-muted);
  font-size: 13px;
  margin: 8px 0 0;
}
</style>
