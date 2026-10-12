<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import TaskAssignPanel from '../components/TaskAssignPanel.vue'
import AgentNativeHint from '../components/AgentNativeHint.vue'
import { isAgentNativeProduct } from '../productLine'
import {
  PROJECT_TYPE_MAP,
  PROJECT_TYPE_HINTS,
  phasesForType,
} from '../constants/projectMeta'
import { pickManagerOptions, pickSupportOptions, withSelectedOptions } from '../utils/projectRoles'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()
const projectId = computed(() => Number(route.params.id))
const activeTab = ref('phases')
const project = ref<any>(null)
const phases = ref<any[]>([])
const projectTasks = ref<any[]>([])
const blockers = ref<any[]>([])
const members = ref<any[]>([])
const allUsers = ref<any[]>([])
const projectReportPeriods = ref<any[]>([])
const loading = ref(false)
/** 分层进度里折叠的阶段键：phase:{id} 或 unassigned */
const collapsedPhaseKeys = ref<Record<string, boolean>>({})
const savingPhaseId = ref<number | null>(null)

const leadId = ref<number | null>(null)
const supportIds = ref<number[]>([])
const assistantIds = ref<number[]>([])


const canEditProject = computed(() => {
  if (!project.value) return false
  return userStore.canEditOwnedProject(project.value.owner?.id)
})
const canEditPhases = computed(
  () => canEditProject.value || userStore.hasPermission('phase.edit'),
)
const canDeleteProject = computed(() => userStore.hasPermission('project.delete'))
const canChangeLevel = computed(() => userStore.hasPermission('project.level.change'))
const showEdit = ref(false)
const savingEdit = ref(false)
const editForm = ref({
  name: '',
  project_type: 'software',
  current_phase: '',
  status: 'normal',
  level: 'normal',
  description: '',
})

const editPhaseOptions = computed(() => {
  const typed = phasesForType(editForm.value.project_type)
  const cur = (editForm.value.current_phase || '').trim()
  if (cur && !typed.includes(cur)) return [...typed, cur]
  return typed
})

const statusMap: Record<string, string> = {
  following: '跟进中', pending_decision: '待决策', escalated: '已升级', resolved: '已解决',
}
const phaseStatusMap: Record<string, string> = {
  not_started: '未开始', in_progress: '进行中', completed: '已完成',
}

function phaseCellClass(status: string) {
  if (status === 'completed') return 'is-done'
  if (status === 'in_progress') return 'is-doing'
  return 'is-todo'
}

const taskStatusMap: Record<string, string> = {
  not_started: '未开始', in_progress: '进行中', completed: '已完成',
}

function todayIso() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/** 相对进度条宽度：完成 100%；有截止日按距今天远近；否则按状态给固定宽度。 */
function progressWidth(item: { status?: string; due_date?: string | null }) {
  if (item.status === 'completed') return 100
  if (item.due_date) {
    const today = todayIso()
    if (item.due_date < today) return 92
    if (item.due_date === today) return 70
    return item.status === 'in_progress' ? 45 : 28
  }
  if (item.status === 'in_progress') return 55
  return 22
}

function progressBarClass(item: { status?: string; due_date?: string | null }) {
  if (item.status === 'completed') return 'bar-done'
  if (item.due_date && item.due_date < todayIso() && item.status !== 'completed') return 'bar-late'
  if (item.status === 'in_progress') return 'bar-doing'
  return 'bar-todo'
}

const phaseHierarchy = computed(() => {
  const tasks = projectTasks.value || []
  const byPhase = new Map<number, any[]>()
  const unassigned: any[] = []
  for (const task of tasks) {
    if (task.phase_id != null) {
      const list = byPhase.get(task.phase_id) || []
      list.push(task)
      byPhase.set(task.phase_id, list)
    } else {
      unassigned.push(task)
    }
  }
  const groups = (phases.value || []).map((phase, index) => ({
    key: `phase:${phase.id}`,
    index: index + 1,
    phase,
    tasks: byPhase.get(phase.id) || [],
  }))
  if (unassigned.length || !(phases.value || []).length) {
    groups.push({
      key: 'unassigned',
      index: groups.length + 1,
      phase: {
        id: null,
        name: '未归入阶段',
        status: 'not_started',
        planned_end_date: null,
        is_overdue: false,
      },
      tasks: unassigned,
    })
  }
  return groups
})

function isPhaseCollapsed(key: string) {
  return !!collapsedPhaseKeys.value[key]
}

function togglePhase(key: string) {
  collapsedPhaseKeys.value = {
    ...collapsedPhaseKeys.value,
    [key]: !collapsedPhaseKeys.value[key],
  }
}

const projectStatusMap: Record<string, string> = {
  normal: '正常', at_risk: '有风险', blocked: '已阻塞',
}

const blockerGroups = computed(() => {
  const groups: Record<string, any[]> = { following: [], pending_decision: [], escalated: [], resolved: [] }
  for (const b of blockers.value) {
    groups[b.status]?.push(b)
  }
  return groups
})

const showBlockerDialog = ref(false)
const blockerAiText = ref('')
const blockerAiLoading = ref(false)
const blockerForm = ref({
  description: '', responsible_party: '', party_type: 'internal',
  actions_taken: '', next_action: '', next_follow_up: '',
})

const canManageMembers = computed(() => userStore.hasPermission('project.members'))

const managerOptions = computed(() => {
  const fromCandidates = withSelectedOptions(
    pickManagerOptions(allUsers.value),
    [leadId.value],
    allUsers.value,
  )
  // 成员表里已有的负责人若不在候选里，也补上以便显示姓名
  const memberUsers = members.value.map((m: any) => m.user).filter(Boolean)
  return withSelectedOptions(fromCandidates, [leadId.value], memberUsers)
})
const supportOptions = computed(() => {
  const fromCandidates = withSelectedOptions(
    pickSupportOptions(allUsers.value),
    supportIds.value,
    allUsers.value,
  )
  const memberUsers = members.value.map((m: any) => m.user).filter(Boolean)
  return withSelectedOptions(fromCandidates, supportIds.value, memberUsers)
})

function typeLabel(t: string) {
  return PROJECT_TYPE_MAP[t] || t || '—'
}

function openEdit() {
  if (!project.value) return
  const ptype = project.value.project_type || 'software'
  const defaults = phasesForType(ptype)
  editForm.value = {
    name: project.value.name || '',
    project_type: ptype,
    current_phase: project.value.current_phase || defaults[0] || '',
    status: project.value.status || 'normal',
    level: project.value.level || 'normal',
    description: project.value.description || '',
  }
  showEdit.value = true
}

async function setPhaseStatus(phase: { id: number; name: string; status: string }, status: string) {
  if (!canEditPhases.value || !phase?.id) return
  if (phase.status === status) return
  savingPhaseId.value = phase.id
  try {
    await api.patch(`/projects/phases/${phase.id}`, { status })
    ElMessage.success(`已将「${phase.name}」设为${phaseStatusMap[status] || status}`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '更新阶段失败')
  } finally {
    savingPhaseId.value = null
  }
}

async function saveEdit() {
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
    await api.patch(`/projects/${projectId.value}`, {
      name,
      project_type: editForm.value.project_type,
      current_phase: editForm.value.current_phase || null,
      status: editForm.value.status,
      description: editForm.value.description || null,
    })
    if (canChangeLevel.value && editForm.value.level !== project.value.level) {
      await api.patch(`/projects/${projectId.value}/level`, { level: editForm.value.level })
    }
    ElMessage.success('项目信息已保存')
    showEdit.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    savingEdit.value = false
  }
}

async function removeProject() {
  if (!project.value || !canDeleteProject.value) return
  if (project.value.name === '日常任务') {
    ElMessage.warning('系统内置项目「日常任务」不可删除')
    return
  }
  let password = ''
  try {
    const res = await ElMessageBox.prompt(
      `将永久删除项目「${project.value.name}」及其阶段、任务、卡点等关联数据，不能恢复。请输入你的登录密码确认。`,
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
    await api.delete(`/projects/${projectId.value}`, { data: { password } })
    ElMessage.success('项目已删除')
    router.push('/overview')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function load() {
  loading.value = true
  try {
    const [p, ph, bl, mem, tasks] = await Promise.all([
      api.get(`/projects/${projectId.value}`),
      api.get(`/projects/${projectId.value}/phases`),
      api.get(`/blockers/project/${projectId.value}`),
      api.get(`/projects/${projectId.value}/members`),
      api.get(`/projects/${projectId.value}/tasks`),
    ])
    project.value = p.data
    phases.value = ph.data
    projectTasks.value = tasks.data || []
    blockers.value = bl.data
    members.value = mem.data
    leadId.value = mem.data.find((m: any) => m.member_role === 'lead')?.user?.id ?? null
    supportIds.value = mem.data
      .filter((m: any) => m.member_role === 'support')
      .map((m: any) => m.user.id)
    assistantIds.value = mem.data
      .filter((m: any) => m.member_role === 'assistant')
      .map((m: any) => m.user.id)

    // 首屏关键数据就绪即解除 loading；候选人与周报不挡主内容
    loading.value = false

    if (canManageMembers.value) {
      try {
        const { data } = await api.get('/projects/role-candidates')
        allUsers.value = data
      } catch {
        allUsers.value = mem.data.map((m: any) => m.user)
      }
    } else {
      allUsers.value = []
    }

    if (userStore.hasPermission('report.view.all')) {
      try {
        const { data } = await api.get(`/progress-reports/by-project/${projectId.value}`)
        projectReportPeriods.value = data.periods || []
      } catch {
        projectReportPeriods.value = []
      }
    } else {
      projectReportPeriods.value = []
    }
  } catch (e) {
    loading.value = false
    throw e
  }
}

function periodLabel(p: { period_start?: string; period_end?: string }) {
  const start = p.period_start || ''
  const end = p.period_end || start
  if (!start) return '未知周期'
  return start === end ? start : `${start} ~ ${end}`
}

function reasonLabel(reason: string) {
  if (reason === 'explicit') return '已挂项目'
  if (reason === 'inferred') return '推断归属'
  if (reason === 'task_linked') return '任务关联'
  return reason
}

function reasonTagType(reason: string) {
  if (reason === 'explicit') return 'success'
  if (reason === 'inferred') return 'warning'
  if (reason === 'task_linked') return 'info'
  return 'info'
}

async function submitBlocker() {
  await api.post(`/blockers/project/${projectId.value}`, blockerForm.value)
  ElMessage.success('卡点已添加')
  showBlockerDialog.value = false
  blockerAiText.value = ''
  blockerForm.value = {
    description: '', responsible_party: '', party_type: 'internal',
    actions_taken: '', next_action: '', next_follow_up: '',
  }
  load()
}

async function runBlockerAiDraft() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const text = blockerAiText.value.trim() || blockerForm.value.description.trim()
  if (!text) {
    ElMessage.warning('先写一句卡点情况')
    return
  }
  blockerAiLoading.value = true
  try {
    const { data } = await api.post('/ai/blocker/draft', {
      text,
      project_id: projectId.value,
    })
    blockerForm.value = {
      description: data.description || text,
      responsible_party: data.responsible_party || '',
      party_type: data.party_type || 'internal',
      actions_taken: data.actions_taken || '',
      next_action: data.next_action || '',
      next_follow_up: data.next_follow_up || '',
    }
    ElMessage.success('已生成卡点草稿，确认后保存')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || 'AI 草稿失败')
  } finally {
    blockerAiLoading.value = false
  }
}

async function saveMembers() {
  if (!supportIds.value.length) {
    ElMessage.warning('请至少选择一名项目支撑')
    return
  }
  if (leadId.value != null && supportIds.value.includes(leadId.value)) {
    ElMessage.warning('项目经理和项目支撑不能是同一个人')
    return
  }
  try {
    await api.put(`/projects/${projectId.value}/members`, {
      lead_user_id: leadId.value,
      support_user_ids: supportIds.value,
      assistant_user_ids: assistantIds.value.filter(
        (id) => id !== leadId.value && !supportIds.value.includes(id),
      ),
      member_user_ids: [],
    })
    ElMessage.success('成员角色已保存')
    load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  }
}

function roleLabel(role: string) {
  if (role === 'lead') return '项目经理'
  if (role === 'support') return '项目支撑'
  if (role === 'assistant') return '辅助人'
  return '成员'
}

function memberName(role: string) {
  return members.value.find((m) => m.member_role === role)?.user?.name || ''
}

function memberNames(role: string) {
  return members.value
    .filter((m) => m.member_role === role)
    .map((m) => m.user?.name)
    .filter(Boolean)
    .join('、')
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="page">
    <button type="button" class="back-link" @click="$router.push('/overview')">← 返回项目管理</button>
    <div class="title-row">
      <h1 class="wb-page-title">{{ project?.name || '项目详情' }}</h1>
      <div class="title-actions">
        <el-button v-if="canEditProject && project" type="primary" plain @click="openEdit">
          修正项目信息
        </el-button>
        <el-button v-if="canDeleteProject && project" type="danger" plain @click="removeProject">
          删除项目
        </el-button>
      </div>
    </div>
    <p v-if="project" class="wb-page-desc">
      类型 {{ typeLabel(project.project_type) }}
      · 项目经理 {{ memberName('lead') || '未指定' }}
      · 项目支撑 {{ memberNames('support') || '未指定' }}
      · 当前阶段 {{ project.current_phase || '—' }}
      · 状态 {{ projectStatusMap[project.status] || project.status }}
    </p>

    <div v-if="project" class="meta-row">
      <span
        class="wb-tag"
        :class="project.level === 'key' || project.level === 'red' || project.level === 'yellow' ? 'wb-tag--yellow' : 'wb-tag--green'"
      >
        {{ project.level === 'key' || project.level === 'red' || project.level === 'yellow' ? '重点' : project.level === 'daily' ? '日常' : '普通' }}
      </span>
    </div>

    <div class="panel tabs-panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="进度阶段" name="phases">
          <p v-if="canEditPhases" class="note phase-edit-hint">点击阶段格子可推进进度：未开始 / 进行中 / 已完成。</p>
          <div v-if="phases.length" class="phase-bar">
            <el-dropdown
              v-for="p in phases"
              :key="p.id"
              trigger="click"
              :disabled="!canEditPhases || savingPhaseId === p.id"
              @command="(cmd: string | number | object) => setPhaseStatus(p, String(cmd))"
            >
              <div
                class="phase-cell"
                :class="[phaseCellClass(p.status), { editable: canEditPhases }]"
              >
                <div class="phase-name">{{ p.name }}</div>
                <div class="phase-state">{{ phaseStatusMap[p.status] || p.status }}</div>
                <div v-if="p.planned_end_date" class="phase-date" :class="{ overdue: p.is_overdue }">
                  计划完成 {{ p.planned_end_date }}
                  <span v-if="p.is_overdue">已逾期</span>
                </div>
                <div v-if="p.note" class="note">{{ p.note }}</div>
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="not_started" :disabled="p.status === 'not_started'">
                    未开始
                  </el-dropdown-item>
                  <el-dropdown-item command="in_progress" :disabled="p.status === 'in_progress'">
                    进行中
                  </el-dropdown-item>
                  <el-dropdown-item command="completed" :disabled="p.status === 'completed'">
                    已完成
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <p v-else class="note">暂无阶段记录。新建项目会自动带上规范阶段。</p>

          <div class="phase-plan">
            <div class="phase-plan-head">阶段与任务</div>
            <div v-for="group in phaseHierarchy" :key="group.key" class="phase-group">
              <button type="button" class="phase-group-row" @click="togglePhase(group.key)">
                <span class="collapse-arrow" aria-hidden="true">
                  {{ isPhaseCollapsed(group.key) ? '▶' : '▼' }}
                </span>
                <span class="phase-group-title">
                  {{ group.index }} {{ group.phase.name }}
                </span>
                <span class="phase-group-meta">
                  {{ phaseStatusMap[group.phase.status] || '—' }}
                  · {{ group.tasks.length }} 项任务
                </span>
                <span class="phase-group-bar-wrap" aria-hidden="true">
                  <span
                    class="phase-group-bar"
                    :class="progressBarClass(group.phase)"
                    :style="{ width: `${progressWidth(group.phase)}%` }"
                  ></span>
                </span>
              </button>
              <div v-show="!isPhaseCollapsed(group.key)" class="phase-task-list">
                <button
                  v-for="(task, ti) in group.tasks"
                  :key="task.id"
                  type="button"
                  class="phase-task-row"
                  @click="router.push(`/tasks/${task.id}`)"
                >
                  <span class="phase-task-title">
                    {{ group.index }}.{{ ti + 1 }} {{ task.title }}
                  </span>
                  <span class="phase-task-meta">
                    {{ task.assignee?.name || '—' }}
                    · {{ taskStatusMap[task.status] || task.status }}
                    <template v-if="task.due_date"> · {{ task.due_date }}</template>
                  </span>
                  <span class="phase-group-bar-wrap" aria-hidden="true">
                    <span
                      class="phase-group-bar"
                      :class="progressBarClass(task)"
                      :style="{ width: `${progressWidth(task)}%` }"
                    ></span>
                  </span>
                </button>
                <p v-if="!group.tasks.length" class="empty-line">此阶段暂无任务</p>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="任务待办" name="tasks">
          <TaskAssignPanel mode="project" :project-id="projectId" />
        </el-tab-pane>

        <el-tab-pane label="卡点记录" name="blockers">
          <el-button type="primary" @click="showBlockerDialog = true" style="margin-bottom:16px">新增卡点</el-button>
          <div v-for="(items, status) in blockerGroups" :key="status">
            <h3 v-if="items.length" class="group-title">{{ statusMap[status] }}（{{ items.length }}）</h3>
            <el-card v-for="b in items" :key="b.id" class="blocker-card" shadow="never">
              <div class="blocker-title">{{ b.description }}</div>
              <el-descriptions :column="2" size="small" style="margin-top:8px">
                <el-descriptions-item label="责任方">{{ b.responsible_party }}</el-descriptions-item>
                <el-descriptions-item label="跟进人">{{ b.assignee.name }}</el-descriptions-item>
                <el-descriptions-item label="已做动作" :span="2">{{ b.actions_taken }}</el-descriptions-item>
                <el-descriptions-item label="下一步">{{ b.next_action }}</el-descriptions-item>
                <el-descriptions-item label="下次跟进">
                  <span :class="{ overdue: b.is_follow_up_overdue }">{{ b.next_follow_up }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="主责确认">
                  {{ b.lead_acked_at ? '已确认' : '未确认' }}
                </el-descriptions-item>
              </el-descriptions>
            </el-card>
          </div>
        </el-tab-pane>

        <el-tab-pane label="周报" name="reports">
          <div v-if="!userStore.hasPermission('report.view.all')" class="note">
            请到「周报管理」提交；管理者可在同页「看周报/日报」查看汇总。
          </div>
          <div v-else-if="!projectReportPeriods.length" class="note">暂无该项目相关周报</div>
          <div v-else class="report-timeline">
            <div
              v-for="period in projectReportPeriods"
              :key="period.period_start"
              class="report-period"
            >
              <div class="period-node">
                <span class="period-dot" />
                <span class="period-label">{{ periodLabel(period) }}</span>
              </div>
              <div class="period-body">
                <div v-for="r in period.reports" :key="r.id" class="report-block">
                  <strong>{{ r.author.name }}</strong> · {{ r.theme || '无主题' }}
                  <div
                    v-for="it in r.items"
                    :key="it.id"
                    class="report-item"
                  >
                    <div class="reason-tags">
                      <el-tag
                        v-for="reason in (it.match_reasons || [])"
                        :key="reason"
                        size="small"
                        effect="plain"
                        :type="reasonTagType(reason)"
                      >{{ reasonLabel(reason) }}</el-tag>
                    </div>
                    <div v-if="!it.no_action">
                      <div v-if="it.task_title" class="item-title">{{ it.task_title }}</div>
                      做成了：{{ it.result_text }}
                      <div>下一步：{{ it.next_goal }}（{{ it.next_due_date }}）</div>
                    </div>
                    <div v-else class="note">无实质动作：{{ it.task_title || '—' }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="成员角色" name="members">
          <el-table :data="members" size="small" style="margin-bottom:16px">
            <el-table-column label="姓名">
              <template #default="{ row }">{{ row.user.name }}</template>
            </el-table-column>
            <el-table-column label="角色">
              <template #default="{ row }">{{ roleLabel(row.member_role) }}</template>
            </el-table-column>
          </el-table>

          <template v-if="canManageMembers">
            <el-form label-width="100px" style="max-width:560px">
              <el-form-item label="项目经理">
                <el-select
                  v-model="leadId"
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
                  v-model="supportIds"
                  multiple
                  filterable
                  style="width:100%"
                  placeholder="可多选"
                >
                  <el-option v-for="u in supportOptions" :key="u.id" :label="u.name" :value="u.id" />
                </el-select>
                <p v-if="!supportOptions.length" class="note">
                  除许金斌、周永祺（项目经理）和孙振振（管理员）外，其他同事都可作为项目支撑。若列表为空，请先在系统管理同步钉钉通讯录。
                </p>
              </el-form-item>
              <el-form-item label="辅助人">
                <el-select v-model="assistantIds" multiple filterable style="width:100%" placeholder="可不选">
                  <el-option v-for="u in allUsers" :key="u.id" :label="u.name" :value="u.id" />
                </el-select>
              </el-form-item>
              <el-button type="primary" @click="saveMembers">保存角色</el-button>
            </el-form>
          </template>
          <p v-else class="note">需要「管理项目成员角色」权限才能修改。</p>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="showEdit" title="修正项目信息" width="520px">
      <p class="note edit-hint">可手工改立项名称、类型与当前阶段。运维偏技术底层；运营偏平台录入/账号/分析。</p>
      <el-form label-width="96px">
        <el-form-item label="项目名称" required>
          <el-input v-model="editForm.name" maxlength="128" placeholder="与真实立项名称一致" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editForm.project_type" style="width:100%">
            <el-option
              v-for="(label, value) in PROJECT_TYPE_MAP"
              :key="value"
              :label="label"
              :value="value"
            >
              <span>{{ label }}</span>
              <span v-if="PROJECT_TYPE_HINTS[value]" class="opt-hint"> · {{ PROJECT_TYPE_HINTS[value] }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="当前阶段">
          <el-select v-model="editForm.current_phase" filterable allow-create style="width:100%">
            <el-option v-for="p in editPhaseOptions" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width:100%">
            <el-option v-for="(l, v) in projectStatusMap" :key="v" :label="l" :value="v" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="canChangeLevel" label="分级">
          <el-select v-model="editForm.level" style="width:100%">
            <el-option label="普通" value="normal" />
            <el-option label="重点" value="key" />
            <el-option label="日常" value="daily" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showBlockerDialog" title="新增卡点" width="560px">
      <AgentNativeHint v-if="agentNative" tip="记卡点请到「我的助手」里说，确认后再写入账本。" />
      <div v-else class="ai-box" style="margin-bottom:12px">
        <el-input
          v-model="blockerAiText"
          type="textarea"
          :rows="2"
          placeholder="一句话描述卡点（可选），再点 AI 填表"
        />
        <el-button
          type="primary"
          plain
          size="small"
          style="margin-top:8px"
          :loading="blockerAiLoading"
          @click="runBlockerAiDraft"
        >
          AI 整理成表
        </el-button>
      </div>
      <el-form label-width="100px">
        <el-form-item label="卡点描述" required><el-input v-model="blockerForm.description" type="textarea" /></el-form-item>
        <el-form-item label="责任方" required><el-input v-model="blockerForm.responsible_party" /></el-form-item>
        <el-form-item label="卡点方">
          <el-select v-model="blockerForm.party_type">
            <el-option label="第三方" value="third_party" /><el-option label="客户" value="customer" />
            <el-option label="内部" value="internal" /><el-option label="审批" value="approval" />
          </el-select>
        </el-form-item>
        <el-form-item label="已做动作" required><el-input v-model="blockerForm.actions_taken" type="textarea" /></el-form-item>
        <el-form-item label="下一步" required><el-input v-model="blockerForm.next_action" /></el-form-item>
        <el-form-item label="下次跟进" required>
          <el-date-picker v-model="blockerForm.next_follow_up" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showBlockerDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBlocker">确认保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { max-width: 1200px; }
.ai-box {
  background: #f7faf8;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  padding: 12px;
}
.back-link {
  font-family: var(--wb-font);
  font-size: 13px;
  color: var(--wb-text-muted);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  margin-bottom: 12px;
}
.back-link:hover { color: var(--wb-text-primary); }
.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.title-row .wb-page-title { margin-bottom: 0; }
.title-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.meta-row { margin-bottom: 20px; }
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 20px 24px;
  box-shadow: var(--wb-shadow-soft);
}
.tabs-panel :deep(.el-tabs__header) { margin-bottom: 20px; }
.phase-bar {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.phase-edit-hint {
  margin: 0 0 10px;
}
.phase-cell {
  flex: 1 0 108px;
  min-width: 108px;
  border-radius: 10px;
  padding: 12px 10px;
}
.phase-cell.editable {
  cursor: pointer;
  outline: none;
}
.phase-cell.editable:hover {
  box-shadow: 0 0 0 2px rgba(61, 153, 112, 0.35);
}
.phase-bar :deep(.el-dropdown) {
  flex: 1 0 108px;
  min-width: 108px;
}
.phase-bar :deep(.el-dropdown .phase-cell) {
  width: 100%;
}
.phase-cell.is-done {
  background: var(--wb-bg-active);
  color: #3d9970;
}
.phase-cell.is-doing {
  background: var(--wb-accent);
  color: #fff;
}
.phase-cell.is-todo {
  background: #f5f5f5;
  color: #888;
}
.phase-name {
  font-weight: 600;
  font-size: 13px;
}
.phase-state,
.phase-date {
  margin-top: 4px;
  font-size: 12px;
}
.phase-cell .note {
  color: inherit;
}
.phase-cell .overdue {
  color: #c45656;
}
.phase-plan {
  margin-top: 20px;
  border-top: 1px solid var(--wb-border);
  padding-top: 16px;
}
.phase-plan-head {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}
.phase-group {
  margin-bottom: 8px;
}
.phase-group-row,
.phase-task-row {
  display: grid;
  grid-template-columns: 18px minmax(140px, 1.2fr) minmax(120px, 1fr) minmax(120px, 1fr);
  gap: 10px;
  align-items: center;
  width: 100%;
  border: 1px solid var(--wb-border);
  background: #fff;
  border-radius: 10px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.phase-group-row {
  background: #f7faf8;
  font-weight: 600;
}
.phase-task-row {
  margin-top: 6px;
  margin-left: 18px;
  width: calc(100% - 18px);
}
.phase-group-row:hover,
.phase-task-row:hover {
  border-color: #9ecfb8;
}
.collapse-arrow {
  font-size: 11px;
  color: var(--wb-text-muted, #999);
}
.phase-group-title,
.phase-task-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.phase-group-meta,
.phase-task-meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--wb-text-muted, #999);
}
.phase-group-bar-wrap {
  display: block;
  height: 8px;
  border-radius: 99px;
  background: #f0f0f0;
  overflow: hidden;
}
.phase-group-bar {
  display: block;
  height: 100%;
  border-radius: 99px;
}
.phase-group-bar.bar-done {
  background: var(--wb-accent);
}
.phase-group-bar.bar-doing {
  background: #3d9970;
}
.phase-group-bar.bar-todo {
  background: #c8c8c8;
}
.phase-group-bar.bar-late {
  background: #c45656;
}
.phase-task-list {
  margin-bottom: 4px;
}
.empty-line {
  margin: 8px 0 0 28px;
  font-size: 13px;
  color: var(--wb-text-muted, #999);
}
.blocker-card { margin-bottom: 12px; border: 1px solid var(--wb-border); }
.group-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--wb-text-secondary);
  margin: 16px 0 12px;
}
.note { color: var(--wb-text-secondary); font-size: 13px; margin-top: 4px; }
.edit-hint { margin: 0 0 12px; }
.opt-hint { color: #999; font-size: 12px; }
.blocker-title { font-weight: 600; font-size: 15px; color: var(--wb-text-primary); }
.overdue { color: #c45656; font-weight: 500; }
.report-block { margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--wb-border); }
.report-item { font-size: 13px; margin-top: 6px; color: #444; }
.report-timeline { margin-top: 4px; }
.report-period { position: relative; padding-left: 18px; margin-bottom: 18px; }
.report-period::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 18px;
  bottom: 0;
  width: 2px;
  background: var(--wb-border, #e5e5e5);
}
.report-period:last-child::before { display: none; }
.period-node {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px -18px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-text-primary, #333);
}
.period-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--wb-accent, #3d9970);
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px var(--wb-accent, #3d9970);
  flex-shrink: 0;
}
.period-label { letter-spacing: 0.02em; }
.period-body { padding-left: 4px; }
.reason-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
.inferred-tag {
  display: inline-block;
  font-size: 11px;
  color: #8a6d3b;
  background: #fff7e8;
  border-radius: 4px;
  padding: 1px 6px;
  margin-bottom: 4px;
}
.item-title { font-weight: 600; margin-bottom: 2px; }
</style>
