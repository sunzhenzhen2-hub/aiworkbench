<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { PROJECT_TYPE_MAP, PROJECT_LEVEL_MAP } from '../constants/projectMeta'
import { pickManagerOptions, pickSupportOptions, defaultRolesForCreator, withSelectedOptions } from '../utils/projectRoles'
import { isAgentNativeProduct } from '../productLine'
import AgentNativeHint from '../components/AgentNativeHint.vue'

interface Stats {
  total_projects: number
  key_count: number
  daily_count: number
  red_count?: number
  yellow_count?: number
  pending_blockers: number
  task_total: number
  task_done: number
  task_completion_rate: number
  report_period_start: string | null
  report_period_end: string | null
  report_auto_switched: boolean
  report_expected: number
  report_submitted: number
  report_submit_rate: number
  report_quality_alerts: number
  unassigned_report_items: number
  key_task_count: number
}

interface Project {
  id: number
  name: string
  project_type: string
  level: string
  status: string
  current_phase: string | null
  owner: { id: number; name: string }
  max_blocker: { description: string; party_type: string; next_action: string } | null
  next_update_date: string | null
  is_update_overdue: boolean
  task_total: number
  task_done: number
  task_in_progress: number
  task_overdue: number
  week_report_items: number
  week_report_inferred: number
  week_escalate: number
  week_quality: 'good' | 'ok' | 'bad'
}

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()
const canViewReports = computed(() => userStore.hasPermission('report.view.all'))
const canCreateProject = computed(() => userStore.hasPermission('project.create'))
const canDeleteProject = computed(() => userStore.hasPermission('project.delete'))

function canEditRow(row: Project) {
  return userStore.canEditOwnedProject(row.owner?.id)
}

function canOperateRow(row: Project) {
  return canEditRow(row) || canDeleteProject.value
}

const stats = ref<Stats>({
  total_projects: 0,
  key_count: 0,
  daily_count: 0,
  pending_blockers: 0,
  task_total: 0,
  task_done: 0,
  task_completion_rate: 0,
  report_period_start: null,
  report_period_end: null,
  report_auto_switched: false,
  report_expected: 0,
  report_submitted: 0,
  report_submit_rate: 0,
  report_quality_alerts: 0,
  unassigned_report_items: 0,
  key_task_count: 0,
})
const projects = ref<Project[]>([])
const loading = ref(false)

interface AttributionItem {
  id: number
  description: string
  party_type: string
  status: string
  responsible_party?: string
  next_action?: string
  project_id: number
  project_name?: string | null
  project_level?: string
  is_key_project?: boolean
  assignee_name?: string | null
}
interface AttributionData {
  counts: Record<string, number>
  total: number
  items: AttributionItem[]
}
const attribution = ref<AttributionData>({
  counts: { third_party: 0, customer: 0, internal: 0, approval: 0, other: 0 },
  total: 0,
  items: [],
})
interface KeyTaskRow {
  id: number
  title: string
  project_id: number
  project_name?: string | null
  assignee_name?: string | null
  due_date?: string | null
  priority?: string
  status?: string
}
const partyLabel: Record<string, string> = {
  third_party: '第三方',
  customer: '客户',
  internal: '内部',
  approval: '审批',
  other: '其他',
}
const levelFromQuery = typeof route.query.level === 'string' ? route.query.level : ''
const statusFromQuery = typeof route.query.status === 'string' ? route.query.status : ''
const filters = ref({
  level: ['key', 'normal', 'daily'].includes(levelFromQuery) ? levelFromQuery : '',
  project_type: '',
  status: ['normal', 'at_risk', 'blocked'].includes(statusFromQuery) ? statusFromQuery : '',
  owner_id: null as number | null,
  search: '',
})
/** 负责人下拉：从历次列表结果累积，避免筛窄后选项变空 */
const ownerOptions = ref<{ id: number; name: string }[]>([])
const attrPanelRef = ref<HTMLElement | null>(null)
const projectPanelRef = ref<HTMLElement | null>(null)

function mergeOwnerOptions(rows: Project[]) {
  const map = new Map(ownerOptions.value.map((o) => [o.id, o.name]))
  for (const row of rows) {
    if (row.owner?.id) map.set(row.owner.id, row.owner.name)
  }
  ownerOptions.value = [...map.entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
}

/** 列表分页 */
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

function projectListParams() {
  const f = filters.value
  return {
    level: f.level || undefined,
    project_type: f.project_type || undefined,
    status: f.status || undefined,
    owner_id: f.owner_id || undefined,
    search: f.search || undefined,
    page: page.value,
    page_size: pageSize.value,
  }
}

const showCreate = ref(false)
const creating = ref(false)
const createForm = ref({
  name: '',
  project_type: 'software',
  level: 'normal',
  description: '',
  current_phase: '',
  lead_user_id: null as number | null,
  support_user_ids: [] as number[],
})
const roleUsers = ref<{ id: number; name: string; job_title?: string }[]>([])
const managerOptions = computed(() =>
  withSelectedOptions(
    pickManagerOptions(roleUsers.value),
    [createForm.value.lead_user_id],
    roleUsers.value,
  ),
)
const supportOptions = computed(() =>
  withSelectedOptions(
    pickSupportOptions(roleUsers.value),
    createForm.value.support_user_ids,
    roleUsers.value,
  ),
)
const projectAiText = ref('')
const projectAiLoading = ref(false)
const projectAiHint = ref('')

/** 写周报/写任务带过来的回跳路径（仅允许白名单） */
const ALLOWED_RETURN_TO = ['/progress-report', '/write-task', '/tasks', '/reports'] as const

function resolveReturnTo(): string | null {
  const raw = route.query.returnTo
  if (typeof raw !== 'string') return null
  return (ALLOWED_RETURN_TO as readonly string[]).includes(raw) ? raw : null
}

/** 写任务回跳时带上 Tab（如 write） */
function buildReturnQuery(extra: Record<string, string> = {}): Record<string, string> {
  const query: Record<string, string> = { ...extra }
  const returnTab = route.query.returnTab
  if (typeof returnTab === 'string' && returnTab) {
    query.tab = returnTab
  }
  const itemIndex = route.query.itemIndex
  if (typeof itemIndex === 'string' && itemIndex !== '') {
    query.itemIndex = itemIndex
  }
  const edit = route.query.edit
  if (typeof edit === 'string' && edit !== '') {
    query.edit = edit
  }
  return query
}

function cancelCreate() {
  showCreate.value = false
  const returnTo = resolveReturnTo()
  if (returnTo) {
    router.push({ path: returnTo, query: buildReturnQuery() })
  }
}

/** 列表内编辑名称/类型 */
const showEdit = ref(false)
const savingEdit = ref(false)
const editTargetId = ref<number | null>(null)
const editForm = ref({ name: '', project_type: 'software' })

/** 质量告警明细弹窗 */
interface QualityAlertRow {
  person: string
  kind: string
  detail: string
  reportId: number | null
}
const showQuality = ref(false)
const qualityLoading = ref(false)
const qualityRows = ref<QualityAlertRow[]>([])

/** 重点任务明细弹窗 */
const showKeyTasks = ref(false)
const keyTasksLoading = ref(false)
const keyTasks = ref<KeyTaskRow[]>([])

/** 重点项目明细弹窗 */
const showKeyProjects = ref(false)
const keyProjectsLoading = ref(false)
const keyProjectsList = ref<Project[]>([])

const typeMap: Record<string, string> = { ...PROJECT_TYPE_MAP }
const partyMap: Record<string, string> = {
  third_party: '第三方', customer: '客户', internal: '内部', approval: '审批',
}

function levelTag(level: string) {
  if (level === 'key' || level === 'red' || level === 'yellow') {
    return { cls: 'wb-tag--yellow', text: PROJECT_LEVEL_MAP.key }
  }
  if (level === 'daily') return { cls: 'wb-tag--green', text: PROJECT_LEVEL_MAP.daily }
  return { cls: 'wb-tag--green', text: PROJECT_LEVEL_MAP.normal }
}

function statusTag(status: string) {
  if (status === 'blocked') return { cls: 'wb-tag--red', text: '已阻塞' }
  if (status === 'at_risk') return { cls: 'wb-tag--yellow', text: '有风险' }
  return { cls: 'wb-tag--green', text: '正常' }
}

function qualityTag(q: string) {
  if (q === 'bad') return { cls: 'wb-tag--red', text: '差' }
  if (q === 'ok') return { cls: 'wb-tag--yellow', text: '中' }
  return { cls: 'wb-tag--green', text: '优' }
}

function pct(rate: number) {
  return `${Math.round((rate || 0) * 100)}%`
}

function taskPct(row: Project) {
  if (!row.task_total) return 0
  return Math.round((row.task_done / row.task_total) * 100)
}

const periodLabel = computed(() => {
  const s = stats.value.report_period_start
  const e = stats.value.report_period_end
  if (!s) return ''
  return e && e !== s ? `${s} ~ ${e}` : s
})

async function load() {
  loading.value = true
  try {
    const [s, p, attr] = await Promise.all([
      api.get('/projects/overview/stats'),
      api.get('/projects', { params: projectListParams() }),
      api.get('/projects/overview/blocker-attribution'),
    ])
    stats.value = { ...stats.value, ...s.data }
    const items = p.data?.items ?? []
    projects.value = items
    total.value = p.data?.total ?? 0
    if (p.data?.page) page.value = p.data.page
    if (p.data?.page_size) pageSize.value = p.data.page_size
    mergeOwnerOptions(items)
    attribution.value = {
      counts: {
        third_party: 0,
        customer: 0,
        internal: 0,
        approval: 0,
        other: 0,
        ...(attr.data?.counts || {}),
      },
      total: attr.data?.total || 0,
      items: attr.data?.items || [],
    }
  } finally {
    loading.value = false
  }
}

function queryProjects() {
  page.value = 1
  load()
}

function onPageChange() {
  load()
}

function onPageSizeChange() {
  page.value = 1
  load()
}

/** 日常项目统计卡 → 列表筛日常并滚到表格 */
async function filterDailyProjects() {
  filters.value.level = 'daily'
  page.value = 1
  await load()
  await nextTick()
  projectPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 待处理卡点统计卡 → 滚到卡点归因区（不改挂接逻辑） */
async function focusPendingBlockers() {
  if (!stats.value.pending_blockers && !attribution.value.total) {
    ElMessage.info('当前没有待处理卡点')
    return
  }
  await nextTick()
  if (attrPanelRef.value) {
    attrPanelRef.value.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  // 归因区因无数据未渲染时，仍滚到项目列表（列表有「最大卡点」列）
  projectPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function openCreate() {
  createForm.value = {
    name: '',
    project_type: 'software',
    level: 'normal',
    description: '',
    current_phase: '',
    lead_user_id: null,
    support_user_ids: [],
  }
  projectAiText.value = ''
  projectAiHint.value = ''
  showCreate.value = true
  await loadRoleCandidates()
  const defaults = defaultRolesForCreator(userStore.user)
  createForm.value.lead_user_id = defaults.lead_user_id
  createForm.value.support_user_ids = [...defaults.support_user_ids]
}

async function loadRoleCandidates() {
  try {
    const { data } = await api.get('/projects/role-candidates')
    roleUsers.value = data
  } catch (e: any) {
    roleUsers.value = []
    ElMessage.error(e?.response?.data?.detail || '人选加载失败')
  }
}

function openEdit(row: Project, ev?: Event) {
  ev?.stopPropagation()
  if (!canEditRow(row)) return
  editTargetId.value = row.id
  editForm.value = {
    name: row.name || '',
    project_type: row.project_type || 'software',
  }
  showEdit.value = true
}

async function removeProject(row: Project, ev?: Event) {
  ev?.stopPropagation()
  if (!canDeleteProject.value) return
  if (row.name === '日常任务') {
    ElMessage.warning('系统内置项目「日常任务」不可删除')
    return
  }
  let password = ''
  try {
    const res = await ElMessageBox.prompt(
      `将永久删除项目「${row.name}」及其阶段、任务、卡点等关联数据，不能恢复。请输入你的登录密码确认。`,
      '删除项目',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        inputType: 'password',
        inputPlaceholder: '登录密码',
        inputValidator: (v) => ((v || '').trim() ? true : '请输入密码'),
      },
    )
    password = (res.value || '').trim()
  } catch {
    return
  }
  try {
    await api.delete(`/projects/${row.id}`, { data: { password } })
    ElMessage.success('项目已删除')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function submitEdit() {
  const id = editTargetId.value
  if (!id) return
  const name = editForm.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写项目名称')
    return
  }
  if (name === '日常任务') {
    ElMessage.warning('「日常任务」为系统内置名称，请换用真实立项名')
    return
  }
  savingEdit.value = true
  try {
    await api.patch(`/projects/${id}`, {
      name,
      project_type: editForm.value.project_type,
    })
    ElMessage.success('项目信息已保存')
    showEdit.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    savingEdit.value = false
  }
}

async function runProjectAiDraft() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const text = projectAiText.value.trim()
  if (!text) {
    ElMessage.warning('写一句要立什么项，例如：新建家庭医生运维项目，当前卡在联调')
    return
  }
  projectAiLoading.value = true
  try {
    const { data } = await api.post('/ai/project/draft', { text })
    createForm.value.name = data.name || createForm.value.name
    if (data.project_type) createForm.value.project_type = data.project_type
    if (data.level) createForm.value.level = data.level
    createForm.value.description = data.description || text
    createForm.value.current_phase = data.current_phase || ''
    projectAiHint.value = data.rationale
      ? `${data.rationale}（${data.source === 'llm' ? 'AI' : '规则'}）`
      : ''
    ElMessage.success('已生成立项草稿，确认后创建')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || 'AI 草稿失败')
  } finally {
    projectAiLoading.value = false
  }
}

async function submitCreate() {
  const name = createForm.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写立项项目名称')
    return
  }
  if (name === '日常任务') {
    ElMessage.warning('「日常任务」为系统内置项目，请换用真实立项名称')
    return
  }
  if (!userStore.user?.id) {
    ElMessage.error('未登录')
    return
  }
  if (!createForm.value.support_user_ids.length) {
    ElMessage.warning('请至少选择一名项目支撑')
    return
  }
  if (
    createForm.value.lead_user_id != null
    && createForm.value.support_user_ids.includes(createForm.value.lead_user_id)
  ) {
    ElMessage.warning('项目经理和项目支撑不能是同一个人')
    return
  }
  creating.value = true
  try {
    const { data } = await api.post('/projects', {
      name,
      project_type: createForm.value.project_type,
      level: createForm.value.level,
      lead_user_id: createForm.value.lead_user_id,
      support_user_ids: createForm.value.support_user_ids,
      description: createForm.value.description || null,
    })
    if (createForm.value.current_phase) {
      try {
        await api.patch(`/projects/${data.id}`, {
          current_phase: createForm.value.current_phase,
        })
      } catch {
        /* 阶段可选，失败不挡创建 */
      }
    }
    ElMessage.success(
      createForm.value.lead_user_id
        ? '项目已创建，已通知项目经理和项目支撑'
        : '项目已创建，已通知项目支撑（项目经理可稍后指定）',
    )
    showCreate.value = false
    await load()
    const returnTo = resolveReturnTo()
    if (returnTo) {
      router.push({
        path: returnTo,
        query: buildReturnQuery({ selectProjectId: String(data.id) }),
      })
    } else {
      router.push(`/projects/${data.id}`)
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

async function openQualityAlerts() {
  if (!canViewReports.value) return
  showQuality.value = true
  qualityLoading.value = true
  qualityRows.value = []
  try {
    const params: Record<string, string> = { period_type: 'week' }
    if (stats.value.report_period_start) {
      params.period_start = stats.value.report_period_start
    }
    const { data } = await api.get('/progress-reports/board', {
      params: { ...params, mode: 'full' },
    })
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
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载告警失败')
  } finally {
    qualityLoading.value = false
  }
}

function openAlertReport(row: QualityAlertRow) {
  if (!row.reportId) return
  showQuality.value = false
  router.push({ path: '/reports', query: { tab: 'write', view: String(row.reportId) } })
}

function goReportBoard() {
  showQuality.value = false
  const q: Record<string, string> = {}
  if (stats.value.report_period_start) q.period = stats.value.report_period_start
  router.push({ path: '/reports', query: q })
}

async function openKeyTasks() {
  showKeyTasks.value = true
  keyTasksLoading.value = true
  keyTasks.value = []
  try {
    const { data } = await api.get('/projects/overview/key-tasks')
    keyTasks.value = data || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载重点任务失败')
  } finally {
    keyTasksLoading.value = false
  }
}

async function openKeyProjects() {
  showKeyProjects.value = true
  keyProjectsLoading.value = true
  keyProjectsList.value = []
  try {
    const { data } = await api.get('/projects', { params: { level: 'key', page_size: 100 } })
    keyProjectsList.value = data?.items ?? []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载重点项目失败')
  } finally {
    keyProjectsLoading.value = false
  }
}

function openKeyProjectRow(row: Project) {
  showKeyProjects.value = false
  router.push(`/projects/${row.id}`)
}

function openKeyTaskRow(row: KeyTaskRow) {
  showKeyTasks.value = false
  router.push(`/projects/${row.project_id}`)
}

onMounted(async () => {
  await load()
  // 首屏若已带筛选，再静默拉一次全量，补全负责人下拉（不改列表当前筛选结果）
  if (filters.value.level || filters.value.status || filters.value.owner_id || filters.value.search) {
    try {
      const { data } = await api.get('/projects', { params: { page_size: 100 } })
      mergeOwnerOptions(data?.items ?? [])
    } catch {
      /* 忽略，下拉仍可用当前页累积 */
    }
  }
  if (route.query.create === '1' && canCreateProject.value) {
    openCreate()
  }
})
</script>

<template>
  <div>
    <h1 class="wb-page-title">项目管理</h1>
    <p class="wb-page-desc">看项目风险、任务进度和本周周报；请按真实立项名称建项目，杂项归「日常任务」</p>

    <div
      v-if="canViewReports && stats.unassigned_report_items > 0"
      class="unassigned-banner"
      @click="router.push('/reports')"
    >
      有 {{ stats.unassigned_report_items }} 条周报任务未能归类到项目（点此去周报管理查看「未归类」）
    </div>

    <div class="stats-grid">
      <div class="wb-stat-card">
        <div class="wb-stat-num">{{ stats.total_projects }}</div>
        <div class="wb-stat-label">项目总数</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div
        class="wb-stat-card wb-stat-card--clickable"
        role="button"
        tabindex="0"
        @click="openKeyProjects"
        @keydown.enter.prevent="openKeyProjects"
      >
        <div class="wb-stat-num stat-warn">{{ stats.key_count ?? stats.red_count ?? 0 }}</div>
        <div class="wb-stat-label">重点项目 · 点击查看</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div
        class="wb-stat-card wb-stat-card--clickable"
        role="button"
        tabindex="0"
        @click="filterDailyProjects"
        @keydown.enter.prevent="filterDailyProjects"
      >
        <div class="wb-stat-num stat-caution">{{ stats.daily_count ?? stats.yellow_count ?? 0 }}</div>
        <div class="wb-stat-label">日常项目 · 点击筛选</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div
        class="wb-stat-card wb-stat-card--clickable"
        role="button"
        tabindex="0"
        @click="focusPendingBlockers"
        @keydown.enter.prevent="focusPendingBlockers"
      >
        <div class="wb-stat-num">{{ stats.pending_blockers }}</div>
        <div class="wb-stat-label">待处理卡点 · 点击定位</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div class="wb-stat-card">
        <div class="wb-stat-num">{{ pct(stats.task_completion_rate) }}</div>
        <div class="wb-stat-label">任务完成率（{{ stats.task_done }}/{{ stats.task_total }}）</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div v-if="canViewReports" class="wb-stat-card">
        <div class="wb-stat-num">{{ stats.report_submitted }}/{{ stats.report_expected }}</div>
        <div class="wb-stat-label">周报提交（{{ pct(stats.report_submit_rate) }}）</div>
        <div class="stat-meta">
          <span v-if="periodLabel" class="period-hint">
            {{ periodLabel }}<template v-if="stats.report_auto_switched"> · 已切最近有数据周</template>
          </span>
          <template v-else>&nbsp;</template>
        </div>
      </div>
      <div
        v-if="canViewReports"
        class="wb-stat-card wb-stat-card--clickable"
        role="button"
        tabindex="0"
        @click="openQualityAlerts"
        @keydown.enter.prevent="openQualityAlerts"
      >
        <div class="wb-stat-num" :class="{ 'stat-warn': stats.report_quality_alerts > 0 }">
          {{ stats.report_quality_alerts }}
        </div>
        <div class="wb-stat-label">本周质量告警 · 点击查看</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div
        class="wb-stat-card wb-stat-card--clickable"
        role="button"
        tabindex="0"
        @click="openKeyTasks"
        @keydown.enter.prevent="openKeyTasks"
      >
        <div class="wb-stat-num" :class="{ 'stat-warn': stats.key_task_count > 0 }">
          {{ stats.key_task_count }}
        </div>
        <div class="wb-stat-label">重点任务 · 点击查看</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
      <div
        v-if="canViewReports"
        class="wb-stat-card wb-stat-card--clickable wb-stat-card--link"
        role="button"
        tabindex="0"
        @click="goReportBoard"
        @keydown.enter.prevent="goReportBoard"
      >
        <div class="wb-stat-num wb-stat-num--sm">周报管理</div>
        <div class="wb-stat-label">查看全员提交与任务全文</div>
        <div class="stat-meta" aria-hidden="true">&nbsp;</div>
      </div>
    </div>

    <div v-if="attribution.total > 0" ref="attrPanelRef" class="attr-panel">
      <div class="attr-head">卡点归因（按责任方）</div>
      <div class="attr-counts">
        <div
          v-for="key in ['third_party', 'customer', 'internal', 'approval']"
          :key="key"
          class="attr-chip"
        >
          <div class="attr-num">{{ attribution.counts[key] || 0 }}</div>
          <div class="attr-label">{{ partyLabel[key] }}</div>
        </div>
      </div>
      <el-table
        :data="attribution.items.slice(0, 12)"
        size="small"
        class="attr-table"
        @row-click="(row: AttributionItem) => router.push(`/projects/${row.project_id}`)"
      >
        <el-table-column label="项目" min-width="140">
          <template #default="{ row }">
            <span :class="{ 'is-key-proj': row.is_key_project }">{{ row.project_name }}</span>
            <el-tag v-if="row.is_key_project" size="small" type="danger" class="key-tag">重点</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="卡点" prop="description" min-width="180" show-overflow-tooltip />
        <el-table-column label="责任方类型" width="100">
          <template #default="{ row }">{{ partyLabel[row.party_type] || row.party_type }}</template>
        </el-table-column>
        <el-table-column label="下一步" prop="next_action" min-width="140" show-overflow-tooltip />
      </el-table>
    </div>

    <div ref="projectPanelRef" class="panel">
      <div class="toolbar">
        <el-button v-if="canCreateProject" type="primary" @click="openCreate">新建项目</el-button>
        <el-select v-model="filters.level" placeholder="分级" clearable class="tool-item" @change="queryProjects">
          <el-option label="重点" value="key" />
          <el-option label="普通" value="normal" />
          <el-option label="日常" value="daily" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable class="tool-item" @change="queryProjects">
          <el-option label="正常" value="normal" />
          <el-option label="有风险" value="at_risk" />
          <el-option label="已阻塞" value="blocked" />
        </el-select>
        <el-select
          v-model="filters.owner_id"
          placeholder="负责人"
          clearable
          filterable
          class="tool-item"
          @change="queryProjects"
        >
          <el-option
            v-for="o in ownerOptions"
            :key="o.id"
            :label="o.name"
            :value="o.id"
          />
        </el-select>
        <el-select v-model="filters.project_type" placeholder="类型" clearable class="tool-item" @change="queryProjects">
          <el-option v-for="(l, v) in typeMap" :key="v" :label="l" :value="v" />
        </el-select>
        <el-input v-model="filters.search" placeholder="搜索项目名" clearable class="tool-search" @keyup.enter="queryProjects" />
        <el-button type="primary" plain @click="queryProjects">查询</el-button>
      </div>

      <el-table
        :data="projects"
        v-loading="loading"
        @row-click="(row: Project) => router.push(`/projects/${row.id}`)"
        class="data-table"
      >
        <el-table-column label="分级" width="110">
          <template #default="{ row }">
            <span class="wb-tag" :class="levelTag(row.level).cls">{{ levelTag(row.level).text }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="项目名" min-width="140" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ typeMap[row.project_type] || row.project_type }}</template>
        </el-table-column>
        <el-table-column label="负责人" width="90">
          <template #default="{ row }">{{ row.owner.name }}</template>
        </el-table-column>
        <el-table-column prop="current_phase" label="当前阶段" width="110" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <span class="wb-tag" :class="statusTag(row.status).cls">{{ statusTag(row.status).text }}</span>
          </template>
        </el-table-column>
        <el-table-column label="任务进度" min-width="140">
          <template #default="{ row }">
            <div v-if="row.task_total" class="task-cell">
              <el-progress :percentage="taskPct(row)" :stroke-width="10" />
              <span class="task-meta">
                {{ row.task_done }}/{{ row.task_total }}
                <span v-if="row.task_overdue" class="overdue"> · 逾期{{ row.task_overdue }}</span>
              </span>
            </div>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canViewReports" label="本周周报" width="110">
          <template #default="{ row }">
            <div v-if="row.week_report_items">
              {{ row.week_report_items }} 条
              <div v-if="row.week_report_inferred" class="inferred-hint">含推断 {{ row.week_report_inferred }}</div>
            </div>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canViewReports" label="周报质量" width="90">
          <template #default="{ row }">
            <span
              v-if="row.week_report_items"
              class="wb-tag"
              :class="qualityTag(row.week_quality).cls"
            >{{ qualityTag(row.week_quality).text }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="最大卡点" min-width="160">
          <template #default="{ row }">{{ row.max_blocker?.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="卡点方" width="80">
          <template #default="{ row }">{{ row.max_blocker ? partyMap[row.max_blocker.party_type] : '—' }}</template>
        </el-table-column>
        <el-table-column label="下一步" min-width="140">
          <template #default="{ row }">{{ row.max_blocker?.next_action || '—' }}</template>
        </el-table-column>
        <el-table-column label="下次更新" width="110">
          <template #default="{ row }">
            <span :class="{ overdue: row.is_update_overdue }">{{ row.next_update_date || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <template v-if="canOperateRow(row)">
              <el-button
                v-if="canEditRow(row)"
                type="primary"
                link
                @click="(e: Event) => openEdit(row, e)"
              >
                编辑
              </el-button>
              <el-button
                v-if="canDeleteProject"
                type="danger"
                link
                @click="(e: Event) => removeProject(row, e)"
              >
                删除
              </el-button>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[10, 50, 100]"
          layout="total, sizes, prev, pager, next"
          :total="total"
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </div>

    <el-dialog v-model="showKeyProjects" title="重点项目" width="720px" destroy-on-close>
      <p class="create-hint">分级为「重点」的项目；点一行进入项目详情。</p>
      <el-table
        v-loading="keyProjectsLoading"
        :data="keyProjectsList"
        size="small"
        max-height="420"
        class="quality-table"
        @row-click="openKeyProjectRow"
      >
        <el-table-column prop="name" label="项目" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">{{ statusTag(row.status).text }}</template>
        </el-table-column>
        <el-table-column label="负责人" width="100">
          <template #default="{ row }">{{ row.owner?.name || '—' }}</template>
        </el-table-column>
        <el-table-column label="逾期任务" width="100">
          <template #default="{ row }">{{ row.task_overdue ?? 0 }}</template>
        </el-table-column>
      </el-table>
      <div v-if="!keyProjectsLoading && !keyProjectsList.length" class="quality-empty">暂无重点项目</div>
      <template #footer>
        <el-button @click="showKeyProjects = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showKeyTasks" title="重点任务" width="720px" destroy-on-close>
      <p class="create-hint">未完成且级别为「重点」的任务；点一行进入对应项目。</p>
      <el-table
        v-loading="keyTasksLoading"
        :data="keyTasks"
        size="small"
        max-height="420"
        class="quality-table"
        @row-click="openKeyTaskRow"
      >
        <el-table-column prop="title" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column prop="project_name" label="项目" width="140" show-overflow-tooltip />
        <el-table-column prop="assignee_name" label="负责人" width="100" />
        <el-table-column prop="due_date" label="截止" width="110" />
      </el-table>
      <div v-if="!keyTasksLoading && !keyTasks.length" class="quality-empty">暂无重点任务</div>
      <template #footer>
        <el-button @click="showKeyTasks = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showQuality" title="本周质量告警" width="720px" destroy-on-close>
      <p class="create-hint">含质量提示、需升级、卡点/协调。点一行可打开对应周报。</p>
      <el-table
        v-loading="qualityLoading"
        :data="qualityRows"
        size="small"
        max-height="420"
        class="quality-table"
        @row-click="openAlertReport"
      >
        <el-table-column prop="person" label="人员" width="100" />
        <el-table-column prop="kind" label="类型" width="100" />
        <el-table-column prop="detail" label="说明" min-width="280" show-overflow-tooltip />
      </el-table>
      <div v-if="!qualityLoading && !qualityRows.length" class="quality-empty">本周暂无告警明细</div>
      <template #footer>
        <el-button @click="showQuality = false">关闭</el-button>
        <el-button type="primary" @click="goReportBoard">去周报管理</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreate" title="新建立项项目" width="520px">
      <p class="create-hint">请按真实立项项目名称创建；杂项/无立项工作请使用系统内的「日常任务」，不要另建假名。</p>
      <AgentNativeHint v-if="agentNative" tip="立项请到「我的助手」里说，确认后再写入账本。" />
      <div v-else class="ai-box">
        <el-input
          v-model="projectAiText"
          type="textarea"
          :rows="2"
          placeholder="一句话描述要立什么项（可选）"
        />
        <el-button
          type="primary"
          plain
          size="small"
          style="margin-top:8px"
          :loading="projectAiLoading"
          @click="runProjectAiDraft"
        >
          AI 生成草稿
        </el-button>
        <p v-if="projectAiHint" class="create-hint" style="margin-top:6px;margin-bottom:0">{{ projectAiHint }}</p>
      </div>
      <el-form label-width="88px" style="margin-top:12px">
        <el-form-item label="项目名称" required>
          <el-input v-model="createForm.name" maxlength="128" placeholder="与立项名称一致" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="createForm.project_type" style="width:100%">
            <el-option v-for="(l, v) in typeMap" :key="v" :label="l" :value="v" />
          </el-select>
          <p class="create-hint" style="margin-top:6px;margin-bottom:0">
            三类：软件开发 / 系统集成 / 集众融合（如智安校园）。
          </p>
        </el-form-item>
        <el-form-item label="分级">
          <el-select v-model="createForm.level" style="width:100%">
            <el-option label="普通" value="normal" />
            <el-option label="重点" value="key" />
            <el-option label="日常" value="daily" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目经理">
          <el-select
            v-model="createForm.lead_user_id"
            filterable
            clearable
            style="width:100%"
            placeholder="可先空着，稍后再指定"
          >
            <el-option v-for="u in managerOptions" :key="u.id" :label="u.name" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目支撑" required>
          <el-select
            v-model="createForm.support_user_ids"
            multiple
            filterable
            style="width:100%"
            placeholder="可多选；创建人也可以是支撑"
          >
            <el-option v-for="u in supportOptions" :key="u.id" :label="u.name" :value="u.id" />
          </el-select>
          <p v-if="!supportOptions.length" class="note">
            除许金斌、周永祺（项目经理）和孙振振（管理员）外，其他同事都可作为项目支撑。若列表为空，请先在系统管理同步钉钉通讯录。
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelCreate">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">确认创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showEdit" title="编辑项目" width="480px" destroy-on-close>
      <el-form label-width="88px">
        <el-form-item label="项目名称" required>
          <el-input v-model="editForm.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editForm.project_type" style="width:100%">
            <el-option v-for="(l, v) in typeMap" :key="v" :label="l" :value="v" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ai-box {
  background: #f7faf8;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  padding: 12px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
  align-items: stretch;
}
.stats-grid .wb-stat-card {
  height: 100%;
  min-height: 112px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.wb-stat-card--clickable {
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.wb-stat-card--clickable:hover,
.wb-stat-card--clickable:focus-visible {
  border-color: #a8c5b5;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  outline: none;
}
.wb-stat-card--link .wb-stat-num--sm {
  font-size: 20px;
  letter-spacing: 0;
}
.stat-meta {
  margin-top: auto;
  min-height: 18px;
  padding-top: 4px;
}
.stat-warn { color: #c45656; }
.stat-caution { color: #b88230; }
.period-hint {
  display: block;
  font-size: 12px;
  color: var(--wb-muted, #888);
  font-weight: 400;
  line-height: 1.35;
}
.quality-table { cursor: pointer; }
.quality-empty {
  padding: 24px 0;
  text-align: center;
  color: #999;
  font-size: 14px;
}

.attr-panel {
  background: var(--wb-bg-muted, #f7f8f7);
  border: 1px solid var(--wb-border, #e5e7eb);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.attr-head {
  font-weight: 600;
  margin-bottom: 10px;
}
.attr-counts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.attr-chip {
  background: #fff;
  border: 1px solid #e8ebe9;
  border-radius: 8px;
  padding: 8px;
  text-align: center;
}
.attr-num {
  font-size: 20px;
  font-weight: 700;
}
.attr-label {
  font-size: 12px;
  color: #666;
}
.attr-table { cursor: pointer; }
.is-key-proj { font-weight: 600; color: #b45309; }
.key-tag { margin-left: 6px; }

@media (max-width: 992px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .attr-counts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 520px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}

.unassigned-banner {
  margin-bottom: 16px;
  padding: 10px 14px;
  background: #fff7e8;
  border: 1px solid #f0d9a8;
  border-radius: var(--wb-radius, 8px);
  color: #8a6d3b;
  cursor: pointer;
  font-size: 14px;
}
.unassigned-banner:hover { background: #fff1d6; }
.create-hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
  line-height: 1.5;
}

.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 20px;
  box-shadow: var(--wb-shadow-soft);
}

.toolbar {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 16px;
}

.tool-item { width: 140px; }
.tool-search { width: 220px; }

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.data-table {
  cursor: pointer;
  --el-table-border: none;
}

.data-table :deep(.el-table__inner-wrapper::before) {
  display: none;
}

.task-cell { line-height: 1.3; }
.task-meta { font-size: 12px; color: #666; }
.inferred-hint { font-size: 12px; color: #999; }
.muted { color: #bbb; }

.overdue {
  color: #c45656;
  font-weight: 500;
}
</style>
