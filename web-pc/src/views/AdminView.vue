<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { PASSWORD_RULES, validateStrongPassword } from '../utils/password'

const userStore = useUserStore()

function defaultAdminTab(): string {
  if (userStore.hasPermission('admin.user')) return 'users'
  if (userStore.hasPermission('admin.role')) return 'roles'
  if (userStore.hasPermission('admin.model')) return 'models'
  if (userStore.hasPermission('admin.audit')) return 'audit'
  if (userStore.hasPermission('admin.notify')) return 'notify-logs'
  return 'users'
}

const activeTab = ref(defaultAdminTab())
const users = ref<any[]>([])
const roles = ref<any[]>([])
const permissions = ref<any[]>([])
const auditLogs = ref<any[]>([])
const models = ref<any[]>([])
const loading = ref(false)

const embeddingForm = ref({
  embedding_model: '',
  embedding_base_url: '',
  api_key: '',
  clear_api_key: false,
})
const embeddingMeta = ref({
  enabled: false,
  api_key_masked: '',
  api_key_configured: false,
  source: 'env',
  hint: '',
})
const embeddingLoading = ref(false)
const embeddingTesting = ref(false)

const notifyLogs = ref<any[]>([])
const notifyLogsTotal = ref(0)
const notifyLogFilter = ref({ event_key: '', channel: '', status: '' })
const notifyTemplates = ref<any[]>([])
const notifyLoading = ref(false)
const showTplEdit = ref(false)
const tplForm = ref<any>({})
const EVENT_LABELS: Record<string, string> = {
  task_assigned: '交办任务',
  task_accepted: '任务已接收',
  task_remind: '催办',
  task_completed: '交办完成',
  task_priority_p0: 'P0 重点',
  project_role_assigned: '项目角色',
  blocker_declared: '申报卡点',
  blocker_follow_up_overdue: '卡点逾期',
  phase_overdue: '阶段逾期',
  weekly_report_overdue: '周报未交',
}
const CHANNEL_LABELS: Record<string, string> = { inbox: '站内', dingtalk: '钉钉' }
const STATUS_LABELS: Record<string, string> = {
  success: '成功',
  skipped: '跳过',
  failed: '失败',
}

type DshUsageSummaryRow = {
  username: string
  name?: string | null
  open_count: number
  last_open_at?: string | null
  prompt_count: number
  turns: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  session_count: number
}
type DshUsageVisitRow = {
  username: string
  name?: string | null
  started_at: string
  ended_at?: string | null
  duration_sec?: number | null
  source: string
  ip: string
}
type DshUsageSessionRow = {
  username: string
  name?: string | null
  session_id: string
  model: string
  provider: string
  turns: number
  input_tokens: number
  output_tokens: number
  last_prompt_at?: string | null
}

function defaultDshRange(): [Date, Date] {
  const to = new Date()
  const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000)
  return [from, to]
}

const dshUsageRange = ref<[Date, Date]>(defaultDshRange())
const dshUsageLoading = ref(false)
const dshUsageSummary = ref<DshUsageSummaryRow[]>([])
const dshUsageVisits = ref<DshUsageVisitRow[]>([])
const dshUsageSessions = ref<DshUsageSessionRow[]>([])
const dshUsageSelectedUser = ref('')
const dshUsageError = ref('')

function toIsoDayStart(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString()
}
function toIsoDayEnd(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999).toISOString()
}
function formatDshTime(value?: string | null) {
  if (!value) return '—'
  const ms = Date.parse(value)
  if (!Number.isFinite(ms)) return value
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function formatDuration(sec?: number | null) {
  if (sec == null) return '进行中/未知'
  if (sec < 60) return `${sec} 秒`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return s ? `${m} 分 ${s} 秒` : `${m} 分`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm ? `${h} 小时 ${rm} 分` : `${h} 小时`
}
function sourceLabel(source: string) {
  if (source === 'sso') return '工作台进入'
  if (source === 'dingtalk') return '钉钉扫码'
  if (source === 'password') return '账号登录'
  return source || '—'
}

async function loadDshUsageDetail(username: string) {
  if (!username) {
    dshUsageVisits.value = []
    dshUsageSessions.value = []
    return
  }
  const [from, to] = dshUsageRange.value
  const params = {
    from: toIsoDayStart(from),
    to: toIsoDayEnd(to),
    username,
  }
  const [visitsRes, sessionsRes] = await Promise.all([
    api.get('/admin/dsh-usage/visits', { params }),
    api.get('/admin/dsh-usage/sessions', { params }),
  ])
  dshUsageVisits.value = visitsRes.data?.items || []
  dshUsageSessions.value = sessionsRes.data?.items || []
}

async function loadDshUsage() {
  if (!userStore.hasPermission('admin.model')) return
  dshUsageLoading.value = true
  dshUsageError.value = ''
  try {
    const [from, to] = dshUsageRange.value
    const { data } = await api.get('/admin/dsh-usage/summary', {
      params: { from: toIsoDayStart(from), to: toIsoDayEnd(to) },
    })
    dshUsageSummary.value = data?.items || []
    if (dshUsageSelectedUser.value) {
      const still = dshUsageSummary.value.some((r) => r.username === dshUsageSelectedUser.value)
      if (!still) dshUsageSelectedUser.value = ''
    }
    if (dshUsageSelectedUser.value) {
      await loadDshUsageDetail(dshUsageSelectedUser.value)
    } else {
      dshUsageVisits.value = []
      dshUsageSessions.value = []
    }
  } catch (err: any) {
    dshUsageSummary.value = []
    dshUsageVisits.value = []
    dshUsageSessions.value = []
    dshUsageError.value =
      err?.response?.data?.detail || err?.message || '拉取助手使用情况失败'
  } finally {
    dshUsageLoading.value = false
  }
}

async function selectDshUsageUser(row: DshUsageSummaryRow) {
  dshUsageSelectedUser.value = row.username
  dshUsageLoading.value = true
  dshUsageError.value = ''
  try {
    await loadDshUsageDetail(row.username)
  } catch (err: any) {
    dshUsageVisits.value = []
    dshUsageSessions.value = []
    dshUsageError.value =
      err?.response?.data?.detail || err?.message || '拉取明细失败'
  } finally {
    dshUsageLoading.value = false
  }
}

watch(activeTab, (name) => {
  if (name === 'dsh-usage') loadDshUsage()
})

const showResetPwd = ref(false)
const resetUserId = ref<number | null>(null)
const resetUsername = ref('')
const newPassword = ref('')

const showDingBind = ref(false)
const dingUserId = ref<number | null>(null)
const dingUsername = ref('')
const dingTalkId = ref('')
const dingCandidates = ref<
  { userid: string; name: string; job_number: string; mobile_masked: string; bound_username: string | null }[]
>([])
const dingCandidatesLoading = ref(false)

function candidateLabel(c: {
  name: string
  job_number: string
  mobile_masked: string
  bound_username: string | null
}) {
  const parts = [c.name]
  if (c.job_number) parts.push(c.job_number)
  if (c.mobile_masked) parts.push(c.mobile_masked)
  let label = parts.join(' · ')
  if (c.bound_username) label += `（已绑 ${c.bound_username}）`
  return label
}

async function loadDingCandidates(forUserId: number | null, preferUsername = '') {
  dingCandidatesLoading.value = true
  try {
    const { data } = await api.get('/admin/dingtalk/candidates', {
      params: {
        for_user_id: forUserId || undefined,
        department_name: '政企市场部',
        unbound_only: false,
      },
      timeout: 60000,
    })
    dingCandidates.value = data
    // 若尚未选择，自动选中工号与当前登录名一致且未被占用的人
    if (!dingTalkId.value && preferUsername) {
      const key = preferUsername.trim().toUpperCase()
      const hit = data.find(
        (c: { job_number: string; bound_username: string | null; userid: string }) =>
          (c.job_number || '').trim().toUpperCase() === key && !c.bound_username,
      )
      if (hit) dingTalkId.value = hit.userid
    }
  } catch (err: unknown) {
    dingCandidates.value = []
    const msg =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '拉取钉钉人员失败'
    ElMessage.error(typeof msg === 'string' ? msg : '拉取钉钉人员失败')
  } finally {
    dingCandidatesLoading.value = false
  }
}

function openBindDing(row: any) {
  dingUserId.value = row.id
  dingUsername.value = row.username
  dingTalkId.value = row.dingtalk_user_id || ''
  showDingBind.value = true
  loadDingCandidates(row.id, row.username)
}

async function submitDingBind() {
  await api.patch(`/admin/users/${dingUserId.value}/dingtalk`, {
    dingtalk_user_id: (dingTalkId.value || '').trim() || null,
  })
  ElMessage.success((dingTalkId.value || '').trim() ? '钉钉已绑定' : '钉钉已解绑')
  showDingBind.value = false
  loadUsers()
}

const showModelDialog = ref(false)
const editingModelId = ref<number | null>(null)
const modelForm = ref({
  name: '',
  api_base: '',
  api_key: '',
  model_name: '',
  purpose: 'general',
  is_enabled: true,
  is_default: false,
})
const remoteModelIds = ref<string[]>([])
const discoveringModels = ref(false)

async function loadUsers() {
  if (!userStore.hasPermission('admin.user')) return
  const { data } = await api.get('/admin/users')
  users.value = data
}

const syncing = ref(false)
const syncResult = ref<any>(null)

async function syncDeptUsers() {
  try {
    await ElMessageBox.confirm(
      '将保留 admin，删除其他所有账号，再从钉钉「政企市场部」导入成员。项目负责人会临时改挂到 admin。是否继续？',
      '从钉钉同步用户',
      { type: 'warning', confirmButtonText: '确认同步', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  syncing.value = true
  syncResult.value = null
  try {
    const { data } = await api.post('/admin/users/sync-dingtalk-dept', {
      department_name: '政企市场部',
    })
    syncResult.value = data
    ElMessage.success(
      `同步完成：删除 ${data.deleted} 人，导入 ${data.imported_count} 人，跳过 ${data.skipped_count} 人`,
    )
    await loadUsers()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '同步失败')
  } finally {
    syncing.value = false
  }
}

async function loadRoles() {
  if (!userStore.hasPermission('admin.role')) return
  const [r, p] = await Promise.all([api.get('/admin/roles'), api.get('/admin/permissions')])
  roles.value = r.data
  permissions.value = p.data
}

async function loadAudit() {
  if (!userStore.hasPermission('admin.audit')) return
  const { data } = await api.get('/admin/audit-logs')
  auditLogs.value = data
}

async function loadEmbedding() {
  if (!userStore.hasPermission('admin.model')) return
  embeddingLoading.value = true
  try {
    const { data } = await api.get('/admin/embedding-settings')
    embeddingForm.value = {
      embedding_model: data.embedding_model || '',
      embedding_base_url: data.embedding_base_url || '',
      api_key: '',
      clear_api_key: false,
    }
    embeddingMeta.value = {
      enabled: !!data.enabled,
      api_key_masked: data.api_key_masked || '',
      api_key_configured: !!data.api_key_configured,
      source: data.source || 'env',
      hint: data.hint || '',
    }
  } finally {
    embeddingLoading.value = false
  }
}

async function saveEmbedding() {
  embeddingLoading.value = true
  try {
    const payload: Record<string, unknown> = {
      embedding_model: embeddingForm.value.embedding_model.trim(),
      embedding_base_url: embeddingForm.value.embedding_base_url.trim(),
      clear_api_key: embeddingForm.value.clear_api_key,
    }
    const key = embeddingForm.value.api_key.trim()
    if (key) payload.api_key = key
    const { data } = await api.put('/admin/embedding-settings', payload)
    ElMessage.success(data.enabled ? '向量模型已启用（V2）' : '已保存；未启用向量（V1 关键词）')
    embeddingForm.value.api_key = ''
    embeddingForm.value.clear_api_key = false
    await loadEmbedding()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '保存失败')
  } finally {
    embeddingLoading.value = false
  }
}

async function testEmbedding() {
  embeddingTesting.value = true
  try {
    const payload: Record<string, unknown> = {
      embedding_model: embeddingForm.value.embedding_model.trim(),
      embedding_base_url: embeddingForm.value.embedding_base_url.trim(),
      clear_api_key: embeddingForm.value.clear_api_key,
    }
    const key = embeddingForm.value.api_key.trim()
    if (key) payload.api_key = key
    const { data } = await api.post('/admin/embedding-settings/test', payload)
    if (data.ok) ElMessage.success(data.message || '连通成功')
    else ElMessage.error(data.message || '连通失败')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '测试失败')
  } finally {
    embeddingTesting.value = false
  }
}

async function loadNotifyLogs() {
  if (!userStore.hasPermission('admin.notify')) return
  notifyLoading.value = true
  try {
    const params: Record<string, string> = {}
    if (notifyLogFilter.value.event_key) params.event_key = notifyLogFilter.value.event_key
    if (notifyLogFilter.value.channel) params.channel = notifyLogFilter.value.channel
    if (notifyLogFilter.value.status) params.status = notifyLogFilter.value.status
    const { data } = await api.get('/admin/notification-logs', { params })
    notifyLogs.value = data?.items || []
    notifyLogsTotal.value = data?.total || 0
  } finally {
    notifyLoading.value = false
  }
}

async function loadNotifyTemplates() {
  if (!userStore.hasPermission('admin.notify')) return
  const { data } = await api.get('/admin/notification-templates')
  notifyTemplates.value = data || []
}

function openTplEdit(row: any) {
  tplForm.value = {
    id: row.id,
    name: row.name,
    description: row.description,
    title_template: row.title_template,
    body_template: row.body_template,
    channel_inbox: row.channel_inbox,
    channel_dingtalk: row.channel_dingtalk,
    is_enabled: row.is_enabled,
    schedule_cron: row.schedule_cron || '',
    max_per_day: row.max_per_day ?? 0,
    quiet_start: row.quiet_start || '',
    quiet_end: row.quiet_end || '',
    event_key: row.event_key,
  }
  showTplEdit.value = true
}

async function saveTplEdit() {
  const id = tplForm.value.id
  const payload = {
    name: tplForm.value.name,
    description: tplForm.value.description,
    title_template: tplForm.value.title_template,
    body_template: tplForm.value.body_template,
    channel_inbox: tplForm.value.channel_inbox,
    channel_dingtalk: tplForm.value.channel_dingtalk,
    is_enabled: tplForm.value.is_enabled,
    schedule_cron: tplForm.value.schedule_cron,
    max_per_day: Number(tplForm.value.max_per_day) || 0,
    quiet_start: tplForm.value.quiet_start || '',
    quiet_end: tplForm.value.quiet_end || '',
  }
  await api.patch(`/admin/notification-templates/${id}`, payload)
  ElMessage.success('模板已保存')
  showTplEdit.value = false
  await loadNotifyTemplates()
}

async function runReminder(kind: string) {
  const map: Record<string, string> = {
    blocker: '/admin/reminders/blocker-overdue',
    phase: '/admin/reminders/phase-overdue',
    weekly: '/admin/reminders/weekly-report-overdue',
  }
  const url = map[kind]
  if (!url) return
  const { data } = await api.post(url)
  ElMessage.success(`已触发：扫描 ${data.scanned ?? '-'}，发送 ${data.sent ?? '-'}`)
  await loadNotifyLogs()
}

async function loadModels() {
  if (!userStore.hasPermission('admin.model')) return
  const { data } = await api.get('/admin/models')
  models.value = data
}

async function load() {
  loading.value = true
  try {
    activeTab.value = defaultAdminTab()
    await Promise.all([
      loadUsers(),
      loadRoles(),
      loadAudit(),
      loadModels(),
      loadEmbedding(),
      loadNotifyLogs(),
      loadNotifyTemplates(),
    ])
  } finally {
    loading.value = false
  }
}

async function updateUserRoles(userId: number, roleCodes: string[]) {
  await api.patch(`/admin/users/${userId}/roles`, { role_codes: roleCodes })
  ElMessage.success('角色已更新')
  loadUsers()
}

async function updateRolePerms(roleId: number, codes: string[]) {
  await api.patch(`/admin/roles/${roleId}/permissions`, { permission_codes: codes })
  ElMessage.success('权限已更新')
  loadRoles()
}

function openResetPwd(row: any) {
  resetUserId.value = row.id
  resetUsername.value = row.username
  newPassword.value = ''
  showResetPwd.value = true
}

async function unbindDing(row: any) {
  await api.patch(`/admin/users/${row.id}/dingtalk`, { dingtalk_user_id: null })
  ElMessage.success('已解绑钉钉')
  loadUsers()
}

async function deleteUser(row: any) {
  if (row.username === 'admin') {
    ElMessage.warning('不能删除系统管理员 admin')
    return
  }
  if (row.id === userStore.user?.id) {
    ElMessage.warning('不能删除当前登录账号')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除账号「${row.name}（${row.username}）」？其负责的项目/任务会改挂到 admin，周报等个人数据会一并清除，且不可恢复。`,
      '删除账号',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await api.delete(`/admin/users/${row.id}`)
    ElMessage.success('账号已删除')
    await loadUsers()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '删除失败')
  }
}

async function submitResetPwd() {
  const err = validateStrongPassword(newPassword.value)
  if (err) {
    ElMessage.error(err)
    return
  }
  await api.patch(`/admin/users/${resetUserId.value}/password`, { new_password: newPassword.value })
  ElMessage.success('密码已重置')
  showResetPwd.value = false
}

function openCreateModel() {
  editingModelId.value = null
  modelForm.value = {
    name: '',
    api_base: '',
    api_key: '',
    model_name: '',
    purpose: 'general',
    is_enabled: true,
    is_default: true,
  }
  remoteModelIds.value = []
  showModelDialog.value = true
}

function openEditModel(row: any) {
  editingModelId.value = row.id
  modelForm.value = {
    name: row.name,
    api_base: row.api_base,
    api_key: '',
    model_name: row.model_name,
    purpose: row.purpose,
    is_enabled: row.is_enabled,
    is_default: row.is_default,
  }
  remoteModelIds.value = row.model_name ? [row.model_name] : []
  showModelDialog.value = true
}

async function discoverRemoteModels() {
  if (!modelForm.value.api_base.trim()) {
    ElMessage.error('请先填写 API Base')
    return
  }
  if (!modelForm.value.api_key.trim() && !editingModelId.value) {
    ElMessage.error('请先填写 API Key')
    return
  }
  discoveringModels.value = true
  try {
    const { data } = await api.post(
      '/admin/models/discover',
      {
        api_base: modelForm.value.api_base.trim(),
        api_key: modelForm.value.api_key.trim() || null,
        model_id: editingModelId.value || null,
      },
      { timeout: 30000 },
    )
    if (!data.ok) {
      ElMessage.error(data.message || '获取模型列表失败')
      return
    }
    const ids: string[] = data.models || []
    const current = (modelForm.value.model_name || '').trim()
    if (current && !ids.includes(current)) {
      remoteModelIds.value = [current, ...ids]
    } else {
      remoteModelIds.value = ids
    }
    ElMessage.success(data.message || `已获取 ${ids.length} 个模型`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '获取模型列表失败')
  } finally {
    discoveringModels.value = false
  }
}

async function submitModel() {
  if (!modelForm.value.name.trim() || !modelForm.value.api_base.trim() || !modelForm.value.model_name.trim()) {
    ElMessage.error('请填写名称、API 地址和模型名')
    return
  }
  if (editingModelId.value) {
    const payload: any = { ...modelForm.value }
    if (!payload.api_key?.trim()) delete payload.api_key
    await api.patch(`/admin/models/${editingModelId.value}`, payload)
    ElMessage.success('模型已更新')
  } else {
    if (!modelForm.value.api_key.trim()) {
      ElMessage.error('新建时请填写 API Key')
      return
    }
    await api.post('/admin/models', modelForm.value)
    ElMessage.success('模型已添加')
  }
  showModelDialog.value = false
  loadModels()
}

async function testModel(row: any) {
  try {
    const { data } = await api.post(`/admin/models/${row.id}/test`)
    if (data.ok) ElMessage.success(data.message)
    else ElMessage.warning(data.message)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '测试失败')
  }
}

async function removeModel(row: any) {
  await ElMessageBox.confirm(`确定删除模型「${row.name}」？`, '删除确认', { type: 'warning' })
  await api.delete(`/admin/models/${row.id}`)
  ElMessage.success('已删除')
  loadModels()
}

function purposeLabel(p: string) {
  const map: Record<string, string> = {
    general: '通用',
    report_coach: '周报辅导',
    report_structure: '周报结构化',
    report_review: '周报评价',
    solution_tag: '方案打标',
    task_draft: '任务草稿',
    task_breakdown: '任务拆解',
    task_polish: '任务润色',
    task_review: '任务评价',
    project_draft: '立项草稿',
    blocker_draft: '卡点草稿',
    overview_brief: '总览摘要',
    solution_search: '方案检索',
  }
  return map[p] || p
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <h1 class="wb-page-title">系统设置</h1>
    <p class="wb-page-desc">用户、权限、模型（对话 + 向量）、钉钉与审计</p>

    <div class="panel">
    <el-tabs v-model="activeTab">
      <el-tab-pane v-if="userStore.hasPermission('admin.user')" label="用户管理" name="users">
        <div style="margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <el-button type="primary" :loading="syncing" @click="syncDeptUsers">
            从钉钉同步政企市场部
          </el-button>
          <span class="hint">登录名=工号；初始密码=工号+手机号；首次登录须改密</span>
        </div>
        <el-alert
          v-if="syncResult"
          type="info"
          :closable="true"
          style="margin-bottom:12px"
          @close="syncResult = null"
        >
          <template #title>
            上次同步：删除 {{ syncResult.deleted }}，导入 {{ syncResult.imported_count }}，跳过 {{ syncResult.skipped_count }}
          </template>
          <div v-if="syncResult.skipped?.length" style="margin-top:8px;font-size:13px">
            跳过明细：
            <span v-for="(s, i) in syncResult.skipped" :key="i" style="margin-right:12px">
              {{ s.name || s.job_number || s.userid }}（{{ s.reason }}）
            </span>
          </div>
        </el-alert>
        <el-table :data="users">
          <el-table-column prop="name" label="姓名" width="120" />
          <el-table-column label="工号" width="120">
            <template #default="{ row }">{{ row.job_number || row.username }}</template>
          </el-table-column>
          <el-table-column label="角色" min-width="180">
            <template #default="{ row }">
              <el-select
                :model-value="row.roles"
                multiple
                filterable
                @change="(v: string[]) => updateUserRoles(row.id, v)"
                style="width:100%"
              >
                <el-option v-for="r in roles" :key="r.code" :label="r.name" :value="r.code" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="钉钉" width="160">
            <template #default="{ row }">
              <el-tag :type="row.dingtalk_bound ? 'success' : 'info'" size="small">
                {{ row.dingtalk_bound ? '已绑定' : '未绑定' }}
              </el-tag>
              <el-button link type="primary" @click="openBindDing(row)">绑定</el-button>
              <el-button v-if="row.dingtalk_bound" link @click="unbindDing(row)">解绑</el-button>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button link type="primary" @click="openResetPwd(row)">重置密码</el-button>
              <el-button
                v-if="row.username !== 'admin' && row.id !== userStore.user?.id"
                link
                type="danger"
                @click="deleteUser(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'danger'">{{ row.is_active ? '启用' : '禁用' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.role')" label="权限设置" name="roles">
        <p class="hint">按角色勾选权限，立即生效。取消「钉钉免登」等于收回钉钉门禁。需要本入口请给账号挂 admin 角色（含 admin.role）。</p>
        <el-table :data="roles">
          <el-table-column prop="name" label="角色" width="140" />
          <el-table-column prop="description" label="说明" width="220" />
          <el-table-column label="权限">
            <template #default="{ row }">
              <el-select
                :model-value="row.permissions"
                multiple
                filterable
                @change="(v: string[]) => updateRolePerms(row.id, v)"
                style="width:100%"
              >
                <el-option
                  v-for="p in permissions"
                  :key="p.code"
                  :label="p.name"
                  :value="p.code"
                />
              </el-select>
            </template>
          </el-table-column>
        </el-table>
        <h4 style="margin-top:24px">全部权限点</h4>
        <el-table :data="permissions" size="small">
          <el-table-column prop="code" label="编码" width="180" />
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column prop="description" label="说明" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.model')" label="模型管理" name="models">
        <h4 class="model-section-title">长期记忆 · 向量模型</h4>
        <p class="hint">
          {{ embeddingMeta.hint || '长期记忆 V2：配置 OpenAI 兼容 embeddings（含 OpenRouter）。清空模型名则回退关键词检索（V1）。' }}
          来源：{{ embeddingMeta.source }}；
          状态：{{ embeddingMeta.enabled ? '已启用向量' : '未启用（V1）' }}；
          密钥：{{ embeddingMeta.api_key_configured ? embeddingMeta.api_key_masked : '未设置' }}
        </p>
        <el-form label-width="120px" style="max-width:640px;margin-bottom:28px" v-loading="embeddingLoading">
          <el-form-item label="模型名">
            <el-input
              v-model="embeddingForm.embedding_model"
              placeholder="例如 nvidia/nemotron-3-embed-1b:free 或 bge-m3；留空=关闭向量"
              clearable
            />
          </el-form-item>
          <el-form-item label="API Base">
            <el-input
              v-model="embeddingForm.embedding_base_url"
              placeholder="例如 https://openrouter.ai/api/v1"
              clearable
            />
          </el-form-item>
          <el-form-item label="API 密钥">
            <el-input
              v-model="embeddingForm.api_key"
              type="password"
              show-password
              placeholder="留空则保留已存密钥"
              clearable
            />
          </el-form-item>
          <el-form-item label="清除密钥">
            <el-checkbox v-model="embeddingForm.clear_api_key">清空库内密钥（仍可用 .env）</el-checkbox>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="embeddingLoading" @click="saveEmbedding">保存向量配置</el-button>
            <el-button :loading="embeddingTesting" @click="testEmbedding">测试连通</el-button>
          </el-form-item>
        </el-form>

        <h4 class="model-section-title">工作台 · 对话模型（案例打标等）</h4>
        <p class="hint">
          助手聊天请在 DSH 里配模型，不必在此重复添加。此处仅供工作台后台能力使用（如方案库上传后的自动摘要/打标）。
          建议用途选「方案打标」或「通用」。支持 OpenAI 兼容接口（DeepSeek、通义兼容、MiniMax、本地 vLLM 等）。
        </p>
        <el-button type="primary" style="margin-bottom:12px" @click="openCreateModel">添加对话模型</el-button>
        <el-table :data="models" empty-text="还没有工作台对话模型；案例自动打标需要至少配一个">
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column prop="model_name" label="模型 ID" width="140" />
          <el-table-column prop="api_base" label="API Base" min-width="180" show-overflow-tooltip />
          <el-table-column label="用途" width="100">
            <template #default="{ row }">{{ purposeLabel(row.purpose) }}</template>
          </el-table-column>
          <el-table-column prop="api_key_masked" label="密钥" width="120" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }">
              <el-tag :type="row.is_enabled ? 'success' : 'info'" size="small">{{ row.is_enabled ? '是' : '否' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="默认" width="70">
            <template #default="{ row }">
              <el-tag v-if="row.is_default" type="warning" size="small">默认</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEditModel(row)">编辑</el-button>
              <el-button link @click="testModel(row)">测试</el-button>
              <el-button link type="danger" @click="removeModel(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.model')" label="助手使用" name="dsh-usage">
        <p class="hint">
          查看全员智能助手使用情况：打开次数、进出时长、对话轮数与 token（不含人民币费用）。点某个人可看明细。
        </p>
        <div class="dsh-usage-toolbar">
          <el-date-picker
            v-model="dshUsageRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            :clearable="false"
          />
          <el-button type="primary" :loading="dshUsageLoading" @click="loadDshUsage">刷新</el-button>
        </div>
        <p v-if="dshUsageError" class="hint error">{{ dshUsageError }}</p>
        <el-table
          v-loading="dshUsageLoading"
          :data="dshUsageSummary"
          empty-text="这段时间还没有使用记录"
          highlight-current-row
          @row-click="selectDshUsageUser"
        >
          <el-table-column label="姓名" width="120">
            <template #default="{ row }">{{ row.name || '—' }}</template>
          </el-table-column>
          <el-table-column prop="username" label="账号" width="120" />
          <el-table-column prop="open_count" label="打开次数" width="90" />
          <el-table-column prop="prompt_count" label="提问次数" width="90" />
          <el-table-column prop="turns" label="对话轮数" width="90" />
          <el-table-column prop="session_count" label="会话数" width="80" />
          <el-table-column prop="input_tokens" label="输入 token" width="110" />
          <el-table-column prop="output_tokens" label="输出 token" width="110" />
          <el-table-column label="最近使用" min-width="160">
            <template #default="{ row }">{{ formatDshTime(row.last_open_at) }}</template>
          </el-table-column>
        </el-table>

        <template v-if="dshUsageSelectedUser">
          <h4 class="dsh-usage-subtitle">
            进出明细 · {{ dshUsageSelectedUser }}
            <span class="muted">（点上方表格切换人员）</span>
          </h4>
          <el-table :data="dshUsageVisits" size="small" empty-text="暂无进出记录">
            <el-table-column label="进入时间" min-width="160">
              <template #default="{ row }">{{ formatDshTime(row.started_at) }}</template>
            </el-table-column>
            <el-table-column label="离开时间" min-width="160">
              <template #default="{ row }">{{ formatDshTime(row.ended_at) }}</template>
            </el-table-column>
            <el-table-column label="时长" width="120">
              <template #default="{ row }">{{ formatDuration(row.duration_sec) }}</template>
            </el-table-column>
            <el-table-column label="来源" width="110">
              <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
            </el-table-column>
            <el-table-column prop="ip" label="IP" width="140" />
          </el-table>

          <h4 class="dsh-usage-subtitle">会话用量 · {{ dshUsageSelectedUser }}</h4>
          <el-table :data="dshUsageSessions" size="small" empty-text="暂无会话用量">
            <el-table-column prop="model" label="模型" min-width="160" show-overflow-tooltip />
            <el-table-column prop="turns" label="轮数" width="70" />
            <el-table-column prop="input_tokens" label="输入 token" width="110" />
            <el-table-column prop="output_tokens" label="输出 token" width="110" />
            <el-table-column label="最近提问" min-width="160">
              <template #default="{ row }">{{ formatDshTime(row.last_prompt_at) }}</template>
            </el-table-column>
            <el-table-column prop="session_id" label="会话 ID" min-width="200" show-overflow-tooltip />
          </el-table>
        </template>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.notify')" label="通知日志" name="notify-logs">
        <div class="toolbar" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
          <el-select v-model="notifyLogFilter.event_key" clearable placeholder="事件类型" style="width: 160px">
            <el-option v-for="(label, key) in EVENT_LABELS" :key="key" :label="label" :value="key" />
          </el-select>
          <el-select v-model="notifyLogFilter.channel" clearable placeholder="渠道" style="width: 120px">
            <el-option label="站内" value="inbox" />
            <el-option label="钉钉" value="dingtalk" />
          </el-select>
          <el-select v-model="notifyLogFilter.status" clearable placeholder="状态" style="width: 120px">
            <el-option label="成功" value="success" />
            <el-option label="跳过" value="skipped" />
            <el-option label="失败" value="failed" />
          </el-select>
          <el-button type="primary" @click="loadNotifyLogs">查询</el-button>
          <span style="color: #888; margin-left: 8px">共 {{ notifyLogsTotal }} 条</span>
        </div>
        <el-table :data="notifyLogs" v-loading="notifyLoading" max-height="520">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatDshTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="事件" width="120">
            <template #default="{ row }">{{ EVENT_LABELS[row.event_key] || row.event_key }}</template>
          </el-table-column>
          <el-table-column prop="user_name" label="接收人" width="100" />
          <el-table-column label="渠道" width="80">
            <template #default="{ row }">{{ CHANNEL_LABELS[row.channel] || row.channel }}</template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">{{ STATUS_LABELS[row.status] || row.status }}</template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="140" show-overflow-tooltip />
          <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
          <el-table-column prop="error_message" label="说明" min-width="120" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.notify')" label="通知模板" name="notify-templates">
        <div class="toolbar" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap">
          <el-button @click="runReminder('blocker')">手动扫：卡点逾期</el-button>
          <el-button @click="runReminder('phase')">手动扫：阶段逾期</el-button>
          <el-button @click="runReminder('weekly')">手动扫：周报未交</el-button>
        </div>
        <el-table :data="notifyTemplates">
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column label="事件" width="140">
            <template #default="{ row }">{{ EVENT_LABELS[row.event_key] || row.event_key }}</template>
          </el-table-column>
          <el-table-column label="站内" width="70">
            <template #default="{ row }">{{ row.channel_inbox ? '开' : '关' }}</template>
          </el-table-column>
          <el-table-column label="钉钉" width="70">
            <template #default="{ row }">{{ row.channel_dingtalk ? '开' : '关' }}</template>
          </el-table-column>
          <el-table-column label="启用" width="70">
            <template #default="{ row }">{{ row.is_enabled ? '是' : '否' }}</template>
          </el-table-column>
          <el-table-column prop="schedule_cron" label="定时(cron)" width="130" show-overflow-tooltip />
          <el-table-column prop="max_per_day" label="每日上限" width="90" />
          <el-table-column prop="title_template" label="标题模板" min-width="160" show-overflow-tooltip />
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openTplEdit(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="userStore.hasPermission('admin.audit')" label="审计日志" name="audit">
        <el-table :data="auditLogs">
          <el-table-column prop="created_at" label="时间" width="200" />
          <el-table-column prop="action" label="操作" width="120" />
          <el-table-column prop="resource" label="资源" width="100" />
          <el-table-column prop="resource_id" label="资源ID" width="80" />
          <el-table-column prop="user_id" label="操作人" width="80" />
          <el-table-column prop="detail" label="说明" min-width="240" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
    </el-tabs>
    </div>

    <el-dialog v-model="showTplEdit" :title="`编辑模板 - ${tplForm.name || ''}`" width="640px">
      <el-form label-width="110px">
        <el-form-item label="名称">
          <el-input v-model="tplForm.name" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="tplForm.description" />
        </el-form-item>
        <el-form-item label="标题模板">
          <el-input v-model="tplForm.title_template" />
        </el-form-item>
        <el-form-item label="正文模板">
          <el-input v-model="tplForm.body_template" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="渠道">
          <el-checkbox v-model="tplForm.channel_inbox">站内推送</el-checkbox>
          <el-checkbox v-model="tplForm.channel_dingtalk">钉钉工作通知</el-checkbox>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="tplForm.is_enabled" />
        </el-form-item>
        <el-form-item label="定时 cron">
          <el-input v-model="tplForm.schedule_cron" placeholder="如 0 9 * * * ；即时类留空" />
        </el-form-item>
        <el-form-item label="每日上限">
          <el-input-number v-model="tplForm.max_per_day" :min="0" :max="100" />
          <span style="margin-left: 8px; color: #888">0 表示不限制</span>
        </el-form-item>
        <el-form-item label="安静开始">
          <el-input v-model="tplForm.quiet_start" placeholder="HH:MM，如 22:00" style="width: 160px" />
        </el-form-item>
        <el-form-item label="安静结束">
          <el-input v-model="tplForm.quiet_end" placeholder="HH:MM，如 08:00" style="width: 160px" />
        </el-form-item>
        <p class="hint">占位符示例：{task_title} {actor_name} {project_name} {blocker_desc} 等，按事件不同可用字段不同。</p>
      </el-form>
      <template #footer>
        <el-button @click="showTplEdit = false">取消</el-button>
        <el-button type="primary" @click="saveTplEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showResetPwd" :title="`重置密码 - ${resetUsername}`" width="480px">
      <el-form label-width="90px">
        <el-form-item label="新密码" required>
          <el-input v-model="newPassword" type="password" show-password placeholder="请输入强密码" />
        </el-form-item>
      </el-form>
      <ul class="policy">
        <li v-for="(r, i) in PASSWORD_RULES" :key="i">{{ r }}</li>
      </ul>
      <template #footer>
        <el-button @click="showResetPwd = false">取消</el-button>
        <el-button type="primary" @click="submitResetPwd">确认重置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showDingBind" :title="`绑定钉钉 - ${dingUsername}`" width="520px">
      <p class="hint">
        从钉钉「政企市场部」通讯录中选择人员（可搜姓名/工号）。灰色项表示已被其他账号占用。清空选择后保存等于解绑。
      </p>
      <el-form label-width="110px">
        <el-form-item label="钉钉人员">
          <el-select
            v-model="dingTalkId"
            filterable
            clearable
            :loading="dingCandidatesLoading"
            placeholder="搜索姓名或工号"
            no-data-text="暂无人员，请检查钉钉通讯录权限"
            no-match-text="没有匹配的人员"
            style="width: 100%"
          >
            <el-option
              v-for="c in dingCandidates"
              :key="c.userid"
              :label="candidateLabel(c)"
              :value="c.userid"
              :disabled="!!c.bound_username"
            />
          </el-select>
          <p v-if="!dingCandidatesLoading" class="hint" style="margin-top: 8px">
            共 {{ dingCandidates.length }} 人，其中可绑定
            {{ dingCandidates.filter((c) => !c.bound_username).length }} 人
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDingBind = false">取消</el-button>
        <el-button type="primary" @click="submitDingBind">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showModelDialog"
      :title="editingModelId ? '编辑模型' : '添加模型'"
      width="560px"
    >
      <el-form label-width="110px">
        <el-form-item label="显示名称" required>
          <el-input v-model="modelForm.name" placeholder="例如 DeepSeek 聊天" />
        </el-form-item>
        <el-form-item label="API Base" required>
          <el-input v-model="modelForm.api_base" placeholder="例如 https://api.deepseek.com/v1" />
        </el-form-item>
        <el-form-item :label="editingModelId ? 'API Key（留空不改）' : 'API Key'" :required="!editingModelId">
          <el-input v-model="modelForm.api_key" type="password" show-password placeholder="密钥不会完整显示在列表里" />
        </el-form-item>
        <el-form-item label="模型名" required>
          <div class="model-name-row">
            <el-select
              v-model="modelForm.model_name"
              filterable
              allow-create
              default-first-option
              clearable
              placeholder="先获取列表，或直接输入模型 ID"
              style="flex: 1"
            >
              <el-option v-for="id in remoteModelIds" :key="id" :label="id" :value="id" />
            </el-select>
            <el-button :loading="discoveringModels" @click="discoverRemoteModels">获取模型列表</el-button>
          </div>
        </el-form-item>
        <el-form-item label="用途">
          <el-select v-model="modelForm.purpose" style="width:100%">
            <el-option label="通用" value="general" />
            <el-option label="周报辅导" value="report_coach" />
            <el-option label="周报结构化" value="report_structure" />
            <el-option label="周报评价" value="report_review" />
            <el-option label="方案打标" value="solution_tag" />
            <el-option label="任务草稿" value="task_draft" />
            <el-option label="任务拆解" value="task_breakdown" />
            <el-option label="任务润色" value="task_polish" />
            <el-option label="任务评价" value="task_review" />
            <el-option label="立项草稿" value="project_draft" />
            <el-option label="卡点草稿" value="blocker_draft" />
            <el-option label="总览摘要" value="overview_brief" />
            <el-option label="方案检索" value="solution_search" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="modelForm.is_enabled" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="modelForm.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showModelDialog = false">取消</el-button>
        <el-button type="primary" @click="submitModel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 8px 20px 20px;
  box-shadow: var(--wb-shadow-soft);
}
.policy { font-size: 12px; color: var(--wb-text-muted); padding-left: 18px; margin: 0; }
.policy li { line-height: 1.6; }
.hint { font-size: 13px; color: var(--wb-text-muted); margin: 0 0 12px; }
.hint.error { color: #c45656; }
.muted { color: #999; }
.model-name-row {
  display: flex;
  gap: 8px;
  width: 100%;
  align-items: center;
}
.dsh-usage-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}
.dsh-usage-subtitle {
  margin: 20px 0 10px;
  font-size: 14px;
  font-weight: 600;
}
.model-section-title {
  margin: 4px 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--wb-text, #303133);
}
</style>
