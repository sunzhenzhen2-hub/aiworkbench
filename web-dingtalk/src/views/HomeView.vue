<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'

type TaskRow = {
  id: number
  title: string
  status: string
  due_date?: string | null
  project_name?: string
}

type ProjectRow = {
  id: number
  name: string
  status: string
  current_phase?: string | null
  max_blocker?: { description?: string; status?: string } | null
  task_total?: number
  task_done?: number
  task_overdue?: number
}

type BoardPerson = {
  user: { id: number; name: string }
  submitted: boolean
}

type MineReport = {
  status?: string
  items?: Array<{ stuck_status?: string }>
  phase1_review?: unknown | null
}

const PREVIEW = 5
const TAG_COLORS = ['mint', 'info', 'orange', 'warn'] as const

const router = useRouter()
const auth = useAuthStore()
const error = ref('')
const loading = ref(false)
const loadedOnce = ref(false)
const searchQ = ref('')

const tasks = ref<TaskRow[]>([])
const allProjects = ref<ProjectRow[]>([])
const boardPeople = ref<BoardPerson[]>([])
const myReport = ref<MineReport | null>(null)

const canViewReports = computed(() => (auth.user?.permissions || []).includes('report.view.all'))

function localYmd(d = new Date()) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function mondayOfThisWeek() {
  const d = new Date()
  const day = d.getDay() || 7
  d.setDate(d.getDate() - day + 1)
  return localYmd(d)
}

function sundayOf(monday: string) {
  const d = new Date(`${monday}T12:00:00`)
  d.setDate(d.getDate() + 6)
  return localYmd(d)
}

function weekNumber(d = new Date()) {
  const start = new Date(d.getFullYear(), 0, 1)
  const days = Math.floor((d.getTime() - start.getTime()) / 86400000)
  return Math.ceil((days + start.getDay() + 1) / 7)
}

const today = localYmd()
const weekLabel = computed(() => {
  const now = new Date()
  const m = now.getMonth() + 1
  const d = now.getDate()
  return `${m}月${d}日 · 第${weekNumber(now)}周`
})

const greetName = computed(() => auth.user?.name || auth.user?.username || '')
const greetText = computed(() => {
  const h = new Date().getHours()
  const when = h < 12 ? '上午好' : h < 18 ? '下午好' : '晚上好'
  return greetName.value ? `${when}，${greetName.value}` : when
})
const avatarLetter = computed(() => (greetName.value || '?').slice(0, 1))

const todayTodos = computed(() =>
  tasks.value
    .filter((t) => t.status !== 'completed')
    .sort((a, b) => {
      const ad = a.due_date || '9999'
      const bd = b.due_date || '9999'
      return ad.localeCompare(bd)
    })
    .slice(0, PREVIEW),
)

const projectPreview = computed(() => allProjects.value.slice(0, 3))

const boardSubmitted = computed(() => boardPeople.value.filter((p) => p.submitted).length)
const boardExpected = computed(() => boardPeople.value.length)

const reportSubmitted = computed(() => myReport.value?.status === 'submitted')
const reportItemCount = computed(() => myReport.value?.items?.length || 0)
const reportStuckCount = computed(
  () => (myReport.value?.items || []).filter((it) => it.stuck_status && it.stuck_status !== 'none').length,
)

function progressPct(p: ProjectRow) {
  const total = p.task_total || 0
  if (!total) return 0
  return Math.round(((p.task_done || 0) / total) * 100)
}

function tagClass(i: number) {
  return TAG_COLORS[i % TAG_COLORS.length]
}

function dueText(t: TaskRow) {
  if (!t.due_date) return '未设截止'
  if (t.due_date < today) return `逾期 ${t.due_date}`
  if (t.due_date === today) return '今天到期'
  return t.due_date
}

function goSearch() {
  const q = searchQ.value.trim()
  if (!q) {
    router.push('/todo')
    return
  }
  router.push({ path: '/todo', query: { search: q } })
}

function goReportEdit() {
  router.push('/report')
}
function goReportMine() {
  router.push({ name: 'reports', query: { tab: 'mine' } })
}
function goReportBoard() {
  router.push({ name: 'reports', query: { tab: 'team' } })
}

async function toggleComplete(t: TaskRow, ev: Event) {
  ev.stopPropagation()
  if (t.status === 'completed') return
  try {
    await request(`/tasks/${t.id}`, 'PATCH', {
      status: 'completed',
      result_text: '首页快捷完成',
    })
    t.status = 'completed'
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '完成失败（可能需在详情页补结果说明/附件）')
  }
}

async function load() {
  const first = !loadedOnce.value
  if (first) loading.value = true
  error.value = ''
  try {
    const monday = mondayOfThisWeek()
    const jobs: Promise<void>[] = [
      request<{ my_tasks?: TaskRow[] }>('/personal/workspace')
        .then((data) => {
          tasks.value = data.my_tasks || []
        })
        .catch(() => {
          tasks.value = []
        }),
      request<{ items?: ProjectRow[] }>('/projects', 'GET', { page_size: 100 })
        .then((data) => {
          allProjects.value = data?.items || []
        })
        .catch(() => {
          allProjects.value = []
        }),
      request<MineReport | null>('/progress-reports/mine', 'GET', {
        period_type: 'week',
        period_start: monday,
        period_end: sundayOf(monday),
      })
        .then((data) => {
          myReport.value = data
        })
        .catch(() => {
          myReport.value = null
        }),
    ]
    if (canViewReports.value) {
      jobs.push(
        request<{ people?: BoardPerson[] }>('/progress-reports/board', 'GET', {
          period_type: 'week',
          mode: 'summary',
        })
          .then((data) => {
            boardPeople.value = data.people || []
          })
          .catch(() => {
            boardPeople.value = []
          }),
      )
    }
    await Promise.all(jobs)
    loadedOnce.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function onVisible() {
  if (document.visibilityState === 'visible' && auth.user) void load()
}

onMounted(async () => {
  document.addEventListener('visibilitychange', onVisible)
  if (auth.bindTicket && !auth.user) {
    router.replace({ name: 'bind' })
    return
  }
  if (!auth.user) {
    error.value = auth.denyMessage || '请联系管理员开通钉钉免登'
    return
  }
  await load()
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisible)
})
</script>

<template>
  <div class="page home">
    <header class="home-head">
      <div class="head-text">
        <div class="week-line">{{ weekLabel }}</div>
        <h1 class="greet">{{ greetText }}</h1>
      </div>
      <button type="button" class="avatar-btn" @click="router.push('/mine')">{{ avatarLetter }}</button>
    </header>

    <div v-if="error" class="deny">{{ error }}</div>
    <div v-if="!auth.user && !error" class="deny">
      正在识别钉钉身份…若一直卡住，请确认是在钉钉 App 内打开。
    </div>

    <template v-if="auth.user">
      <form class="search-box" @submit.prevent="goSearch">
        <span class="muted" aria-hidden="true">⌕</span>
        <input v-model="searchQ" type="search" placeholder="搜索任务、周报、项目" enterkeyhint="search" />
      </form>

      <div v-if="loading" class="muted">加载中…</div>

      <template v-else>
        <section class="week-banner card">
          <div class="banner-kicker">本周周报 · 第{{ weekNumber() }}周</div>
          <div class="banner-title">
            {{ reportSubmitted ? '本周已提交' : myReport ? '草稿进行中' : '还没开始写' }}
          </div>
          <div class="banner-meta muted">
            已写 {{ reportItemCount }} 条
            <template v-if="reportStuckCount"> · 卡点 {{ reportStuckCount }}</template>
            <template v-if="canViewReports">
              · 团队 {{ boardSubmitted }}/{{ boardExpected }}
            </template>
          </div>
          <div class="banner-actions">
            <button type="button" class="btn btn-sm btn-solid" @click="goReportEdit">
              {{ reportSubmitted ? '查看本期' : '继续编辑' }}
            </button>
            <button type="button" class="btn btn-sm btn-outline" @click="goReportMine">预览</button>
            <button
              v-if="canViewReports"
              type="button"
              class="btn btn-sm btn-ghost"
              @click="goReportBoard"
            >
              团队
            </button>
          </div>
        </section>

        <section class="sec-block">
          <div class="sec-head">
            <h2 class="sec-title">今日待办</h2>
            <button class="link" type="button" @click="router.push('/todo')">全部</button>
          </div>
          <div v-if="!todayTodos.length" class="card empty-sm">暂无未完成待办</div>
          <div
            v-for="(t, i) in todayTodos"
            :key="t.id"
            class="card todo-row tap"
            @click="router.push(`/tasks/${t.id}`)"
          >
            <button
              type="button"
              class="check"
              :class="{ on: t.status === 'completed' }"
              @click="toggleComplete(t, $event)"
            >
              ✓
            </button>
            <div class="todo-body">
              <div class="todo-title">{{ t.title }}</div>
              <div class="todo-meta">
                <span class="mini-tag" :class="tagClass(i)">{{ t.project_name || '任务' }}</span>
                <span class="muted">{{ dueText(t) }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="sec-block">
          <div class="sec-head">
            <h2 class="sec-title">项目协同</h2>
            <button class="link" type="button" @click="router.push('/projects')">全部</button>
          </div>
          <div v-if="!projectPreview.length" class="card empty-sm">暂无项目</div>
          <div
            v-for="p in projectPreview"
            :key="p.id"
            class="card tap"
            @click="router.push(`/projects/${p.id}`)"
          >
            <div class="proj-row">
              <strong>{{ p.name }}</strong>
              <span class="muted">{{ progressPct(p) }}%</span>
            </div>
            <div class="muted">
              {{ p.current_phase || '未分阶段' }}
              · 任务 {{ p.task_done ?? 0 }}/{{ p.task_total ?? 0 }}
            </div>
            <div class="progress-track">
              <div class="progress-fill" :style="{ width: progressPct(p) + '%' }" />
            </div>
          </div>
        </section>
      </template>
    </template>

    <button
      v-if="auth.user"
      type="button"
      class="fab"
      aria-label="新建任务"
      @click="router.push('/tasks/new')"
    >
      +
    </button>
  </div>
</template>

<style scoped>
.home {
  /* 底栏 reserved by .page；再留出 FAB（52）+ 间距，避免挡住末尾卡片 */
  padding-bottom: calc(var(--wb-tab-h) + var(--wb-safe-bottom) + 16px + 68px);
}
.home-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.week-line {
  font-size: 12px;
  color: var(--wb-muted);
}
.greet {
  margin: 2px 0 0;
  font-size: 22px;
  font-weight: 700;
}
.week-banner {
  background: var(--wb-warn-bg);
  border-color: #f0e0c0;
}
.banner-kicker {
  font-size: 12px;
  color: var(--wb-warn-text);
  margin-bottom: 4px;
}
.banner-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--wb-text);
  margin-bottom: 4px;
}
.banner-meta { margin-bottom: 12px; }
.banner-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.banner-actions .btn { width: auto; min-width: 88px; }
.sec-block { margin-bottom: 8px; }
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 16px 0 8px;
}
.sec-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}
.link {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 13px;
  padding: 0;
}
.todo-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.check {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--wb-border);
  background: #fff;
  color: transparent;
  flex-shrink: 0;
  margin-top: 2px;
  font-size: 12px;
  padding: 0;
}
.check.on {
  background: var(--wb-mint);
  border-color: var(--wb-mint);
  color: #fff;
}
.todo-title { font-weight: 600; margin-bottom: 4px; }
.todo-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.mini-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
}
.mini-tag.mint { background: var(--wb-mint-bg); color: var(--wb-mint-text); }
.mini-tag.info { background: var(--wb-info-bg); color: var(--wb-info); }
.mini-tag.orange { background: var(--wb-orange-bg); color: var(--wb-orange); }
.mini-tag.warn { background: var(--wb-warn-bg); color: var(--wb-warn-text); }
.proj-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.tap { cursor: pointer; }
.tap:active { opacity: 0.92; }
.empty-sm {
  text-align: center;
  color: var(--wb-muted);
  padding: 18px 8px;
}
</style>
