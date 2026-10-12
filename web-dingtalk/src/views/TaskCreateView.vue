<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'
import SearchableSelect from '@/components/SearchableSelect.vue'
import AgentNativeHint from '@/components/AgentNativeHint.vue'
import { isAgentNativeProduct } from '@/productLine'

type Project = { id: number; name: string }
type Member = { id: number; name: string }
type Phase = { id: number; name: string; status: string }

type CreateResult = {
  id: number
  assignee?: { id: number; name: string }
  synced_to_week_draft?: boolean
  dingtalk_notified?: boolean
}

const router = useRouter()
const auth = useAuthStore()
const agentNative = isAgentNativeProduct()

const canAssign = computed(() => (auth.user?.permissions || []).includes('task.assign'))
const canSelf = computed(() => (auth.user?.permissions || []).includes('task.create.self'))
const myId = computed(() => auth.user?.id ?? null)

const projects = ref<Project[]>([])
const projectId = ref<number | null>(null)
const title = ref('')
const goal = ref('')
const dueDate = ref('')
const phaseId = ref<number | null>(null)
const phases = ref<Phase[]>([])
const priority = ref<'p0' | 'p1' | 'p2' | 'p3' | 'p4'>('p3')
const acceptBy = ref('')
const members = ref<Member[]>([])
/** 责任人：仅管理员可见；空字符串=自己 */
const assigneeId = ref<string>('')
/** 辅助人多选（id 列表，不含责任人） */
const assistantIds = ref<number[]>([])
/** 辅助人下拉是否展开 */
const assistOpen = ref(false)
const moreOpen = ref(false)
const error = ref('')
const submitting = ref(false)
const aiText = ref('')
const aiLoading = ref(false)
const aiHint = ref('')
const projectTouched = ref(false)
const projectHint = ref('')
const similarHint = ref('')
const lastAutoProjectId = ref<number | null>(null)
let guessTimer: ReturnType<typeof setTimeout> | undefined
let similarTimer: ReturnType<typeof setTimeout> | undefined

type SimilarTask = { id: number; title: string; score: number; project_id: number }
type CheckSimilarOut = {
  vague: boolean
  similar: SimilarTask[]
  level: 'none' | 'soft' | 'hard'
  message: string
  continuation_warning?: string
}

const effectiveOwnerId = computed(() => {
  if (canAssign.value && assigneeId.value) return Number(assigneeId.value)
  return myId.value
})

const isDelegatingOther = computed(() => {
  if (!canAssign.value || !assigneeId.value || !myId.value) return false
  return Number(assigneeId.value) !== myId.value
})

const assistCandidates = computed(() =>
  members.value.filter((m) => m.id !== effectiveOwnerId.value),
)

const projectOptions = computed(() =>
  projects.value.map((p) => ({ value: p.id, label: p.name })),
)

const assigneeOptions = computed(() =>
  members.value.map((m) => ({
    value: String(m.id),
    label: m.id === myId.value ? `${m.name}（我自己）` : m.name,
  })),
)

/** 辅助人面板内搜索关键字 */
const assistQuery = ref('')
const filteredAssistCandidates = computed(() => {
  const q = assistQuery.value.trim().toLowerCase()
  if (!q) return assistCandidates.value
  return assistCandidates.value.filter((m) => (m.name || '').toLowerCase().includes(q))
})

const assistSummary = computed(() => {
  if (!assistantIds.value.length) return '输入关键字搜索辅助人（可多选）'
  const names = assistCandidates.value
    .filter((m) => assistantIds.value.includes(m.id))
    .map((m) => m.name)
  return names.length ? names.join('、') : '输入关键字搜索辅助人（可多选）'
})

function pruneAssistants() {
  const oid = effectiveOwnerId.value
  const allowed = new Set(assistCandidates.value.map((m) => m.id))
  assistantIds.value = assistantIds.value.filter((id) => id !== oid && allowed.has(id))
}

function toggleAssist(id: number) {
  if (id === effectiveOwnerId.value) return
  const set = new Set(assistantIds.value)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  assistantIds.value = [...set]
}

function isAssistSelected(id: number) {
  return assistantIds.value.includes(id)
}

function markProjectTouched() {
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'B',location:'TaskCreateView.vue:markProjectTouched',message:'project touched',data:{projectId:projectId.value,projectTouchedBefore:projectTouched.value,lastAuto:lastAutoProjectId.value,willSkip:projectId.value!=null&&projectId.value===lastAutoProjectId.value},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
  if (projectId.value != null && projectId.value === lastAutoProjectId.value) return
  projectTouched.value = true
  projectHint.value = ''
}

async function loadMembers(pid: number) {
  const rows = await request<Array<{ user: { id: number; name: string } }>>(
    `/projects/${pid}/members`,
  )
  members.value = rows.map((m) => ({
    id: m.user.id,
    name: m.user.name,
  }))
  if (assigneeId.value && !members.value.some((m) => String(m.id) === assigneeId.value)) {
    assigneeId.value = ''
  }
  pruneAssistants()
  assistOpen.value = false
}

async function loadPhases(pid: number) {
  phases.value = await request<Phase[]>(`/projects/${pid}/phases`).catch(() => [])
  if (phaseId.value != null && !phases.value.some((p) => p.id === phaseId.value)) {
    phaseId.value = null
  }
}

onMounted(async () => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  if (!canSelf.value) {
    error.value = '无写任务权限'
    return
  }
  try {
    const data = await request<{ my_projects?: Project[] }>('/personal/workspace')
    projects.value = (data.my_projects || []).slice().sort((a, b) =>
      (a.name || '').localeCompare(b.name || '', 'zh'),
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
})

onUnmounted(() => {
  if (guessTimer) clearTimeout(guessTimer)
})

watch(projectId, async (pid) => {
  phaseId.value = null
  phases.value = []
  if (!pid) {
    members.value = []
    return
  }
  await Promise.all([loadMembers(pid), loadPhases(pid)])
})

watch(effectiveOwnerId, () => {
  pruneAssistants()
})

watch(title, (text) => {
  if (guessTimer) clearTimeout(guessTimer)
  if (similarTimer) clearTimeout(similarTimer)
  const trimmed = text.trim()
  if (trimmed.length < 2) {
    if (!projectTouched.value) projectHint.value = ''
    similarHint.value = ''
    return
  }
  if (!projectTouched.value) {
    guessTimer = setTimeout(async () => {
      if (projectTouched.value) return
      try {
        const data = await request<{
          project_id?: number | null
          project_name?: string | null
          reason?: string
        }>('/tasks/guess-project', 'POST', { title: trimmed, goal: goal.value.trim() })
        if (projectTouched.value || !data?.project_id) return
        lastAutoProjectId.value = data.project_id
        projectId.value = data.project_id
        const why = data.reason ? `（${data.reason}）` : ''
        projectHint.value = data.project_name
          ? `已建议归入「${data.project_name}」${why}，可以改`
          : `已建议项目${why}，可以改`
      } catch {
        /* 猜不到就让人自己选 */
      }
    }, 500)
  }
  similarTimer = setTimeout(async () => {
    try {
      const data = await request<CheckSimilarOut>('/tasks/check-similar', 'POST', {
        title: trimmed,
        goal: goal.value.trim(),
        project_id: projectId.value,
        assignee_id: assigneeId.value ? Number(assigneeId.value) : null,
        assignment_type: isDelegatingOther.value ? 'delegated' : 'self',
      })
      if (data.vague) {
        similarHint.value = data.message || '标题太笼统，请写清具体事项'
      } else if (data.level === 'hard' && data.similar?.[0]) {
        similarHint.value = `已有相似任务「${data.similar[0].title}」，建议打开已有`
      } else if (data.level === 'soft' && data.similar?.[0]) {
        similarHint.value = `疑似重复「${data.similar[0].title}」，请想清楚`
      } else if (data.continuation_warning) {
        similarHint.value = data.continuation_warning
      } else {
        similarHint.value = ''
      }
    } catch {
      similarHint.value = ''
    }
  }, 500)
})

async function runAi() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const text = aiText.value.trim()
  if (!text) {
    alert('先说一句话')
    return
  }
  aiLoading.value = true
  try {
    const data = await request<Record<string, unknown>>('/tasks/ai/draft', 'POST', { text })
    title.value = String(data.title || text)
    if (data.goal) goal.value = String(data.goal)
    if (data.project_id) {
      lastAutoProjectId.value = Number(data.project_id)
      projectId.value = Number(data.project_id)
    }
    if (canAssign.value && data.assignee_id) {
      assigneeId.value = String(data.assignee_id)
    }
    if (['p0', 'p1', 'p2', 'p3', 'p4'].includes(String(data.priority))) {
      priority.value = data.priority as 'p0' | 'p1' | 'p2' | 'p3' | 'p4'
    } else if (data.priority === 'critical') {
      priority.value = 'p0'
    } else if (data.priority === 'watch') {
      priority.value = 'p1'
    }
    if (data.due_date) dueDate.value = String(data.due_date)
    if (data.accept_by) acceptBy.value = String(data.accept_by).slice(0, 16)
    if (Array.isArray(data.assistant_ids)) {
      const oid = effectiveOwnerId.value
      const allowed = new Set(assistCandidates.value.map((m) => m.id))
      assistantIds.value = (data.assistant_ids as number[])
        .map(Number)
        .filter((id) => id !== oid && allowed.has(id))
    }
    aiHint.value = String(data.rationale || '')
  } catch (err) {
    alert(err instanceof Error ? err.message : 'AI 失败')
  } finally {
    aiLoading.value = false
  }
}

function successMessage(data: CreateResult) {
  const parts: string[] = []
  if (isDelegatingOther.value) {
    const name = data.assignee?.name || '对方'
    parts.push(`已交办给${name}，已进入其个人待办与部门待办`)
    parts.push(data.dingtalk_notified ? '已发钉钉通知' : '钉钉未通知（未绑定或未配置）')
  } else {
    parts.push('已加入我的待办')
  }
  if (data.synced_to_week_draft) {
    parts.push(isDelegatingOther.value ? '已写入对方本周周报草稿' : '已写入本周周报草稿')
  } else {
    parts.push('本周周报已提交，本次未写入草稿（不改已交内容）')
  }
  return parts.join('；')
}

async function createTask(payload: Record<string, unknown>) {
  submitting.value = true
  try {
    const data = await request<CreateResult>('/tasks', 'POST', payload)
    alert(successMessage(data))
    router.replace({ name: 'todo' })
  } catch (err) {
    alert(err instanceof Error ? err.message : '创建失败')
  } finally {
    submitting.value = false
  }
}

async function submit() {
  if (!projectId.value || !title.value.trim()) {
    alert('请填写标题和项目')
    return
  }
  const owner = effectiveOwnerId.value
  const assistant_ids = assistantIds.value.filter((id) => id !== owner)
  const delegated = isDelegatingOther.value
  const payload: Record<string, unknown> = {
    project_id: projectId.value,
    title: title.value.trim(),
    goal: goal.value.trim(),
    due_date: dueDate.value || null,
    phase_id: phaseId.value || null,
    assignment_type: delegated ? 'delegated' : 'self',
    assistant_ids,
    priority: priority.value,
  }
  if (delegated) {
    payload.assignee_id = Number(assigneeId.value)
    if (acceptBy.value) {
      payload.accept_by = new Date(acceptBy.value).toISOString()
    }
  }

  try {
    const check = await request<CheckSimilarOut>('/tasks/check-similar', 'POST', {
      title: payload.title,
      goal: payload.goal,
      project_id: payload.project_id,
      assignee_id: payload.assignee_id ?? null,
      assignment_type: payload.assignment_type,
    })
    if (check.vague) {
      alert(check.message || '请写清具体事项后再创建')
      return
    }
    if (check.level === 'hard' && check.similar?.[0]) {
      const first = check.similar[0]
      const open = confirm(
        `${check.message || '已有高度相似未完成任务'}\n已有：#${first.id} ${first.title}\n\n确定=打开已有；取消=考虑仍要新建`,
      )
      if (open) {
        router.push(`/tasks/${first.id}`)
        return
      }
      if (!confirm('确定仍要新建？请确认不是同一件事。')) return
      payload.force_create = true
    } else if (check.level === 'soft' && check.similar?.[0]) {
      if (
        !confirm(
          `${check.message || '疑似重复'}（「${check.similar[0].title}」）\n仍要创建吗？`,
        )
      ) {
        return
      }
    } else if (check.continuation_warning) {
      if (!confirm(`${check.continuation_warning}\n\n继续创建？`)) return
    }
  } catch {
    /* 检测失败不阻断，交给创建接口兜底 */
  }

  await createTask(payload)
}
</script>

<template>
  <div class="page no-tab">
    <button class="back" @click="router.back()">← 返回</button>
    <h1 class="page-title">记任务</h1>
    <p class="muted" style="margin-top:-8px;margin-bottom:12px">写标题、选项目、定截止日期即可。</p>
    <div v-if="error" class="deny">{{ error }}</div>
    <template v-else>
      <div class="field">
        <div class="field-label">标题</div>
        <input v-model="title" class="input" placeholder="要做什么（勿只写「继续推进」）" />
        <p v-if="similarHint" class="warn-hint">{{ similarHint }}</p>
      </div>
      <div class="field">
        <div class="field-label">项目</div>
        <SearchableSelect
          v-model="projectId"
          :options="projectOptions"
          placeholder="输入关键字搜索项目"
          @change="markProjectTouched"
        />
        <p v-if="projectHint && !projectTouched" class="muted">{{ projectHint }}</p>
      </div>
      <div class="field">
        <div class="field-label">截止日期</div>
        <input v-model="dueDate" class="input" type="date" />
      </div>
      <div v-if="phases.length" class="field">
        <div class="field-label">所属阶段（可不选）</div>
        <select v-model="phaseId" class="input">
          <option :value="null">不指定</option>
          <option v-for="p in phases" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>

      <button type="button" class="link more-toggle" @click="moreOpen = !moreOpen">
        {{ moreOpen ? '收起更多' : '更多（目标、辅助人、一句话生成）' }}
      </button>

      <template v-if="moreOpen">
        <div class="field">
          <div class="field-label">一句话（可选）</div>
          <AgentNativeHint v-if="agentNative" tip="建任务请到「我的助手」里说。" />
          <template v-else>
          <textarea v-model="aiText" class="textarea" placeholder="例：跟进开票；或交办同事明天前接收" />
          <button class="btn btn-ghost" style="margin-top:8px" :disabled="aiLoading" @click="runAi">
            {{ aiLoading ? '生成中…' : '生成草稿' }}
          </button>
          <p v-if="aiHint" class="muted">{{ aiHint }}</p>
          </template>
        </div>
        <div class="field">
          <div class="field-label">目标</div>
          <textarea v-model="goal" class="textarea" placeholder="做成什么样（可选）" />
        </div>
        <div class="field">
          <div class="field-label">级别</div>
          <select v-model="priority" class="input">
            <option value="p0">P0（最高，会提醒责任人）</option>
            <option value="p1">P1</option>
            <option value="p2">P2</option>
            <option value="p3">P3</option>
            <option value="p4">P4（最低）</option>
          </select>
        </div>
        <div v-if="canAssign" class="field">
          <div class="field-label">责任人（不选=自己）</div>
          <SearchableSelect
            :model-value="assigneeId || null"
            :options="assigneeOptions"
            placeholder="输入关键字搜索；不选则归自己"
            @update:model-value="(v) => { assigneeId = v == null ? '' : String(v) }"
          />
        </div>
        <div v-if="isDelegatingOther" class="field">
          <div class="field-label">请于何时前接收</div>
          <input v-model="acceptBy" class="input" type="datetime-local" />
        </div>
        <div class="field">
          <div class="field-label">辅助人（可选，可多选）</div>
          <div v-if="assistCandidates.length" class="ms">
            <button
              type="button"
              class="input ms-trigger"
              :aria-expanded="assistOpen"
              @click="assistOpen = !assistOpen"
            >
              <span class="ms-text" :class="{ placeholder: !assistantIds.length }">{{ assistSummary }}</span>
              <span class="ms-arrow">{{ assistOpen ? '▲' : '▼' }}</span>
            </button>
            <div v-if="assistOpen" class="ms-panel">
              <input
                v-model="assistQuery"
                class="input ss-assist-search"
                type="search"
                placeholder="输入关键字搜索成员"
                @click.stop
              />
              <label v-for="m in filteredAssistCandidates" :key="m.id" class="ms-option">
                <input
                  type="checkbox"
                  :checked="isAssistSelected(m.id)"
                  @change="toggleAssist(m.id)"
                />
                <span>{{ m.name }}</span>
              </label>
              <p v-if="!filteredAssistCandidates.length" class="ms-empty">无匹配成员</p>
            </div>
          </div>
          <p v-else class="muted">先选项目，再选辅助人</p>
        </div>
      </template>

      <button class="btn" :disabled="submitting" @click="submit">确认创建</button>
    </template>
  </div>
</template>

<style scoped>
.back {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0 0 12px;
  cursor: pointer;
}
.more-toggle {
  display: block;
  margin: 4px 0 14px;
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0;
  cursor: pointer;
}
.ms {
  position: relative;
}
.ms-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  text-align: left;
  cursor: pointer;
  width: 100%;
}
.ms-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ms-text.placeholder {
  color: var(--wb-muted);
}
.ms-arrow {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--wb-muted);
}
.ms-panel {
  margin-top: 6px;
  border: 1px solid var(--wb-border);
  border-radius: 10px;
  background: #fff;
  max-height: 260px;
  overflow-y: auto;
}
.ss-assist-search {
  border: none;
  border-bottom: 1px solid var(--wb-border);
  border-radius: 0;
  width: 100%;
}
.ms-empty {
  margin: 0;
  padding: 14px;
  color: var(--wb-muted);
  font-size: 13px;
}
.ms-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--wb-border);
  font-size: 14px;
  color: var(--wb-text);
  cursor: pointer;
}
.ms-option:last-child {
  border-bottom: none;
}
.ms-option input {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}
.warn-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: #b45309;
  line-height: 1.4;
}
</style>
