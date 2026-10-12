<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api, { request, getToken } from '@/utils/api'
import { formatDateTime } from '@/utils/datetime'
import SearchableSelect from '@/components/SearchableSelect.vue'
import { isAgentNativeProduct } from '@/productLine'

type UserBrief = { id: number; name: string; display_name?: string }
type Task = {
  id: number
  title: string
  goal?: string
  status: string
  priority?: string
  result_text?: string | null
  project_id: number
  assignment_type?: string
  acceptance_status?: string
  accept_by?: string | null
  assignee?: UserBrief | null
  created_by?: UserBrief | null
  assistants?: UserBrief[]
  checklist?: Array<{ title: string; done?: boolean }>
  events?: Array<{ action: string; created_at?: string; detail?: string }>
  due_date?: string | null
}

const STATUS_TEXT: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
const PRIORITY_TEXT: Record<string, string> = {
  p0: 'P0',
  p1: 'P1',
  p2: 'P2',
  p3: 'P3',
  p4: 'P4',
  critical: 'P0',
  watch: 'P1',
  normal: 'P3',
}
const EVENT_LABEL: Record<string, string> = {
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

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const agentNative = isAgentNativeProduct()
const taskId = computed(() => Number(route.params.id))
const task = ref<Task | null>(null)
const taskReview = ref<{
  summary?: string
  suggestions?: string[]
  source?: string
} | null>(null)
const resultText = ref('')
const goalText = ref('')
const error = ref('')
const members = ref<Array<{ user: UserBrief }>>([])
const assistUserId = ref<number | null>(null)
const canManageAssist = ref(false)
const checklist = ref<Array<{ title: string; done: boolean }>>([])
const attachments = ref<
  Array<{
    id: number
    original_filename: string
    uploader_name?: string
    file_size?: number
  }>
>([])
const attachUploading = ref(false)
const polishingGoal = ref(false)
const polishingResult = ref(false)
const canChangeLevel = computed(() =>
  (auth.user?.permissions || []).includes('task.level.change'),
)
const canAssign = computed(() =>
  (auth.user?.permissions || []).includes('task.assign'),
)
const ownerUserId = ref<number | null>(null)

const memberOptions = computed(() =>
  members.value.map((m) => ({
    value: m.user.id,
    label: m.user.name || m.user.display_name || `#${m.user.id}`,
  })),
)

const ownerOptions = computed(() =>
  members.value
    .filter((m) => m.user.id !== task.value?.assignee?.id)
    .map((m) => ({
      value: m.user.id,
      label: m.user.name || m.user.display_name || `#${m.user.id}`,
    })),
)

const isPending = computed(
  () =>
    task.value?.assignment_type === 'delegated' &&
    (task.value?.acceptance_status || 'accepted') === 'pending',
)
const canAccept = computed(
  () => isPending.value && task.value?.assignee?.id === auth.user?.id,
)
const canRemind = computed(
  () =>
    isPending.value &&
    (task.value?.created_by?.id === auth.user?.id ||
      (auth.user?.permissions || []).includes('task.assign')),
)
const canDelete = computed(() => {
  if (!task.value || !auth.user) return false
  if (task.value.created_by?.id === auth.user.id) return true
  const roles = auth.user.roles || []
  return roles.includes('admin') || roles.includes('dept_head')
})

/** 详情加载代数：切换任务时丢弃过期结果 */
let loadSeq = 0

async function load() {
  const id = taskId.value
  const seq = ++loadSeq
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H3',location:'dingtalk/TaskDetailView.vue:load:start',message:'dingtalk load start',data:{seq,id},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  try {
    const t = await request<Task>(`/tasks/${id}`)
    if (seq !== loadSeq) return
    task.value = t
    resultText.value = t.result_text || ''
    goalText.value = t.goal || ''
    checklist.value = Array.isArray(t.checklist)
      ? t.checklist.map((c) => ({ title: c.title || '', done: !!c.done }))
      : []
    const uid = auth.user?.id
    canManageAssist.value =
      !!(t.assignee && t.assignee.id === uid) ||
      (auth.user?.permissions || []).includes('task.assign')
    error.value = ''
    const [memRes, reviewRes, attachRes] = await Promise.all([
      request(`/projects/${t.project_id}/members`)
        .then((membersData) => ({ ok: true as const, membersData }))
        .catch(() => ({ ok: false as const, membersData: [] as any[] })),
      request(`/tasks/${id}/review`)
        .then((review) => ({ ok: true as const, review }))
        .catch(() => ({ ok: false as const, review: null })),
      request(`/tasks/${id}/attachments`)
        .then((list) => ({ ok: true as const, list: list as any[] }))
        .catch(() => ({ ok: false as const, list: [] as any[] })),
    ])
    if (seq !== loadSeq) {
      // #region agent log
      fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H3',location:'dingtalk/TaskDetailView.vue:load:stale',message:'discard stale dingtalk secondary',data:{seq,loadSeq,taskId:t.id},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      return
    }
    members.value = (memRes.membersData || []) as typeof members.value
    if (memRes.ok && members.value[0]) {
      assistUserId.value = members.value[0].user.id
      const other = members.value.find((m) => m.user.id !== t.assignee?.id)
      ownerUserId.value = other?.user.id ?? members.value[0]?.user.id ?? null
    } else {
      members.value = []
    }
    taskReview.value = (reviewRes.ok ? reviewRes.review : null) as typeof taskReview.value
    attachments.value = (attachRes.list || []) as typeof attachments.value
    // #region agent log
    fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'c7f782'},body:JSON.stringify({sessionId:'c7f782',hypothesisId:'H3',location:'dingtalk/TaskDetailView.vue:load:applied',message:'dingtalk applied',data:{seq,taskId:t.id},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  } catch (err) {
    if (seq !== loadSeq) return
    error.value = err instanceof Error ? err.message : '加载失败'
    task.value = null
    taskReview.value = null
    attachments.value = []
  }
}

async function acceptTask() {
  try {
    task.value = await request(`/tasks/${taskId.value}/accept`, 'POST')
    alert('已接收')
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '接收失败')
  }
}

async function remindTask() {
  try {
    await request(`/tasks/${taskId.value}/remind`, 'POST')
    alert('已催办')
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '催办失败')
  }
}

const deleteOpen = ref(false)
const deletePassword = ref('')

async function removeTask() {
  if (!task.value || !canDelete.value) return
  deletePassword.value = ''
  deleteOpen.value = true
}

async function confirmDelete() {
  if (!task.value || !canDelete.value) return
  const password = deletePassword.value.trim()
  if (!password) {
    alert('请输入登录密码')
    return
  }
  try {
    await request(`/tasks/${task.value.id}`, 'DELETE', { password })
    alert('任务已删除')
    router.back()
  } catch (err) {
    alert(err instanceof Error ? err.message : '删除失败')
  }
}

async function saveResult() {
  if (isPending.value) {
    alert('请先接收任务')
    return
  }
  try {
    task.value = await request(`/tasks/${taskId.value}`, 'PATCH', {
      result_text: resultText.value,
    })
    alert('已保存')
  } catch (err) {
    alert(err instanceof Error ? err.message : '保存失败')
  }
}

async function saveGoal() {
  try {
    task.value = await request(`/tasks/${taskId.value}`, 'PATCH', {
      goal: goalText.value,
    })
    alert('目标已保存')
  } catch (err) {
    alert(err instanceof Error ? err.message : '保存失败')
  }
}

async function polishField(field: 'goal' | 'result_text') {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  if (field === 'result_text' && isPending.value) {
    alert('请先接收任务')
    return
  }
  const loading = field === 'goal' ? polishingGoal : polishingResult
  loading.value = true
  try {
    const data = await request<{ text?: string; source?: string }>(
      `/tasks/${taskId.value}/ai/polish`,
      'POST',
      {
        field,
        text: field === 'goal' ? goalText.value : resultText.value,
      },
    )
    const polished = (data.text || '').trim()
    if (!polished) {
      alert('AI 未返回可用内容')
      return
    }
    if (field === 'goal') goalText.value = polished
    else resultText.value = polished
    alert(`已润色（${data.source === 'llm' ? 'AI' : '规则'}），请确认后保存`)
  } catch (err) {
    alert(err instanceof Error ? err.message : '润色失败')
  } finally {
    loading.value = false
  }
}

async function setStatus(status: string) {
  if (isPending.value) {
    alert('请先接收任务')
    return
  }
  if (status === 'completed' && !resultText.value.trim()) {
    alert('完成前请填写结果说明')
    return
  }
  if (status === 'completed' && !(attachments.value || []).length) {
    const ok = confirm('建议上传证明材料或输出文档。点「确定」仍完成，点「取消」先去上传。')
    if (!ok) return
  }
  try {
    const data = await request<Task & { synced_to_week_draft?: boolean; dingtalk_notified?: boolean }>(
      `/tasks/${taskId.value}`,
      'PATCH',
      {
        status,
        result_text: resultText.value,
      },
    )
    task.value = data
    if (status === 'completed') {
      let msg = '已完成'
      if (data.synced_to_week_draft) msg += '，已写入周报草稿'
      else msg += '，未写入周报草稿（本周已交则写入下周；请确认已填结果说明）'
      if (data.dingtalk_notified) msg += '，已通知交办人'
      alert(msg)
    } else {
      alert('状态已更新')
    }
  } catch (err) {
    alert(err instanceof Error ? err.message : '更新失败')
  }
}

async function onPickAttachment(ev: Event) {
  if (isPending.value) {
    alert('请先接收任务')
    return
  }
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  attachUploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    await api.post(`/tasks/${taskId.value}/attachments`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    alert('附件已上传')
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '上传失败')
  } finally {
    attachUploading.value = false
    input.value = ''
  }
}

async function removeAttachment(id: number) {
  try {
    await request(`/tasks/${taskId.value}/attachments/${id}`, 'DELETE')
    await load()
    alert('已删除')
  } catch (err) {
    alert(err instanceof Error ? err.message : '删除失败')
  }
}

async function downloadAttachment(id: number, name: string) {
  try {
    const base = api.defaults.baseURL || ''
    const res = await fetch(`${base}/tasks/${taskId.value}/attachments/${id}/download`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
    if (!res.ok) throw new Error('下载失败')
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = name || '附件'
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (err) {
    alert(err instanceof Error ? err.message : '下载失败')
  }
}

async function changePriority(priority: string) {
  try {
    task.value = await request(`/tasks/${taskId.value}/priority`, 'PATCH', { priority })
    alert('级别已更新')
  } catch (err) {
    alert(err instanceof Error ? err.message : '调整级别失败')
  }
}

async function addAssist() {
  if (!assistUserId.value) return
  try {
    await request(`/tasks/${taskId.value}/assistants`, 'POST', {
      user_id: assistUserId.value,
    })
    await load()
    alert('已添加')
  } catch (err) {
    alert(err instanceof Error ? err.message : '添加失败')
  }
}

async function removeAssist(uid: number) {
  try {
    await request(`/tasks/${taskId.value}/assistants/${uid}`, 'DELETE')
    await load()
    alert('已移除辅助人')
  } catch (err) {
    alert(err instanceof Error ? err.message : '移除失败')
  }
}

async function changeOwner() {
  if (!ownerUserId.value) {
    alert('请选择新负责人')
    return
  }
  if (ownerUserId.value === task.value?.assignee?.id) {
    alert('请选择与当前不同的负责人')
    return
  }
  try {
    task.value = await request(`/tasks/${taskId.value}/owner`, 'PATCH', {
      assignee_id: ownerUserId.value,
    })
    alert('负责人已更换（交办任务需重新接收）')
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '更换失败')
  }
}

async function runBreakdown() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  if (isPending.value) {
    alert('请先接收')
    return
  }
  try {
    const data = await request<{ items: Array<{ title: string; done?: boolean }> }>(
      `/tasks/${taskId.value}/ai/breakdown`,
      'POST',
    )
    checklist.value = (data.items || []).map((c) => ({
      title: c.title || '',
      done: !!c.done,
    }))
    alert('已生成拆解，请点保存清单')
  } catch (err) {
    alert(err instanceof Error ? err.message : '拆解失败')
  }
}

async function saveChecklist() {
  if (isPending.value) {
    alert('请先接收')
    return
  }
  try {
    task.value = await request(`/tasks/${taskId.value}/checklist`, 'PATCH', {
      checklist: checklist.value,
    })
    alert('清单已保存')
  } catch (err) {
    alert(err instanceof Error ? err.message : '保存失败')
  }
}

onMounted(async () => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  await load()
})
</script>

<template>
  <div class="page no-tab">
    <button class="back" @click="router.back()">← 返回</button>
    <div v-if="error" class="deny">{{ error }}</div>
    <template v-if="task">
      <h1 class="page-title">{{ task.title }}</h1>
      <p class="page-desc">
        {{ STATUS_TEXT[task.status] || task.status }}
        · {{ PRIORITY_TEXT[task.priority || 'p3'] || task.priority || 'P3' }}
        <span v-if="task.assignment_type === 'delegated'">
          · {{ isPending ? '待接收' : '已接收' }}
        </span>
        <span v-if="task.due_date"> · 截止 {{ task.due_date }}</span>
      </p>
      <button v-if="canDelete && !deleteOpen" type="button" class="btn btn-sm btn-danger" @click="removeTask">
        删除任务
      </button>
      <div v-if="deleteOpen" class="card">
        <div class="field-label">请输入登录密码</div>
        <p class="muted">删除后不能恢复。密码正确才会删除。</p>
        <input v-model="deletePassword" type="password" class="input" placeholder="登录密码" />
        <div class="btn-row" style="margin-top:10px">
          <button type="button" class="btn btn-ghost" @click="deleteOpen = false">取消</button>
          <button type="button" class="btn btn-danger" @click="confirmDelete">确认删除</button>
        </div>
      </div>

      <div class="card">
        <div class="field-label">目标（做成什么样）</div>
        <textarea v-model="goalText" class="textarea" />
        <div class="btn-row" style="margin-top:10px">
          <button v-if="!agentNative" class="btn btn-ghost" :disabled="polishingGoal" @click="polishField('goal')">
            {{ polishingGoal ? '润色中…' : 'AI 润色目标' }}
          </button>
          <button class="btn btn-mint" @click="saveGoal">保存目标</button>
        </div>
      </div>

      <div v-if="taskReview" class="card">
        <div class="sec-title">对我的任务评价</div>
        <p>{{ taskReview.summary }}</p>
        <ul v-if="taskReview.suggestions?.length">
          <li v-for="(s, i) in taskReview.suggestions" :key="'sg' + i">{{ s }}</li>
        </ul>
        <p class="muted">来源：{{ taskReview.source === 'llm' ? 'AI' : '规则' }}</p>
      </div>

      <div v-if="isPending" class="card">
        <p class="muted">交办任务需先接收，才能更新进度。</p>
        <div class="btn-row">
          <button v-if="canAccept" class="btn" @click="acceptTask">一键接收</button>
          <button v-if="canRemind" class="btn btn-ghost" @click="remindTask">催办</button>
        </div>
      </div>

      <div v-if="canChangeLevel" class="card">
        <div class="field-label">调整级别</div>
        <div class="btn-row">
          <button class="btn btn-ghost" @click="changePriority('p0')">P0</button>
          <button class="btn btn-ghost" @click="changePriority('p1')">P1</button>
          <button class="btn btn-ghost" @click="changePriority('p2')">P2</button>
          <button class="btn btn-ghost" @click="changePriority('p3')">P3</button>
          <button class="btn" @click="changePriority('p4')">P4</button>
        </div>
      </div>

      <div class="card">
        <div class="field-label">结果说明</div>
        <textarea v-model="resultText" class="textarea" :disabled="isPending" />
        <div class="btn-row" style="margin-top:10px">
          <button
            v-if="!agentNative"
            class="btn btn-ghost"
            :disabled="isPending || polishingResult"
            @click="polishField('result_text')"
          >
            {{ polishingResult ? '润色中…' : 'AI 润色结果' }}
          </button>
          <button class="btn btn-mint" :disabled="isPending" @click="saveResult">
            保存内容
          </button>
        </div>
      </div>

      <div class="card">
        <div class="field-label">附件上传</div>
        <input
          type="file"
          :disabled="isPending || attachUploading"
          @change="onPickAttachment"
        />
        <div v-if="!attachments.length" class="muted" style="margin-top:8px">暂无附件</div>
        <div v-for="a in attachments" :key="a.id" class="attach-row">
          <button class="link" type="button" @click="downloadAttachment(a.id, a.original_filename)">
            {{ a.original_filename }}
          </button>
          <button class="btn btn-ghost" type="button" @click="removeAttachment(a.id)">删</button>
        </div>
      </div>

      <div class="card">
        <div class="field-label">改状态</div>
        <div class="btn-row">
          <button class="btn btn-ghost" :disabled="isPending" @click="setStatus('not_started')">未开始</button>
          <button class="btn btn-ghost" :disabled="isPending" @click="setStatus('in_progress')">进行中</button>
          <button class="btn" :disabled="isPending" @click="setStatus('completed')">完成</button>
        </div>
      </div>

      <div class="card">
        <div class="field-label">执行清单</div>
        <div class="btn-row">
          <button v-if="!agentNative" class="btn btn-ghost" :disabled="isPending" @click="runBreakdown">AI 拆解</button>
          <button class="btn btn-mint" :disabled="isPending" @click="saveChecklist">保存清单</button>
        </div>
        <label v-for="(c, i) in checklist" :key="i" class="check-item">
          <input v-model="c.done" type="checkbox" :disabled="isPending" />
          <input v-model="c.title" class="input" :disabled="isPending" />
        </label>
      </div>

      <div v-if="canManageAssist" class="card">
        <div class="field-label">添加辅助人</div>
        <SearchableSelect
          v-model="assistUserId"
          :options="memberOptions"
          placeholder="输入关键字搜索成员"
        />
        <button class="btn btn-mint" style="margin-top:10px" @click="addAssist">添加</button>
        <div v-if="task.assistants?.length" style="margin-top:8px">
          <div
            v-for="a in task.assistants"
            :key="a.id"
            class="assist-row"
          >
            <span class="muted">{{ a.name }}</span>
            <button class="btn btn-ghost" type="button" @click="removeAssist(a.id)">移除</button>
          </div>
        </div>
      </div>

      <div v-if="canAssign && task" class="card">
        <div class="field-label">更换负责人（改派）</div>
        <SearchableSelect
          v-model="ownerUserId"
          :options="ownerOptions"
          placeholder="输入关键字搜索新负责人"
        />
        <button class="btn btn-mint" style="margin-top:10px" @click="changeOwner">更换</button>
        <p class="muted" style="margin-top:8px">更换后若为交办，对方需重新接收</p>
      </div>

      <div class="card">
        <div class="field-label">流水</div>
        <div v-if="!(task.events || []).length" class="muted">暂无记录</div>
        <div v-for="(e, i) in task.events || []" :key="i" class="event">
          <b>{{ EVENT_LABEL[e.action] || e.action }}</b>
          <span class="muted"> {{ formatDateTime(e.created_at) }}</span>
          <div v-if="e.detail" class="muted">{{ e.detail }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.btn-danger {
  background: #c45656;
  color: #fff;
  margin-bottom: 12px;
}
.back {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0 0 12px;
  cursor: pointer;
}
.event { padding: 8px 0; border-bottom: 1px solid var(--wb-border); }
.event:last-child { border-bottom: none; }
.check-item {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}
.attach-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}
.assist-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}
.link {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  text-align: left;
  padding: 0;
  cursor: pointer;
  word-break: break-all;
}
</style>
