<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { formatDateTime } from '../utils/datetime'
import { isAgentNativeProduct } from '../productLine'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()

const loading = ref(false)
const detail = ref<any | null>(null)
const taskReview = ref<any | null>(null)
const taskReviewLoading = ref(false)
const breakdownLoading = ref(false)
const checklistDraft = ref<Array<{ title: string; done: boolean }>>([])
const newCheckItem = ref('')
const members = ref<any[]>([])
const phases = ref<Array<{ id: number; name: string }>>([])
const phasePick = ref<number | null>(null)
const assistPick = ref<number | null>(null)
const ownerPick = ref<number | null>(null)
const attachments = ref<any[]>([])
const attachUploading = ref(false)
const savingBasics = ref(false)
const polishingGoal = ref(false)
const polishingResult = ref(false)

const statusMap: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
const priorityMap: Record<string, string> = {
  p0: 'P0',
  p1: 'P1',
  p2: 'P2',
  p3: 'P3',
  p4: 'P4',
}
const eventLabel: Record<string, string> = {
  created: '创建任务',
  created_from_report: '从周报创建',
  updated_from_report: '从周报更新',
  owner_changed: '更换负责人',
  assistant_added: '添加辅助人',
  assistant_removed: '移除辅助人',
  status_changed: '状态变更',
  result_updated: '更新内容',
  priority_set: '设定级别',
  priority_changed: '调整级别',
  accepted: '已接收',
  reminded: '催办',
  checklist_updated: '更新清单',
  attachment_added: '上传附件',
  attachment_removed: '删除附件',
}

const canAssign = computed(() => userStore.hasPermission('task.assign'))
const canChangeLevel = computed(() => userStore.hasPermission('task.level.change'))

const memberOptions = computed(() =>
  members.value.map((m: any) => ({
    id: m.user?.id ?? m.id,
    name: m.user?.name ?? m.name,
  })),
)

const isPending = computed(
  () =>
    detail.value?.assignment_type === 'delegated' &&
    (detail.value?.acceptance_status || 'accepted') === 'pending',
)

const canAccept = computed(
  () =>
    !!detail.value &&
    isPending.value &&
    detail.value.assignee?.id === userStore.user?.id,
)

const canRemind = computed(
  () =>
    !!detail.value &&
    isPending.value &&
    (detail.value.created_by?.id === userStore.user?.id || canAssign.value),
)

const canDelete = computed(
  () =>
    !!detail.value &&
    (detail.value.created_by?.id === userStore.user?.id ||
      userStore.hasRole('admin') ||
      userStore.hasRole('dept_head')),
)

const canManageAssist = computed(() => {
  if (!detail.value || !userStore.user) return false
  return detail.value.assignee?.id === userStore.user.id || canAssign.value
})

const taskId = computed(() => Number(route.params.id))
/** 详情加载代数：切换任务时丢弃过期的并行请求结果 */
let loadSeq = 0

function goBack() {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/personal')
  }
}

async function fetchMembers(pid: number) {
  const { data } = await api.get(`/projects/${pid}/members`)
  return data
}

async function fetchPhases(pid: number) {
  try {
    const { data } = await api.get(`/projects/${pid}/phases`)
    return (data || []).map((p: any) => ({ id: p.id, name: p.name }))
  } catch {
    return [] as Array<{ id: number; name: string }>
  }
}

async function fetchTaskReview(id: number) {
  try {
    const { data } = await api.get(`/tasks/${id}/review`)
    return data
  } catch {
    return null
  }
}

async function fetchAttachments(id: number) {
  try {
    const { data } = await api.get(`/tasks/${id}/attachments`)
    return data || []
  } catch {
    return []
  }
}

async function loadAttachments(id: number) {
  attachments.value = await fetchAttachments(id)
}

async function loadDetail() {
  const id = taskId.value
  if (!id || Number.isNaN(id)) {
    detail.value = null
    return
  }
  const seq = ++loadSeq
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H1',location:'TaskDetailView.vue:loadDetail:start',message:'loadDetail start',data:{seq,id,loadSeq},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  loading.value = true
  try {
    const { data } = await api.get(`/tasks/${id}`)
    if (seq !== loadSeq) {
      // #region agent log
      fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H1',location:'TaskDetailView.vue:loadDetail:stale-main',message:'discard stale main response',data:{seq,loadSeq,id,responseId:data?.id},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      return
    }
    detail.value = data
    checklistDraft.value = Array.isArray(data.checklist)
      ? data.checklist.map((c: any) => ({
          title: c.title || '',
          done: !!c.done,
        }))
      : []
    taskReview.value = null
    attachments.value = []
    assistPick.value = null
    ownerPick.value = null
    phasePick.value = data.phase_id ?? null
    // 主信息先出，去掉整页等待
    loading.value = false
    taskReviewLoading.value = true
    let atts: any[] = []
    let review: any | null = null
    let mems: any[] | null = null
    let phs: Array<{ id: number; name: string }> | null = null
    if (data.project_id) {
      ;[atts, review, mems, phs] = (await Promise.all([
        fetchAttachments(data.id),
        fetchTaskReview(data.id),
        fetchMembers(data.project_id),
        fetchPhases(data.project_id),
      ])) as [any[], any | null, any[], Array<{ id: number; name: string }>]
    } else {
      phases.value = []
      ;[atts, review] = (await Promise.all([
        fetchAttachments(data.id),
        fetchTaskReview(data.id),
      ])) as [any[], any | null]
    }
    if (seq !== loadSeq) {
      // #region agent log
      fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H1',location:'TaskDetailView.vue:loadDetail:stale-secondary',message:'discard stale secondary',data:{seq,loadSeq,expectedId:data.id},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      return
    }
    attachments.value = atts || []
    taskReview.value = review
    taskReviewLoading.value = false
    if (mems) members.value = mems
    if (phs) phases.value = phs
    // #region agent log
    fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H1',location:'TaskDetailView.vue:loadDetail:applied',message:'applied secondary for current seq',data:{seq,id:data.id,attachCount:(atts||[]).length},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  } catch (e: any) {
    if (seq !== loadSeq) return
    detail.value = null
    taskReviewLoading.value = false
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载任务失败')
    loading.value = false
  }
}

async function acceptTask() {
  if (!detail.value) return
  const { data } = await api.post(`/tasks/${detail.value.id}/accept`)
  detail.value = data
  ElMessage.success('已接收，可以更新进度了')
}

async function remindTask() {
  if (!detail.value) return
  const { data } = await api.post(`/tasks/${detail.value.id}/remind`)
  detail.value = data
  ElMessage.success('已催办责任人')
}

async function removeTask() {
  if (!detail.value || !canDelete.value) return
  let password = ''
  try {
    const res = await ElMessageBox.prompt(
      '删除后不能恢复。请输入你的登录密码确认。',
      '删除任务',
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
    await api.delete(`/tasks/${detail.value.id}`, { data: { password } })
    ElMessage.success('任务已删除')
    goBack()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function saveBasics() {
  if (!detail.value) return
  const title = (detail.value.title || '').trim()
  if (!title) {
    ElMessage.warning('标题不能为空')
    return
  }
  savingBasics.value = true
  try {
    const { data } = await api.patch(`/tasks/${detail.value.id}`, {
      title,
      goal: detail.value.goal || '',
      phase_id: phasePick.value || undefined,
      clear_phase_id: !phasePick.value,
    })
    detail.value = data
    phasePick.value = data.phase_id ?? null
    ElMessage.success('标题、目标与阶段已保存')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    savingBasics.value = false
  }
}

async function polishField(field: 'goal' | 'result_text') {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  if (!detail.value) return
  if (field === 'result_text' && isPending.value) {
    ElMessage.warning('请先接收任务后再润色结果')
    return
  }
  const loadingRef = field === 'goal' ? polishingGoal : polishingResult
  loadingRef.value = true
  try {
    const { data } = await api.post(`/tasks/${detail.value.id}/ai/polish`, {
      field,
      text: detail.value[field] || '',
    })
    const polished = (data?.text || '').trim()
    if (!polished) {
      ElMessage.warning('AI 未返回可用内容')
      return
    }
    detail.value[field] = polished
    const src = data?.source === 'llm' ? 'AI' : '规则'
    ElMessage.success(`已润色（${src}），请确认后保存`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '润色失败')
  } finally {
    loadingRef.value = false
  }
}

async function setStatus(status: string) {
  if (!detail.value) return
  if (isPending.value) {
    ElMessage.warning('请先接收任务后再更新进度')
    return
  }
  let result_text = detail.value.result_text || ''
  if (status === 'completed' && !result_text.trim()) {
    ElMessage.warning('完成前请先填写结果说明')
    return
  }
  if (status === 'completed' && !(attachments.value || []).length) {
    try {
      await ElMessageBox.confirm(
        '建议上传证明材料或输出文档，也可以跳过直接完成。',
        '完成任务',
        {
          type: 'info',
          confirmButtonText: '仍要完成',
          cancelButtonText: '先去上传',
          distinguishCancelAndClose: true,
        },
      )
    } catch (action) {
      if (action === 'cancel') return
      return
    }
  }
  try {
    const { data } = await api.patch(`/tasks/${detail.value.id}`, { status, result_text })
    detail.value = data
    if (status === 'completed') {
      let msg = '已完成'
      if (data.synced_to_week_draft) msg += '，已写入周报草稿'
      else msg += '，未写入周报草稿（本周已交则写入下周；请确认已填结果说明）'
      if (data.dingtalk_notified) msg += '，已钉钉通知交办人'
      ElMessage.success(msg)
    } else {
      ElMessage.success('状态已更新')
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '更新失败')
  }
}

async function saveResult() {
  if (!detail.value) return
  if (isPending.value) {
    ElMessage.warning('请先接收任务后再填写结果')
    return
  }
  try {
    const { data } = await api.patch(`/tasks/${detail.value.id}`, {
      result_text: detail.value.result_text || '',
    })
    detail.value = data
    ElMessage.success('结果已保存')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  }
}

async function changeOwner(uid: number) {
  if (!detail.value) return
  const { data } = await api.patch(`/tasks/${detail.value.id}/owner`, { assignee_id: uid })
  detail.value = data
  checklistDraft.value = Array.isArray(data.checklist) ? [...data.checklist] : []
  ElMessage.success('负责人已更换（交办任务需重新接收）')
}

async function addAssistant(uid: number) {
  if (!detail.value || !uid) return
  const { data } = await api.post(`/tasks/${detail.value.id}/assistants`, { user_id: uid })
  detail.value = data
  assistPick.value = null
  ElMessage.success('已添加辅助人')
}

async function removeAssistant(uid: number) {
  if (!detail.value) return
  const { data } = await api.delete(`/tasks/${detail.value.id}/assistants/${uid}`)
  detail.value = data
  ElMessage.success('已移除辅助人')
}

async function changePriority(priority: string) {
  if (!detail.value) return
  const { data } = await api.patch(`/tasks/${detail.value.id}/priority`, { priority })
  detail.value = data
  ElMessage.success('级别已更新')
}

async function runBreakdown() {
  if (!detail.value) return
  if (agentNative) {
    router.push('/dsh')
    return
  }
  if (isPending.value) {
    ElMessage.warning('请先接收任务')
    return
  }
  breakdownLoading.value = true
  try {
    const { data } = await api.post(`/tasks/${detail.value.id}/ai/breakdown`)
    checklistDraft.value = (data.items || []).map((c: any) => ({
      title: c.title || '',
      done: !!c.done,
    }))
    ElMessage.success('已生成拆解，确认后点「保存清单」')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '拆解失败')
  } finally {
    breakdownLoading.value = false
  }
}

function addCheckItem() {
  const t = newCheckItem.value.trim()
  if (!t) return
  checklistDraft.value.push({ title: t, done: false })
  newCheckItem.value = ''
}

async function saveChecklist() {
  if (!detail.value) return
  try {
    const { data } = await api.patch(`/tasks/${detail.value.id}/checklist`, {
      checklist: checklistDraft.value,
    })
    detail.value = data
    ElMessage.success('清单已保存')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  }
}

async function onAttachFile(file: File) {
  if (!detail.value) return false
  if (isPending.value) {
    ElMessage.warning('请先接收任务后再上传附件')
    return false
  }
  attachUploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    await api.post(`/tasks/${detail.value.id}/attachments`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success('附件已上传')
    await loadAttachments(detail.value.id)
    const { data } = await api.get(`/tasks/${detail.value.id}`)
    detail.value = data
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '上传失败')
  } finally {
    attachUploading.value = false
  }
  return false
}

async function removeAttachment(row: any) {
  if (!detail.value) return
  try {
    await api.delete(`/tasks/${detail.value.id}/attachments/${row.id}`)
    ElMessage.success('已删除')
    await loadAttachments(detail.value.id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

function downloadAttachment(row: any) {
  if (!detail.value) return
  const base = api.defaults.baseURL || ''
  const token = localStorage.getItem('token') || ''
  const url = `${base}/tasks/${detail.value.id}/attachments/${row.id}/download`
  fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    .then(async (res) => {
      if (!res.ok) throw new Error('下载失败')
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = row.original_filename || '附件'
      a.click()
      URL.revokeObjectURL(a.href)
    })
    .catch((e) => ElMessage.error(e?.message || '下载失败'))
}

function priorityTagClass(p: string) {
  if (p === 'p0') return 'wb-tag--red'
  if (p === 'p1') return 'wb-tag--yellow'
  if (p === 'p2') return 'wb-tag--yellow'
  return 'wb-tag--green'
}

function acceptLabel(row: any) {
  if (row.assignment_type !== 'delegated') return '—'
  return (row.acceptance_status || 'accepted') === 'pending' ? '待接收' : '已接收'
}

watch(taskId, () => {
  loadDetail()
})

onMounted(loadDetail)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <el-button text @click="goBack">← 返回</el-button>
      <h1 class="page-title">任务详情与编辑</h1>
      <el-button v-if="canDelete" class="head-delete" type="danger" plain @click="removeTask">
        删除任务
      </el-button>
    </div>

    <el-empty v-if="!loading && !detail" description="任务不存在或无权查看" />

    <div v-if="detail" class="layout">
      <div class="main-col">
        <div class="project-banner">
          <span class="project-banner-label">所属项目</span>
          <el-button
            v-if="detail.project_id"
            type="primary"
            link
            class="project-banner-name"
            @click="router.push(`/projects/${detail.project_id}`)"
          >
            {{ detail.project_name || `项目 #${detail.project_id}` }}
          </el-button>
          <span v-else class="project-banner-empty">未关联项目</span>
        </div>
        <div class="block">
          <div class="label">标题</div>
          <el-input v-model="detail.title" placeholder="任务标题" />
        </div>
        <div class="block">
          <div class="label">目标（做成什么样）</div>
          <el-input
            v-model="detail.goal"
            type="textarea"
            :rows="6"
            placeholder="写清验收标准，便于核对是否完成"
          />
          <div class="btn-row">
            <el-button
              v-if="!agentNative"
              size="small"
              :loading="polishingGoal"
              @click="polishField('goal')"
            >
              AI 润色目标
            </el-button>
            <el-button
              type="primary"
              plain
              size="small"
              :loading="savingBasics"
              @click="saveBasics"
            >
              保存标题与目标
            </el-button>
          </div>
        </div>

        <div class="block">
          <div class="label">所属阶段</div>
          <el-select
            v-model="phasePick"
            filterable
            clearable
            placeholder="未归入阶段"
            style="width: 100%; max-width: 320px"
          >
            <el-option v-for="p in phases" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <div class="btn-row">
            <el-button type="primary" plain size="small" :loading="savingBasics" @click="saveBasics">
              保存阶段
            </el-button>
          </div>
        </div>

        <p class="meta">
          {{ detail.assignment_type === 'delegated' ? '交办' : '自建' }}
          · 负责人 {{ detail.assignee?.name }}
          · {{ statusMap[detail.status] }}
          · 级别
          <span class="wb-tag" :class="priorityTagClass(detail.priority || 'p3')">
            {{ priorityMap[detail.priority || 'p3'] || detail.priority }}
          </span>
          <template v-if="detail.phase_name"> · 阶段 {{ detail.phase_name }}</template>
          <template v-if="detail.assignment_type === 'delegated'">
            · {{ acceptLabel(detail) }}
          </template>
          <template v-if="detail.due_date"> · 截止 {{ detail.due_date }}</template>
        </p>
        <p v-if="detail.accept_by && isPending" class="hint">
          请于 {{ detail.accept_by }} 前接收
        </p>

        <div v-if="taskReviewLoading" class="block muted">加载任务评价…</div>
        <div v-else-if="taskReview" class="block review-box">
          <div class="label">对我的任务评价</div>
          <p>{{ taskReview.summary }}</p>
          <ul v-if="taskReview.suggestions?.length">
            <li v-for="(s, i) in taskReview.suggestions" :key="'sug' + i">{{ s }}</li>
          </ul>
          <p class="hint">来源：{{ taskReview.source === 'llm' ? 'AI' : '规则' }}</p>
        </div>

        <div v-if="isPending" class="block accept-banner">
          <p>交办任务需先一键接收，才能更新进度。</p>
          <el-button v-if="canAccept" type="primary" @click="acceptTask">一键接收</el-button>
          <el-button v-if="canRemind" type="warning" plain @click="remindTask">催办</el-button>
        </div>

        <p v-if="detail.assistants?.length" class="meta">
          辅助人：
          <el-tag
            v-for="a in detail.assistants"
            :key="a.id"
            size="small"
            style="margin-right: 6px"
            :closable="canManageAssist"
            @close="removeAssistant(a.id)"
          >
            {{ a.name }}
          </el-tag>
        </p>

        <div v-if="canChangeLevel" class="block">
          <div class="label">调整级别（管理者）</div>
          <el-radio-group
            :model-value="detail.priority || 'p3'"
            size="small"
            @change="(v: string) => changePriority(v)"
          >
            <el-radio-button value="p0">P0</el-radio-button>
            <el-radio-button value="p1">P1</el-radio-button>
            <el-radio-button value="p2">P2</el-radio-button>
            <el-radio-button value="p3">P3</el-radio-button>
            <el-radio-button value="p4">P4</el-radio-button>
          </el-radio-group>
        </div>

        <div class="block">
          <div class="label">结果说明</div>
          <el-input
            v-model="detail.result_text"
            type="textarea"
            :rows="8"
            placeholder="完成时必填：写清做成了什么"
            :disabled="isPending"
          />
          <div class="btn-row">
            <el-button
              v-if="!agentNative"
              size="small"
              :loading="polishingResult"
              :disabled="isPending"
              @click="polishField('result_text')"
            >
              AI 润色结果
            </el-button>
            <el-button size="small" :disabled="isPending" @click="saveResult">
              保存结果
            </el-button>
          </div>
        </div>

        <div class="block">
          <div class="label">附件上传</div>
          <el-upload
            :show-file-list="false"
            :disabled="isPending || attachUploading"
            :http-request="(opt: any) => onAttachFile(opt.file)"
          >
            <el-button size="small" :loading="attachUploading" :disabled="isPending">
              上传附件
            </el-button>
          </el-upload>
          <ul v-if="attachments.length" class="attach-list">
            <li v-for="a in attachments" :key="a.id">
              <a href="#" @click.prevent="downloadAttachment(a)">{{ a.original_filename }}</a>
              <span class="muted"> · {{ a.uploader_name || '' }}</span>
              <el-button text type="danger" size="small" @click="removeAttachment(a)">删</el-button>
            </li>
          </ul>
          <p v-else class="hint">暂无附件</p>
        </div>

        <div class="block">
          <div class="label">更新状态</div>
          <el-button-group>
            <el-button size="default" :disabled="isPending" @click="setStatus('not_started')">
              未开始
            </el-button>
            <el-button size="default" :disabled="isPending" @click="setStatus('in_progress')">
              进行中
            </el-button>
            <el-button
              size="default"
              type="success"
              :disabled="isPending"
              @click="setStatus('completed')"
            >
              完成
            </el-button>
          </el-button-group>
        </div>

        <div class="block">
          <div class="label">执行清单</div>
          <div class="check-toolbar">
            <el-button
              v-if="!agentNative"
              size="small"
              :loading="breakdownLoading"
              :disabled="isPending"
              @click="runBreakdown"
            >
              AI 拆解
            </el-button>
            <el-button
              size="small"
              type="primary"
              plain
              :disabled="isPending"
              @click="saveChecklist"
            >
              保存清单
            </el-button>
          </div>
          <div v-for="(c, i) in checklistDraft" :key="i" class="check-row">
            <el-checkbox v-model="c.done" :disabled="isPending" />
            <el-input v-model="c.title" :disabled="isPending" />
            <el-button text type="danger" :disabled="isPending" @click="checklistDraft.splice(i, 1)">
              删
            </el-button>
          </div>
          <div class="check-row">
            <el-input
              v-model="newCheckItem"
              placeholder="自己加一步"
              :disabled="isPending"
              @keyup.enter="addCheckItem"
            />
            <el-button :disabled="isPending" @click="addCheckItem">添加</el-button>
          </div>
        </div>

        <div v-if="canManageAssist" class="block">
          <div class="label">添加辅助人</div>
          <el-select
            v-model="assistPick"
            filterable
            clearable
            placeholder="选择成员"
            style="width: 70%"
          >
            <el-option
              v-for="m in memberOptions.filter(
                (x) =>
                  x.id !== detail.assignee?.id &&
                  !(detail.assistants || []).some((a: any) => a.id === x.id),
              )"
              :key="m.id"
              :label="m.name"
              :value="m.id"
            />
          </el-select>
          <el-button
            size="small"
            style="margin-left: 8px"
            @click="assistPick && addAssistant(assistPick)"
          >
            添加
          </el-button>
        </div>

        <div v-if="canAssign" class="block">
          <div class="label">更换负责人（改派）</div>
          <el-select
            v-model="ownerPick"
            filterable
            clearable
            placeholder="新负责人"
            style="width: 70%"
          >
            <el-option
              v-for="m in memberOptions"
              :key="m.id"
              :label="m.name"
              :value="m.id"
            />
          </el-select>
          <el-button
            size="small"
            style="margin-left: 8px"
            @click="ownerPick && changeOwner(ownerPick)"
          >
            更换
          </el-button>
        </div>
      </div>

      <aside class="side-col">
        <div class="label">操作流水</div>
        <el-timeline v-if="(detail.events || []).length">
          <el-timeline-item
            v-for="e in detail.events || []"
            :key="e.id"
            :timestamp="formatDateTime(e.created_at)"
            placement="top"
          >
            <strong>{{ e.actor?.name }}</strong>
            {{ eventLabel[e.action] || e.action }}
            <span v-if="e.payload?.result_text" class="payload">
              ：{{ e.payload.result_text }}
            </span>
            <span v-else-if="e.payload?.new_status" class="payload">
              → {{ statusMap[e.payload.new_status] || e.payload.new_status }}
            </span>
            <span v-else-if="e.payload?.filename" class="payload">
              ：{{ e.payload.filename }}
            </span>
          </el-timeline-item>
        </el-timeline>
        <p v-else class="hint">暂无流水</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 8px 40px;
}
.page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
}
.head-delete {
  margin-left: auto;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 28px;
  align-items: start;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
.main-col,
.side-col {
  min-width: 0;
}
.side-col {
  background: #f8faf9;
  border: 1px solid var(--wb-border, #e5e7eb);
  border-radius: 10px;
  padding: 16px;
}
.project-banner {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 18px;
  padding: 12px 14px;
  background: #f0f7f4;
  border: 1px solid #d7ebe3;
  border-radius: 10px;
}
.project-banner-label {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: #4a635a;
}
.project-banner-name {
  font-size: 16px !important;
  font-weight: 650;
  white-space: normal;
  text-align: left;
  height: auto !important;
  padding: 0 !important;
  line-height: 1.4;
}
.project-banner-empty {
  font-size: 15px;
  color: #999;
}
.meta {
  color: #666;
  font-size: 13px;
  margin: 8px 0 16px;
  line-height: 1.6;
}
.block {
  margin-bottom: 22px;
}
.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.payload {
  color: #888;
  font-size: 12px;
}
.hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #999;
}
.muted {
  color: #888;
  font-size: 13px;
}
.attach-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}
.attach-list li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  padding: 4px 0;
}
.attach-list .muted {
  color: #999;
  font-size: 12px;
}
.review-box {
  background: #f6faf8;
  border-radius: 8px;
  padding: 12px;
}
.review-box ul {
  margin: 8px 0 0;
  padding-left: 18px;
}
.accept-banner {
  background: #fff7e6;
  border: 1px solid #f5dab1;
  border-radius: 8px;
  padding: 12px;
}
.accept-banner p {
  margin: 0 0 8px;
  font-size: 13px;
}
.check-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.check-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.wb-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
}
.wb-tag--red {
  background: #fef0f0;
  color: #c45656;
}
.wb-tag--yellow {
  background: #fdf6ec;
  color: #b88230;
}
.wb-tag--green {
  background: #f0f9f4;
  color: #3d9970;
}
</style>
