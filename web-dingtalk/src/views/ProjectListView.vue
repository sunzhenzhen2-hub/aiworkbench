<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/utils/api'

type ProjectRow = {
  id: number
  name: string
  status: string
  current_phase?: string | null
  owner?: { name: string }
  max_blocker?: { description?: string; status?: string } | null
  week_escalate?: number
  task_total?: number
  task_done?: number
  task_overdue?: number
}

const PROJECT_STATUS: Record<string, string> = {
  normal: '正常',
  active: '进行中',
  at_risk: '有风险',
  blocked: '已阻塞',
  completed: '已完成',
  archived: '已归档',
}

const ICON_COLORS = ['#5bb98c', '#3b82f6', '#e67e22', '#a65d00', '#c45656']

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref('')
const projects = ref<ProjectRow[]>([])

const filterBlocker = computed(() => route.query.filter === 'blocker')

const shown = computed(() => {
  if (!filterBlocker.value) return projects.value
  return projects.value.filter(
    (p) =>
      p.status === 'at_risk' ||
      p.status === 'blocked' ||
      (p.max_blocker && p.max_blocker.status !== 'resolved') ||
      (p.week_escalate || 0) > 0,
  )
})

const activityRows = computed(() =>
  shown.value
    .filter(
      (p) =>
        (p.max_blocker && p.max_blocker.status !== 'resolved') ||
        (p.task_overdue || 0) > 0 ||
        (p.week_escalate || 0) > 0,
    )
    .slice(0, 5)
    .map((p) => {
      if (p.max_blocker && p.max_blocker.status !== 'resolved') {
        return { id: p.id, text: `${p.name}：${p.max_blocker.description || '有未关闭卡点'}` }
      }
      if ((p.week_escalate || 0) > 0) {
        return { id: p.id, text: `${p.name}：本周升级 ${p.week_escalate}` }
      }
      return { id: p.id, text: `${p.name}：逾期任务 ${p.task_overdue}` }
    }),
)

function statusText(s?: string) {
  return PROJECT_STATUS[s || ''] || s || '-'
}

function progressPct(p: ProjectRow) {
  const total = p.task_total || 0
  if (!total) return 0
  return Math.round(((p.task_done || 0) / total) * 100)
}

function iconColor(i: number) {
  return ICON_COLORS[i % ICON_COLORS.length]
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await request<{ items?: ProjectRow[] }>('/projects', 'GET', { page_size: 100 })
    projects.value = data?.items || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    projects.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => route.query.filter,
  () => {
    /* filter via computed */
  },
)
</script>

<template>
  <div class="page">
    <div class="list-head">
      <h1 class="page-title">{{ filterBlocker ? '卡点与风险' : '项目协同' }}</h1>
      <button
        type="button"
        class="head-plus"
        aria-label="记任务"
        @click="router.push('/tasks/new')"
      >
        +
      </button>
    </div>
    <p class="page-desc">
      {{ filterBlocker ? '仅显示有卡点或风险信号的项目' : '进度、阶段与风险一览，点进详情' }}
    </p>
    <div v-if="error" class="deny">{{ error }}</div>
    <div v-if="loading" class="muted">加载中…</div>

    <template v-else>
      <div v-if="!shown.length" class="card empty-sm">暂无项目</div>
      <div
        v-for="(p, i) in shown"
        :key="p.id"
        class="card proj-card tap"
        @click="router.push(`/projects/${p.id}`)"
      >
        <div class="proj-top">
          <div class="proj-ico" :style="{ background: iconColor(i) }">
            {{ (p.name || '?').slice(0, 1) }}
          </div>
          <div class="proj-main">
            <div class="proj-name">{{ p.name }}</div>
            <div class="muted">
              {{ p.current_phase || '未分阶段' }}
              <template v-if="p.owner?.name"> · {{ p.owner.name }}</template>
            </div>
          </div>
          <span
            class="tag"
            :class="{ 'tag-warn': p.status === 'at_risk' || p.status === 'blocked' }"
          >
            {{ statusText(p.status) }}
          </span>
        </div>
        <div class="proj-meta muted">
          任务 {{ p.task_done ?? 0 }}/{{ p.task_total ?? 0 }}
          <template v-if="(p.task_overdue || 0) > 0"> · 逾期 {{ p.task_overdue }}</template>
          <span style="float:right">{{ progressPct(p) }}%</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: progressPct(p) + '%' }" />
        </div>
        <div v-if="p.max_blocker && p.max_blocker.status !== 'resolved'" class="blocker-hint">
          卡点：{{ p.max_blocker.description || '有未关闭卡点' }}
        </div>
      </div>

      <section v-if="activityRows.length" class="sec-block">
        <h2 class="sec-title">最近动态</h2>
        <div
          v-for="a in activityRows"
          :key="a.id + a.text"
          class="card activity tap"
          @click="router.push(`/projects/${a.id}`)"
        >
          <div class="act-dot" />
          <div>{{ a.text }}</div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.list-head .page-title { margin: 0; }
.head-plus {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: var(--wb-surface);
  font-size: 22px;
  line-height: 1;
}
.proj-top {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 10px;
}
.proj-ico {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  flex-shrink: 0;
}
.proj-main { flex: 1; min-width: 0; }
.proj-name {
  font-weight: 700;
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proj-meta { margin-bottom: 4px; font-size: 12px; }
.tap { cursor: pointer; }
.empty-sm {
  text-align: center;
  color: var(--wb-muted);
  padding: 24px 8px;
}
.blocker-hint {
  margin-top: 8px;
  font-size: 13px;
  color: var(--wb-danger);
}
.sec-block { margin-top: 16px; }
.sec-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 8px;
}
.activity {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 13px;
}
.act-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--wb-mint);
  margin-top: 6px;
  flex-shrink: 0;
}
</style>
