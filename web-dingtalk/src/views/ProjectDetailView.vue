<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/utils/api'

type ProjectDetail = {
  id: number
  name: string
  status: string
  project_type?: string
  level?: string
  current_phase?: string | null
  description?: string | null
  owner?: { name: string; username?: string }
  max_blocker?: {
    description?: string
    status?: string
    next_action?: string
    party_type?: string
  } | null
  task_total?: number
  task_done?: number
  task_in_progress?: number
  task_overdue?: number
  week_escalate?: number
  week_quality?: string
  start_date?: string | null
  end_date?: string | null
}

const PROJECT_STATUS: Record<string, string> = {
  normal: '正常',
  active: '进行中',
  at_risk: '有风险',
  blocked: '已阻塞',
  completed: '已完成',
  archived: '已归档',
}
const BLOCKER_STATUS: Record<string, string> = {
  following: '跟进中',
  pending_decision: '待决策',
  escalated: '已升级',
  resolved: '已解决',
}

type PhaseRow = {
  id: number
  name: string
  status: string
  planned_end_date?: string | null
  is_overdue?: boolean
}

const PHASE_STATUS: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref('')
const project = ref<ProjectDetail | null>(null)
const phases = ref<PhaseRow[]>([])

function phaseClass(status: string) {
  if (status === 'completed') return 'is-done'
  if (status === 'in_progress') return 'is-doing'
  return 'is-todo'
}

function statusText(s?: string) {
  return PROJECT_STATUS[s || ''] || s || '-'
}

function blockerStatus(s?: string) {
  return BLOCKER_STATUS[s || ''] || s || '-'
}

async function load() {
  const id = Number(route.params.id)
  if (!id) {
    error.value = '无效项目'
    return
  }
  loading.value = true
  error.value = ''
  phases.value = []
  try {
    const [detail, phaseRows] = await Promise.all([
      request<ProjectDetail>(`/projects/${id}`),
      request<PhaseRow[]>(`/projects/${id}/phases`).catch(() => [] as PhaseRow[]),
    ])
    project.value = detail
    phases.value = phaseRows || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    project.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => route.params.id, load)
</script>

<template>
  <div class="page">
    <button class="back" type="button" @click="router.push('/projects')">← 返回项目列表</button>
    <div v-if="error" class="deny">{{ error }}</div>
    <div v-if="loading" class="muted">加载中…</div>

    <template v-if="project && !loading">
      <h1 class="page-title">{{ project.name }}</h1>
      <p class="page-desc">项目详情</p>

      <div class="card">
        <div class="row">
          <span
            class="tag"
            :class="{ 'tag-warn': project.status === 'at_risk' || project.status === 'blocked' }"
          >
            {{ statusText(project.status) }}
          </span>
          <span v-if="(project.week_escalate || 0) > 0" class="tag tag-warn" style="margin-left:6px">
            本周升级 {{ project.week_escalate }}
          </span>
        </div>
        <div class="kv"><span class="muted">负责人</span><span>{{ project.owner?.name || '-' }}</span></div>
        <div v-if="phases.length" class="phase-bar">
          <div
            v-for="p in phases"
            :key="p.id"
            class="phase-cell"
            :class="phaseClass(p.status)"
          >
            <div class="phase-name">{{ p.name }}</div>
            <div class="phase-state">{{ PHASE_STATUS[p.status] || p.status }}</div>
          </div>
        </div>
        <div v-else class="kv">
          <span class="muted">当前阶段</span><span>{{ project.current_phase || '-' }}</span>
        </div>
        <div class="kv"><span class="muted">级别</span><span>{{ project.level || '-' }}</span></div>
        <div class="kv">
          <span class="muted">任务进度</span>
          <span>{{ project.task_done ?? 0 }}/{{ project.task_total ?? 0 }}（进行中 {{ project.task_in_progress ?? 0 }}）</span>
        </div>
        <div v-if="project.task_overdue" class="kv">
          <span class="muted">逾期任务</span>
          <span class="warn-text">{{ project.task_overdue }}</span>
        </div>
        <div v-if="project.description" class="desc">{{ project.description }}</div>
      </div>

      <h2 class="sec">当前卡点</h2>
      <div
        v-if="project.max_blocker && project.max_blocker.status !== 'resolved'"
        class="card"
      >
        <div class="row">
          <span class="tag tag-warn">{{ blockerStatus(project.max_blocker.status) }}</span>
        </div>
        <div class="item-title">{{ project.max_blocker.description || '有未关闭卡点' }}</div>
        <div v-if="project.max_blocker.next_action" class="muted">
          下一步：{{ project.max_blocker.next_action }}
        </div>
      </div>
      <div v-else class="card empty-sm">暂无未关闭卡点</div>

      <div class="card">
        <button
          class="btn btn-mint"
          style="margin-bottom:10px"
          @click="router.push({ path: '/blocker/new', query: { project_id: String(project.id) } })"
        >
          登记卡点
        </button>
        <button class="btn btn-ghost" style="margin-bottom:10px" @click="router.push('/todo')">
          去待办
        </button>
        <button class="btn btn-ghost" @click="router.push('/')">回首页</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.back {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0;
  margin-bottom: 8px;
  cursor: pointer;
}
.row {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
}
.kv {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--wb-border);
  font-size: 14px;
}
.kv:last-of-type {
  border-bottom: none;
}
.desc {
  margin-top: 10px;
  font-size: 14px;
  color: var(--wb-label);
  white-space: pre-wrap;
}
.sec {
  font-size: 15px;
  margin: 16px 0 8px;
}
.item-title {
  font-weight: 500;
  margin-bottom: 4px;
}
.warn-text {
  color: var(--wb-danger);
}
.empty-sm {
  text-align: center;
  color: var(--wb-muted);
  padding: 18px 8px;
}
.phase-bar {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  margin: 4px 0 12px;
  padding-bottom: 2px;
}
.phase-cell {
  flex: 1 0 88px;
  min-width: 88px;
  border-radius: 10px;
  padding: 10px 8px;
}
.phase-cell.is-done {
  background: var(--wb-mint-bg);
  color: var(--wb-mint-text);
}
.phase-cell.is-doing {
  background: var(--wb-mint);
  color: #fff;
}
.phase-cell.is-todo {
  background: var(--wb-surface);
  color: var(--wb-muted);
}
.phase-name {
  font-weight: 600;
  font-size: 13px;
}
.phase-state {
  margin-top: 4px;
  font-size: 12px;
}
</style>
