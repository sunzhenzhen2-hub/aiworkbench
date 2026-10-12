<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { ALL_DISPLAY_PHASES } from '../constants/projectMeta'
import { isAgentNativeProduct } from '../productLine'
import AgentNativeHint from '../components/AgentNativeHint.vue'

interface PersonalSummary {
  task_total: number
  task_done: number
  task_open: number
  task_overdue: number
  task_completion_rate: number
  project_count: number
  blocker_open: number
  todo_open: number
  week_report_status: 'none' | 'draft' | 'submitted' | string
  week_report_period_start: string | null
  week_report_period_end: string | null
  week_report_item_count: number
  report_submitted?: number
  report_expected?: number
  key_project_count?: number
  key_task_count?: number
  report_quality_alerts?: number
}

interface PersonalTask {
  id: number
  title: string
  project_name: string | null
  due_date: string | null
  status: string
  result_text?: string
  acceptance_status?: string
  updated_at?: string | null
}

interface DepartmentTask {
  id: number
  title: string
  due_date: string | null
  assignee?: { name?: string } | null
}

interface PersonalProject {
  id: number
  name: string
  status: string
  current_phase: string | null
  next_update_date: string | null
  task_total: number
  task_done: number
  task_overdue: number
  week_report_items: number
}

const router = useRouter()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()

const workspace = ref<{
  my_tasks: PersonalTask[]
  department_tasks: DepartmentTask[]
  can_view_department_todos: boolean
  my_blockers: any[]
  my_projects: PersonalProject[]
  summary_scope: 'self' | 'all' | string
  summary: PersonalSummary
}>({
  my_tasks: [],
  department_tasks: [],
  can_view_department_todos: false,
  my_blockers: [],
  my_projects: [],
  summary_scope: 'self',
  summary: {
    task_total: 0,
    task_done: 0,
    task_open: 0,
    task_overdue: 0,
    task_completion_rate: 0,
    project_count: 0,
    blocker_open: 0,
    todo_open: 0,
    week_report_status: 'none',
    week_report_period_start: null,
    week_report_period_end: null,
    week_report_item_count: 0,
    report_submitted: 0,
    report_expected: 0,
    key_project_count: 0,
    key_task_count: 0,
    report_quality_alerts: 0,
  },
})

const loading = ref(false)
const completingId = ref<number | null>(null)
const aiBrief = ref<{ summary: string; focus: string[]; source?: string } | null>(null)
const aiBriefStreaming = ref(false)

interface KeyTaskRow {
  id: number
  title: string
  project_id: number
  project_name: string | null
  assignee_name: string | null
  due_date: string | null
  priority: string
  status: string
}
interface QualityAlertRow {
  person: string
  kind: string
  detail: string
  reportId: number | null
}
const showKeyTasks = ref(false)
const keyTasksLoading = ref(false)
const keyTasks = ref<KeyTaskRow[]>([])
const showQuality = ref(false)
const qualityLoading = ref(false)
const qualityRows = ref<QualityAlertRow[]>([])

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

const showDepartment = computed(() => !!workspace.value.can_view_department_todos)
const isAllScope = computed(() => workspace.value.summary_scope === 'all')
const canViewReports = computed(() => userStore.hasPermission('report.view.all'))
const canWriteTask = computed(() => userStore.hasPermission('task.create.self'))
const summary = computed(() => workspace.value.summary)

const todayLabel = computed(() => {
  const d = new Date()
  return `今天是 ${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${WEEKDAYS[d.getDay()]}`
})

/** 本地日期 YYYY-MM-DD，用来和任务截止日期对上「今天」。 */
function todayIso() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

const todayTasks = computed(() => {
  const today = todayIso()
  return (workspace.value.my_tasks || []).filter((t) => t.due_date === today)
})

const departmentTasks = computed(() => workspace.value.department_tasks || [])

function isTaskOverdue(dueDate: string | null | undefined) {
  if (!dueDate) return false
  return dueDate < todayIso()
}

function assigneeName(task: DepartmentTask) {
  return task.assignee?.name || '未指定'
}

const reportStatusLabel = computed(() => {
  if (isAllScope.value) {
    const submitted = summary.value.report_submitted ?? 0
    const expected = summary.value.report_expected ?? 0
    if (expected <= 0) return '无人需交'
    if (submitted >= expected) return '已全交'
    if (submitted > 0) return '部分提交'
    return '未提交'
  }
  const s = summary.value.week_report_status
  if (s === 'submitted') return '已提交'
  if (s === 'draft') return '草稿'
  return '未写'
})

const reportStatusClass = computed(() => {
  if (isAllScope.value) {
    const submitted = summary.value.report_submitted ?? 0
    const expected = summary.value.report_expected ?? 0
    if (expected > 0 && submitted >= expected) return 'wb-tag--green'
    if (submitted > 0) return 'wb-tag--yellow'
    return 'wb-tag--gray'
  }
  const s = summary.value.week_report_status
  if (s === 'submitted') return 'wb-tag--green'
  if (s === 'draft') return 'wb-tag--yellow'
  return 'wb-tag--gray'
})

const weekPeriodLabel = computed(() => {
  const a = summary.value.week_report_period_start
  const b = summary.value.week_report_period_end
  if (!a) return ''
  return b && b !== a ? `${a} ~ ${b}` : a
})

const weekReportDetail = computed(() => {
  if (isAllScope.value) {
    return `已交 ${summary.value.report_submitted ?? 0}/${summary.value.report_expected ?? 0}`
  }
  return `${summary.value.week_report_item_count} 条任务卡`
})

/** 只展示有项目的阶段，并按系统规定的阶段顺序排。 */
const phaseColumns = computed(() => {
  const projects = workspace.value.my_projects || []
  const buckets = new Map<string, PersonalProject[]>()
  for (const project of projects) {
    const name = (project.current_phase || '').trim() || '未填阶段'
    const list = buckets.get(name) || []
    list.push(project)
    buckets.set(name, list)
  }
  const ordered = [
    ...ALL_DISPLAY_PHASES.filter((name) => buckets.has(name)),
    ...[...buckets.keys()].filter((name) => !ALL_DISPLAY_PHASES.includes(name)),
  ]
  return ordered.map((name) => ({ name, projects: buckets.get(name) || [] }))
})

const recentUpdates = computed(() => {
  return [...(workspace.value.my_tasks || [])]
    .filter((task) => !!task.updated_at)
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
    .slice(0, 6)
    .map((task) => ({
      id: task.id,
      text: `更新了任务「${task.title}」`,
      when: formatWhen(task.updated_at),
    }))
})

function formatWhen(iso?: string | null) {
  if (!iso) return ''
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return ''
  const minutes = Math.floor((Date.now() - time) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return iso.slice(0, 10)
}

function statusTag(status: string) {
  if (status === 'blocked') return { cls: 'wb-tag--red', text: '已阻塞' }
  if (status === 'at_risk') return { cls: 'wb-tag--yellow', text: '有风险' }
  return { cls: 'wb-tag--green', text: '正常' }
}

function taskPct(row: PersonalProject) {
  if (!row.task_total) return 0
  return Math.round((row.task_done / row.task_total) * 100)
}

function openCount(row: PersonalProject) {
  return Math.max((row.task_total || 0) - (row.task_done || 0), 0)
}

function planDateText(row: PersonalProject) {
  return row.next_update_date ? `下次更新 ${row.next_update_date}` : '未填'
}

function onReportCardClick() {
  if (isAllScope.value && canViewReports.value) {
    router.push('/reports')
  } else {
    router.push({ path: '/reports', query: { tab: 'write' } })
  }
}

function onProjectCardClick() {
  if (isAllScope.value) {
    router.push('/overview')
  } else {
    scrollTo('section-projects')
  }
}

function onTodayCardClick() {
  scrollTo('section-today')
}

function onOverdueCardClick() {
  if (showDepartment.value) {
    scrollTo('section-team-todos')
  } else {
    router.push('/tasks')
  }
}

function onQuickRecord(cmd: string | number) {
  if (cmd === 'write-task') {
    router.push('/write-task')
    return
  }
  if (cmd === 'report') {
    router.push({ path: '/reports', query: { tab: 'write' } })
  }
}

function onKeyProjectsClick() {
  router.push({ path: '/overview', query: { level: 'key' } })
}

async function openKeyTasks() {
  showKeyTasks.value = true
  keyTasksLoading.value = true
  keyTasks.value = []
  try {
    const { data } = await api.get('/projects/overview/key-tasks')
    keyTasks.value = data || []
  } catch {
    keyTasks.value = []
  } finally {
    keyTasksLoading.value = false
  }
}

function openKeyTaskRow(row: KeyTaskRow) {
  showKeyTasks.value = false
  router.push(`/projects/${row.project_id}`)
}

async function openQualityAlerts() {
  if (!canViewReports.value) return
  showQuality.value = true
  qualityLoading.value = true
  qualityRows.value = []
  try {
    const params: Record<string, string> = { period_type: 'week', mode: 'full' }
    if (summary.value.week_report_period_start) {
      params.period_start = summary.value.week_report_period_start
    }
    const { data } = await api.get('/progress-reports/board', { params })
    const rows: QualityAlertRow[] = []
    for (const p of data.people || []) {
      if (!p.submitted || !p.report) continue
      const name = p.user?.name || '未知'
      const rid = p.report_id ?? p.report?.id ?? null
      for (const msg of p.report.soft_warnings || []) {
        rows.push({ person: name, kind: '质量提示', detail: msg, reportId: rid })
      }
      for (const it of p.report.items || []) {
        if (it.risk_level === 'escalate') {
          rows.push({
            person: name,
            kind: '需升级',
            detail: `${it.task_title || '未命名'}：${it.risk_reason || '未写原因'}`,
            reportId: rid,
          })
        }
        if (it.stuck_status === 'blocker' || it.stuck_status === 'coordination') {
          rows.push({
            person: name,
            kind: it.stuck_status === 'blocker' ? '卡点' : '协调中',
            detail: `${it.task_title || '未命名'}：${it.stuck_detail || '未写说明'}`,
            reportId: rid,
          })
        }
      }
    }
    qualityRows.value = rows
  } catch {
    qualityRows.value = []
  } finally {
    qualityLoading.value = false
  }
}

function openAlertReport(row: QualityAlertRow) {
  showQuality.value = false
  if (row.reportId) {
    router.push({ path: '/reports', query: { tab: 'write', view: String(row.reportId) } })
  } else {
    router.push('/reports')
  }
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

type CollapseKey = 'blockers'

const COLLAPSE_STORAGE_PREFIX = 'wb:personal-collapse:'

function readCollapsed(key: CollapseKey): boolean {
  try {
    return localStorage.getItem(`${COLLAPSE_STORAGE_PREFIX}${key}`) === '1'
  } catch {
    return false
  }
}

function writeCollapsed(key: CollapseKey, value: boolean) {
  try {
    localStorage.setItem(`${COLLAPSE_STORAGE_PREFIX}${key}`, value ? '1' : '0')
  } catch {
    /* ignore quota / private mode */
  }
}

const sectionCollapsed = ref<Record<CollapseKey, boolean>>({
  blockers: readCollapsed('blockers'),
})

function isCollapsed(key: CollapseKey) {
  return !!sectionCollapsed.value[key]
}

function toggleSection(key: CollapseKey) {
  const next = !sectionCollapsed.value[key]
  sectionCollapsed.value = { ...sectionCollapsed.value, [key]: next }
  writeCollapsed(key, next)
}

const sectionCounts = computed(() => ({
  blockers: workspace.value.my_blockers?.length ?? 0,
}))

function briefStreamUrl() {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  return `${base}/api/ai/overview/brief/stream`
}

async function loadAiBrief() {
  if (agentNative) {
    aiBrief.value = null
    aiBriefStreaming.value = false
    return
  }
  aiBriefStreaming.value = true
  aiBrief.value = { summary: '', focus: [], source: undefined }
  const token = localStorage.getItem('token') || ''
  try {
    const resp = await fetch(briefStreamUrl(), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok || !resp.body) {
      aiBrief.value = null
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let currentEvent = 'message'

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n')
      buffer = parts.pop() || ''
      for (const rawLine of parts) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          currentEvent = 'message'
          continue
        }
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
          continue
        }
        if (!line.startsWith('data:')) continue
        const dataStr = line.slice(5).trim()
        let data: Record<string, unknown> = {}
        try {
          data = JSON.parse(dataStr)
        } catch {
          continue
        }
        if (currentEvent === 'start') {
          if (!aiBrief.value) aiBrief.value = { summary: '', focus: [] }
        } else if (currentEvent === 'delta') {
          const text = String(data.text || '')
          if (!aiBrief.value) aiBrief.value = { summary: '', focus: [] }
          aiBrief.value = {
            ...aiBrief.value,
            summary: (aiBrief.value.summary || '') + text,
          }
        } else if (currentEvent === 'done') {
          aiBrief.value = {
            summary: String(data.summary || aiBrief.value?.summary || ''),
            focus: Array.isArray(data.focus) ? (data.focus as string[]) : [],
            source: data.source ? String(data.source) : undefined,
          }
        }
      }
    }
    if (aiBrief.value && !aiBrief.value.summary && !aiBrief.value.focus?.length) {
      aiBrief.value = null
    }
  } catch {
    aiBrief.value = null
  } finally {
    aiBriefStreaming.value = false
  }
}

async function load(options?: { silent?: boolean }) {
  if (!options?.silent) loading.value = true
  try {
    const { data } = await api.get('/personal/workspace')
    workspace.value = {
      ...workspace.value,
      ...data,
      summary: { ...workspace.value.summary, ...(data.summary || {}) },
    }
    if (!options?.silent) loadAiBrief()
  } finally {
    loading.value = false
  }
}

/** 勾选完成沿用任务详情的规则：没写结果说明就不能直接完成。 */
async function completeTodayTask(task: PersonalTask) {
  if (completingId.value != null) return
  if ((task.acceptance_status || 'accepted') === 'pending') {
    ElMessage.warning('请先接收任务后再更新进度')
    router.push(`/tasks/${task.id}`)
    return
  }
  const resultText = (task.result_text || '').trim()
  if (!resultText) {
    ElMessage.warning('完成前请先填写结果说明')
    router.push(`/tasks/${task.id}`)
    return
  }
  completingId.value = task.id
  try {
    const { data } = await api.patch(`/tasks/${task.id}`, {
      status: 'completed',
      result_text: resultText,
    })
    let msg = '已完成'
    if (data.synced_to_week_draft) msg += '，已写入周报草稿'
    else msg += '，未写入周报草稿（本周已交则写入下周；请确认已填结果说明）'
    if (data.dingtalk_notified) msg += '，已钉钉通知交办人'
    ElMessage.success(msg)
    await load({ silent: true })
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '更新失败')
  } finally {
    completingId.value = null
  }
}

onMounted(() => {
  load()
})
</script>

<template>
  <div v-loading="loading">
    <div class="dash-head">
      <div>
        <h1 class="wb-page-title">工作台</h1>
        <p class="wb-page-desc">{{ todayLabel }}</p>
      </div>
      <el-dropdown trigger="click" @command="onQuickRecord">
        <el-button type="primary">快速记录</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="canWriteTask" command="write-task">写任务</el-dropdown-item>
            <el-dropdown-item command="report">写周报</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <AgentNativeHint v-if="agentNative" tip="「今天该看什么」请到「我的助手」里问。" />
    <div class="ai-brief" v-else-if="aiBrief || aiBriefStreaming">
      <div class="ai-brief-title">今天该看什么</div>
      <p class="ai-brief-summary">
        {{ aiBrief?.summary || (aiBriefStreaming ? '正在整理…' : '')
        }}<span v-if="aiBriefStreaming" class="ai-brief-cursor" aria-hidden="true"></span>
      </p>
      <ul v-if="aiBrief?.focus?.length" class="ai-brief-focus">
        <li v-for="(f, i) in aiBrief.focus" :key="i">{{ f }}</li>
      </ul>
    </div>

    <div class="metric-grid">
      <div
        class="wb-stat-card compact clickable-card"
        role="button"
        tabindex="0"
        @click="onProjectCardClick"
        @keydown.enter.prevent="onProjectCardClick"
      >
        <div class="wb-stat-num">{{ summary.project_count }}</div>
        <div class="wb-stat-label">{{ isAllScope ? '全部项目' : '我的项目' }}</div>
        <div class="stat-sub">{{ isAllScope ? '点击进入项目管理' : '点击查看列表' }}</div>
      </div>
      <div
        class="wb-stat-card compact clickable-card"
        role="button"
        tabindex="0"
        @click="onTodayCardClick"
        @keydown.enter.prevent="onTodayCardClick"
      >
        <div class="wb-stat-num">{{ todayTasks.length }}</div>
        <div class="wb-stat-label">今日待办</div>
        <div class="stat-sub">今天到期</div>
      </div>
      <div
        class="wb-stat-card compact clickable-card"
        role="button"
        tabindex="0"
        @click="onOverdueCardClick"
        @keydown.enter.prevent="onOverdueCardClick"
      >
        <div class="wb-stat-num" :class="{ 'stat-warn': summary.task_overdue > 0 }">
          {{ summary.task_overdue }}
        </div>
        <div class="wb-stat-label">逾期任务</div>
        <div class="stat-sub" :class="{ warn: summary.task_overdue > 0 }">
          {{ summary.task_overdue > 0 ? '需尽快处理' : '暂无逾期' }}
        </div>
      </div>
      <div
        class="wb-stat-card compact clickable-card"
        role="button"
        tabindex="0"
        @click="onReportCardClick"
        @keydown.enter.prevent="onReportCardClick"
      >
        <div class="wb-stat-num wb-stat-num--sm">
          <span :class="['wb-tag', reportStatusClass]">{{ reportStatusLabel }}</span>
        </div>
        <div class="wb-stat-label">{{ isAllScope ? '团队周报' : '本周周报' }}</div>
        <div class="stat-sub">
          <template v-if="weekPeriodLabel">{{ weekPeriodLabel }} · </template>{{ weekReportDetail }}
        </div>
      </div>
    </div>

    <div class="mid-grid">
      <section id="section-projects" class="panel">
        <div class="panel-head row-between">
          <span>项目总览</span>
          <button type="button" class="text-link" @click="router.push('/overview')">查看全部项目</button>
        </div>
        <div v-if="workspace.my_projects.length" class="project-list">
          <button
            v-for="project in workspace.my_projects"
            :key="project.id"
            type="button"
            class="project-row"
            @click="router.push(`/projects/${project.id}`)"
          >
            <div class="project-main">
              <div class="project-title">
                <span class="project-name">{{ project.name }}</span>
                <span class="wb-tag" :class="statusTag(project.status).cls">
                  {{ statusTag(project.status).text }}
                </span>
              </div>
              <div class="project-meta">
                {{ project.current_phase || '未填阶段' }} · {{ planDateText(project) }}
              </div>
              <div class="bar" aria-hidden="true">
                <span :style="{ width: `${taskPct(project)}%` }"></span>
              </div>
            </div>
            <div class="project-stats">
              <div>
                <strong>{{ openCount(project) }}</strong>
                <span>待办</span>
              </div>
              <div>
                <strong :class="{ overdue: project.task_overdue > 0 }">{{ project.task_overdue }}</strong>
                <span>逾期</span>
              </div>
              <div>
                <strong>{{ project.week_report_items || 0 }}</strong>
                <span>本周任务</span>
              </div>
            </div>
          </button>
        </div>
        <p v-else class="empty-line">暂无关联项目</p>
      </section>

      <section id="section-today" class="panel">
        <div class="panel-head">今日待办（共 {{ todayTasks.length }} 项）</div>
        <ul v-if="todayTasks.length" class="todo-list">
          <li v-for="task in todayTasks" :key="task.id" class="todo-item">
            <button
              type="button"
              class="todo-check"
              :disabled="completingId === task.id"
              :aria-label="`完成任务 ${task.title}`"
              @click="completeTodayTask(task)"
            ></button>
            <button type="button" class="todo-body" @click="router.push(`/tasks/${task.id}`)">
              <span class="todo-title">{{ task.title }}</span>
              <span class="todo-meta">
                <span v-if="task.project_name" class="wb-tag wb-tag--gray">{{ task.project_name }}</span>
                <span>今天到期</span>
              </span>
            </button>
          </li>
        </ul>
        <p v-else class="empty-line">今天没有到期的任务</p>
        <div v-if="showDepartment" id="section-team-todos" class="team-todos">
          <div class="team-todos-title">团队待办（{{ departmentTasks.length }}）</div>
          <ul v-if="departmentTasks.length" class="todo-list team-todo-list">
            <li v-for="task in departmentTasks" :key="task.id">
              <button type="button" class="todo-body team-todo" @click="router.push(`/tasks/${task.id}`)">
                <span class="todo-title">{{ task.title }}</span>
                <span class="todo-meta">
                  <span>{{ assigneeName(task) }}</span>
                  <span :class="{ overdue: isTaskOverdue(task.due_date) }">
                    {{ task.due_date || '未设截止' }}
                  </span>
                </span>
              </button>
            </li>
          </ul>
          <p v-else class="empty-line">暂无团队待办</p>
        </div>
        <button type="button" class="text-link footer-link" @click="router.push('/tasks')">
          查看全部待办
        </button>
      </section>
    </div>

    <div class="bottom-grid">
      <section class="panel clickable-card" role="button" tabindex="0" @click="onReportCardClick" @keydown.enter.prevent="onReportCardClick">
        <div class="panel-head">本周周报</div>
        <div class="report-status">
          <span :class="['wb-tag', reportStatusClass]">{{ reportStatusLabel }}</span>
        </div>
        <p class="report-period">{{ weekPeriodLabel || '本周' }}</p>
        <p class="report-detail">{{ weekReportDetail }}</p>
      </section>

      <section class="panel">
        <div class="panel-head">项目阶段</div>
        <div v-if="phaseColumns.length" class="phase-board">
          <div v-for="col in phaseColumns" :key="col.name" class="phase-col">
            <div class="phase-name">{{ col.name }}</div>
            <button
              v-for="project in col.projects"
              :key="project.id"
              type="button"
              class="phase-dot"
              @click="router.push(`/projects/${project.id}`)"
            >
              {{ project.name }}
            </button>
          </div>
        </div>
        <p v-else class="empty-line">暂无项目阶段</p>
      </section>

      <section class="panel">
        <div class="panel-head">最近更新</div>
        <ul v-if="recentUpdates.length" class="activity-list">
          <li v-for="item in recentUpdates" :key="item.id">
            <button type="button" class="activity-item" @click="router.push(`/tasks/${item.id}`)">
              <span>{{ item.text }}</span>
              <time>{{ item.when }}</time>
            </button>
          </li>
        </ul>
        <p v-else class="empty-line">暂无最近更新</p>
      </section>
    </div>

    <section v-if="isAllScope" class="panel section-block">
      <div class="panel-head">团队关注</div>
      <div class="stats-grid">
        <div
          class="wb-stat-card compact clickable-card"
          role="button"
          tabindex="0"
          @click="onKeyProjectsClick"
          @keydown.enter.prevent="onKeyProjectsClick"
        >
          <div class="wb-stat-num" :class="{ 'stat-warn': (summary.key_project_count ?? 0) > 0 }">
            {{ summary.key_project_count ?? 0 }}
          </div>
          <div class="wb-stat-label">重点项目</div>
          <div class="stat-sub">点击进入项目管理筛选</div>
        </div>
        <div
          class="wb-stat-card compact clickable-card"
          role="button"
          tabindex="0"
          @click="openKeyTasks"
          @keydown.enter.prevent="openKeyTasks"
        >
          <div class="wb-stat-num" :class="{ 'stat-warn': (summary.key_task_count ?? 0) > 0 }">
            {{ summary.key_task_count ?? 0 }}
          </div>
          <div class="wb-stat-label">重点任务</div>
          <div class="stat-sub">未完成的 P0/P1 · 点击查看</div>
        </div>
        <div
          v-if="canViewReports"
          class="wb-stat-card compact clickable-card"
          role="button"
          tabindex="0"
          @click="openQualityAlerts"
          @keydown.enter.prevent="openQualityAlerts"
        >
          <div class="wb-stat-num" :class="{ 'stat-warn': (summary.report_quality_alerts ?? 0) > 0 }">
            {{ summary.report_quality_alerts ?? 0 }}
          </div>
          <div class="wb-stat-label">质量告警</div>
          <div class="stat-sub">本周周报质量 · 点击查看</div>
        </div>
      </div>
    </section>

    <el-dialog v-model="showKeyTasks" title="重点任务" width="720px" destroy-on-close>
      <el-table
        v-loading="keyTasksLoading"
        :data="keyTasks"
        size="small"
        max-height="420"
        class="clickable"
        empty-text="暂无未完成重点任务"
        @row-click="openKeyTaskRow"
      >
        <el-table-column prop="title" label="任务" min-width="180" />
        <el-table-column prop="project_name" label="项目" min-width="140" />
        <el-table-column prop="assignee_name" label="负责人" width="100" />
        <el-table-column prop="priority" label="级别" width="70" />
        <el-table-column prop="due_date" label="截止" width="110" />
      </el-table>
      <template #footer>
        <el-button @click="showKeyTasks = false">关闭</el-button>
        <el-button type="primary" @click="router.push('/overview')">去项目管理</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showQuality" title="本周质量告警" width="720px" destroy-on-close>
      <p class="panel-hint" style="margin-top: 0">含质量提示、需升级、卡点/协调。点一行可打开周报。</p>
      <el-table
        v-loading="qualityLoading"
        :data="qualityRows"
        size="small"
        max-height="420"
        class="clickable"
        empty-text="本周暂无告警明细"
        @row-click="openAlertReport"
      >
        <el-table-column prop="person" label="人员" width="100" />
        <el-table-column prop="kind" label="类型" width="100" />
        <el-table-column prop="detail" label="说明" min-width="280" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="showQuality = false">关闭</el-button>
        <el-button type="primary" @click="router.push('/reports')">去周报管理</el-button>
      </template>
    </el-dialog>

    <div class="panel section-block">
      <div class="panel-head row-between" :class="{ 'is-collapsed': isCollapsed('blockers') }">
        <button type="button" class="collapse-toggle" @click="toggleSection('blockers')">
          <span class="collapse-arrow" aria-hidden="true">{{ isCollapsed('blockers') ? '▶' : '▼' }}</span>
          <span>与我相关 · 卡点跟进（{{ sectionCounts.blockers }}）</span>
        </button>
      </div>
      <div v-show="!isCollapsed('blockers')">
        <el-table :data="workspace.my_blockers" empty-text="暂无跟进卡点">
          <el-table-column prop="description" label="卡点" min-width="200" />
          <el-table-column prop="next_follow_up" label="下次跟进" width="110">
            <template #default="{ row }">
              <span :class="{ overdue: row.is_follow_up_overdue }">{{ row.next_follow_up }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

  </div>
</template>

<style scoped>
.dash-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.dash-head .wb-page-desc {
  margin: 0;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.mid-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.bottom-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr) minmax(0, 1fr);
  gap: 16px;
  margin-bottom: 16px;
  align-items: start;
}
@media (max-width: 960px) {
  .bottom-grid {
    grid-template-columns: 1fr;
  }
}
.section-block {
  margin-bottom: 16px;
}
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px 20px;
  box-shadow: var(--wb-shadow-soft);
}
.panel-head {
  font-size: 14px;
  font-weight: 600;
  color: var(--wb-text-primary);
  margin-bottom: 12px;
}
.panel-head.is-collapsed {
  margin-bottom: 0;
}
.row-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.text-link {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--wb-accent);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.text-link:hover {
  color: var(--wb-accent-hover);
}
.footer-link {
  margin-top: 12px;
}
.collapse-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  padding: 0;
  margin: 0;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  color: inherit;
  text-align: left;
}
.collapse-toggle:hover {
  color: var(--wb-mint-text, #2d7a58);
}
.collapse-arrow {
  display: inline-block;
  width: 1em;
  font-size: 11px;
  color: var(--wb-text-muted, #999);
}
.panel-hint {
  margin: -4px 0 12px;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
}
.empty-line {
  margin: 0;
  font-size: 13px;
  color: var(--wb-text-muted, #999);
}
.project-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow: auto;
}
.project-row {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  text-align: left;
  border: 1px solid var(--wb-border);
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.project-row:hover {
  border-color: #9ecfb8;
}
.project-main {
  flex: 1;
  min-width: 0;
}
.project-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.project-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.bar {
  margin-top: 8px;
  height: 6px;
  border-radius: 99px;
  background: #f0f0f0;
  overflow: hidden;
}
.bar > span {
  display: block;
  height: 100%;
  background: var(--wb-accent);
  border-radius: 99px;
}
.project-stats {
  display: flex;
  gap: 16px;
  flex-shrink: 0;
}
.project-stats div {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 48px;
}
.project-stats strong {
  font-size: 16px;
  font-weight: 600;
}
.project-stats span {
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.todo-list,
.activity-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--wb-border);
}
.team-todos {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--wb-border);
}
.team-todos-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.team-todo-list {
  max-height: 280px;
  overflow: auto;
}
.team-todo-list li {
  border-bottom: 1px solid var(--wb-border);
}
.team-todo {
  width: 100%;
  padding: 8px 0;
}
.todo-check {
  width: 16px;
  height: 16px;
  margin-top: 3px;
  border: 1.5px solid #c8c8c8;
  border-radius: 50%;
  background: #fff;
  cursor: pointer;
  flex-shrink: 0;
}
.todo-check:hover {
  border-color: var(--wb-accent);
}
.todo-body {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.todo-title {
  display: block;
  font-size: 14px;
}
.todo-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.report-status {
  margin-bottom: 8px;
}
.report-period,
.report-detail {
  margin: 0;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
}
.report-detail {
  margin-top: 4px;
}
.phase-board {
  display: flex;
  gap: 12px;
  max-height: 360px;
  overflow-x: auto;
  overflow-y: auto;
}
.phase-col {
  min-width: 120px;
  flex: 1;
}
.phase-name {
  font-size: 12px;
  color: var(--wb-text-muted, #999);
  margin-bottom: 8px;
}
.phase-dot {
  display: block;
  width: 100%;
  margin: 0 0 6px;
  padding: 6px 8px;
  border: none;
  border-radius: 8px;
  background: var(--wb-bg-active);
  color: #3d9970;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.activity-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 8px 0;
  border-bottom: 1px solid var(--wb-border);
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.activity-item time {
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.ai-brief {
  background: #f7faf8;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.ai-brief-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 6px;
}
.ai-brief-summary {
  margin: 0;
  font-size: 14px;
  color: var(--wb-text-primary);
  line-height: 1.5;
}
.ai-brief-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--wb-text-muted, #999);
  animation: ai-brief-blink 1s steps(1) infinite;
}
@keyframes ai-brief-blink {
  50% {
    opacity: 0;
  }
}
.ai-brief-focus {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--wb-text-muted);
  font-size: 13px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.wb-stat-card.compact {
  padding: 14px 16px;
  box-shadow: none;
}
.stat-warn {
  color: #c45656;
}
.stat-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.stat-sub.warn {
  color: #c45656;
}
.wb-stat-num--sm {
  font-size: 18px;
  display: flex;
  align-items: center;
  min-height: 34px;
}
.clickable-card {
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.clickable-card:hover,
.clickable-card:focus-visible {
  border-color: #9ecfb8;
  box-shadow: 0 0 0 2px rgba(61, 153, 112, 0.12);
  outline: none;
}
.clickable {
  cursor: pointer;
}
.overdue {
  color: #c45656;
  font-weight: 500;
}
</style>
