<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { TASK_PRIORITY_HINT, TASK_PRIORITY_MAP } from '../constants/projectMeta'
import { isAgentNativeProduct } from '../productLine'
import AgentNativeHint from '../components/AgentNativeHint.vue'

type SimilarTask = { id: number; title: string; score: number; project_id: number }
type CheckSimilarOut = {
  vague: boolean
  similar: SimilarTask[]
  level: 'none' | 'soft' | 'hard'
  message: string
  continuation_warning?: string
}

type Project = { id: number; name: string }
type Member = { id: number; name: string }
type PhaseOption = { id: number; name: string }

const WRITE_TASK_DRAFT_KEY = 'wb:write-task-draft'
/** 嵌入任务管理页时回跳用的路径 */
const WRITE_TASK_RETURN = '/tasks'

const props = withDefaults(
  defineProps<{
    /** 嵌在任务管理 Tab 内时不显示页面标题 */
    embedded?: boolean
  }>(),
  { embedded: false },
)

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()

const canAssign = computed(() => userStore.hasPermission('task.assign'))
const canSelf = computed(() => userStore.hasPermission('task.create.self'))
const canCreateProject = computed(() => userStore.hasPermission('project.create'))
const myId = computed(() => userStore.user?.id ?? null)

function cancelWrite() {
  if (props.embedded) {
    router.replace({ path: WRITE_TASK_RETURN })
    return
  }
  router.push('/personal')
}

const projects = ref<Project[]>([])
const members = ref<Member[]>([])
const phases = ref<PhaseOption[]>([])
const loading = ref(false)
const submitting = ref(false)
const aiText = ref('')
const aiLoading = ref(false)
const aiHint = ref('')
/** 用户手动改过项目后，不再自动改选 */
const projectTouched = ref(false)
const projectHint = ref('')
/** 相似/续推黄字提示（标题停顿检测） */
const similarHint = ref('')
/** 最近一次自动建议的项目，避免程序改选被当成手动 */
const lastAutoProjectId = ref<number | null>(null)
let projectGuessTimer: ReturnType<typeof setTimeout> | null = null
let similarCheckTimer: ReturnType<typeof setTimeout> | null = null

const form = ref({
  project_id: null as number | null,
  phase_id: null as number | null,
  title: '',
  goal: '',
  due_date: '' as string,
  priority: 'p3' as 'p0' | 'p1' | 'p2' | 'p3' | 'p4',
  assignee_id: null as number | null,
  assistant_ids: [] as number[],
  accept_by: '' as string,
})

/** 当前负责人（自建=自己；交办=所选责任人；未选责任人=自己） */
const effectiveOwnerId = computed(() => {
  if (canAssign.value && form.value.assignee_id) return form.value.assignee_id
  return myId.value
})

const isDelegatingOther = computed(() => {
  if (!canAssign.value || !form.value.assignee_id || !myId.value) return false
  return form.value.assignee_id !== myId.value
})

const assistantOptions = computed(() =>
  members.value.filter((m) => m.id !== effectiveOwnerId.value),
)

async function loadMembers(pid: number) {
  const { data } = await api.get(`/projects/${pid}/members`)
  members.value = (data || []).map((m: any) => ({
    id: m.user?.id ?? m.id,
    name: m.user?.name ?? m.name,
  }))
  // 责任人若不在成员里则清空；辅助人过滤掉负责人
  if (
    form.value.assignee_id &&
    !members.value.some((m) => m.id === form.value.assignee_id)
  ) {
    form.value.assignee_id = null
  }
  form.value.assistant_ids = form.value.assistant_ids.filter(
    (id) => id !== effectiveOwnerId.value && members.value.some((m) => m.id === id),
  )
}

async function loadPhases(pid: number) {
  try {
    const { data } = await api.get(`/projects/${pid}/phases`)
    phases.value = (data || []).map((p: any) => ({ id: p.id, name: p.name }))
  } catch {
    phases.value = []
  }
  if (
    form.value.phase_id != null &&
    !phases.value.some((p) => p.id === form.value.phase_id)
  ) {
    form.value.phase_id = null
  }
}

function stashDraft() {
  try {
    sessionStorage.setItem(
      WRITE_TASK_DRAFT_KEY,
      JSON.stringify({
        form: form.value,
        aiText: aiText.value,
        aiHint: aiHint.value,
      }),
    )
  } catch {
    /* 忽略配额/隐私模式失败 */
  }
}

function restoreDraft(): boolean {
  try {
    const raw = sessionStorage.getItem(WRITE_TASK_DRAFT_KEY)
    if (!raw) return false
    const parsed = JSON.parse(raw)
    if (parsed?.form && typeof parsed.form === 'object') {
      form.value = {
        ...form.value,
        ...parsed.form,
        assistant_ids: Array.isArray(parsed.form.assistant_ids)
          ? parsed.form.assistant_ids
          : [],
      }
    }
    if (typeof parsed?.aiText === 'string') aiText.value = parsed.aiText
    if (typeof parsed?.aiHint === 'string') aiHint.value = parsed.aiHint
    sessionStorage.removeItem(WRITE_TASK_DRAFT_KEY)
    return true
  } catch {
    sessionStorage.removeItem(WRITE_TASK_DRAFT_KEY)
    return false
  }
}

async function loadProjects(opts?: { preferProjectId?: number | null; skipDefault?: boolean }) {
  loading.value = true
  try {
    const { data } = await api.get('/personal/workspace')
    const list: Project[] = (data.my_projects || []).slice().sort((a: Project, b: Project) => {
      if (a.name === '日常任务') return -1
      if (b.name === '日常任务') return 1
      return (a.name || '').localeCompare(b.name || '', 'zh')
    })
    projects.value = list
    // #region agent log
    fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'D',location:'WriteTaskView.vue:loadProjects',message:'pc projects loaded',data:{count:list.length,buildMarker:'PC_FILTERABLE_V2'},timestamp:Date.now()})}).catch(()=>{})
    // #endregion
    const prefer = opts?.preferProjectId
    if (prefer && list.some((p) => p.id === prefer)) {
      form.value.project_id = prefer
      await loadMembers(prefer)
      return
    }
    if (opts?.skipDefault && form.value.project_id) {
      const keep = list.find((p) => p.id === form.value.project_id)
      if (keep) {
        await loadMembers(keep.id)
        return
      }
    }
    const daily = list.find((p) => p.name === '日常任务')
    const first = daily || list[0]
    if (first) {
      form.value.project_id = first.id
      await loadMembers(first.id)
    }
  } finally {
    loading.value = false
  }
}

function goCreateProject() {
  if (!canCreateProject.value) return
  stashDraft()
  router.push({
    path: '/overview',
    query: { create: '1', returnTo: WRITE_TASK_RETURN, returnTab: 'write' },
  })
}

watch(
  () => form.value.project_id,
  async (pid) => {
    if (pid) {
      await Promise.all([loadMembers(pid), loadPhases(pid)])
    } else {
      phases.value = []
      form.value.phase_id = null
    }
  },
)

watch(effectiveOwnerId, (oid) => {
  if (oid == null) return
  form.value.assistant_ids = form.value.assistant_ids.filter((id) => id !== oid)
})

function markProjectTouched(value: number | null) {
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'D',location:'WriteTaskView.vue:markProjectTouched',message:'pc project changed',data:{value,projectCount:projects.value.length,buildMarker:'PC_FILTERABLE_V2',willSkip:value!=null&&value===lastAutoProjectId.value},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
  if (value != null && value === lastAutoProjectId.value) return
  projectTouched.value = true
  projectHint.value = ''
}

/** 标题停一下后，按项目名和同事近期任务建议归属；手动选过就不再改 */
watch(
  () => [form.value.title, form.value.goal] as const,
  ([title, goal]) => {
    if (projectGuessTimer) clearTimeout(projectGuessTimer)
    if (similarCheckTimer) clearTimeout(similarCheckTimer)
    const text = (title || '').trim()
    const extra = (goal || '').trim()
    if (text.length < 2 && extra.length < 2) {
      if (!projectTouched.value) projectHint.value = ''
      similarHint.value = ''
      return
    }
    if (!projectTouched.value) {
      projectGuessTimer = setTimeout(async () => {
        if (projectTouched.value) return
        try {
          const { data } = await api.post('/tasks/guess-project', {
            title: text,
            goal: extra,
          })
          if (projectTouched.value) return
          if (data?.project_id) {
            lastAutoProjectId.value = data.project_id
            form.value.project_id = data.project_id
            const why = data.reason ? `（${data.reason}）` : ''
            projectHint.value = data.project_name
              ? `已建议归入「${data.project_name}」${why}，可以手动改`
              : `已建议项目${why}，可以手动改`
          }
        } catch {
          /* 猜不到就保持当前选择 */
        }
      }, 500)
    }
    similarCheckTimer = setTimeout(async () => {
      try {
        const { data } = await api.post<CheckSimilarOut>('/tasks/check-similar', {
          title: text,
          goal: extra,
          project_id: form.value.project_id,
          assignee_id: form.value.assignee_id,
          assignment_type: isDelegatingOther.value ? 'delegated' : 'self',
        })
        if (data.vague) {
          similarHint.value = data.message || '标题太笼统，请写清具体事项'
        } else if (data.level === 'hard' && data.similar?.[0]) {
          similarHint.value = `已有高度相似未完成任务「${data.similar[0].title}」，建议打开已有任务`
        } else if (data.level === 'soft' && data.similar?.[0]) {
          similarHint.value = `疑似重复「${data.similar[0].title}」，请想清楚是否还要新建`
        } else if (data.continuation_warning) {
          similarHint.value = data.continuation_warning
        } else {
          similarHint.value = ''
        }
      } catch {
        similarHint.value = ''
      }
    }, 500)
  },
)

async function runAiDraft() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const text = aiText.value.trim()
  if (!text) {
    ElMessage.warning('先写一句话，例如：跟进开票，周三前完成')
    return
  }
  aiLoading.value = true
  try {
    const { data } = await api.post('/tasks/ai/draft', { text })
    form.value.title = data.title || text
    if (data.goal) form.value.goal = data.goal
    if (data.project_id) {
      form.value.project_id = data.project_id
      await loadMembers(data.project_id)
    }
    if (canAssign.value && data.assignee_id) {
      form.value.assignee_id = data.assignee_id
    }
    if (Array.isArray(data.assistant_ids)) {
      form.value.assistant_ids = data.assistant_ids.filter(
        (id: number) => id !== effectiveOwnerId.value,
      )
    }
    if (data.priority) form.value.priority = data.priority
    if (data.due_date) form.value.due_date = data.due_date
    if (data.accept_by) form.value.accept_by = String(data.accept_by).slice(0, 19)
    aiHint.value = data.rationale
      ? `${data.rationale}（来源：${data.source === 'llm' ? 'AI' : '规则'}）`
      : ''
    ElMessage.success('已生成草稿，请确认后创建')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || 'AI 草稿失败')
  } finally {
    aiLoading.value = false
  }
}

function successMessage(data: any) {
  const parts: string[] = []
  if (isDelegatingOther.value) {
    const name = data?.assignee?.name || '对方'
    parts.push(`已交办给${name}，已进入其个人待办与部门待办`)
    if (data?.dingtalk_notified) parts.push('已发钉钉通知')
    else parts.push('钉钉未通知（对方未绑定或未配置工作通知）')
  } else {
    parts.push('已加入我的待办')
  }
  if (data?.synced_to_week_draft) {
    parts.push(isDelegatingOther.value ? '已写入对方本周周报草稿' : '已写入本周周报草稿')
  } else {
    parts.push('本周周报已提交，本次未写入草稿（不改已交内容）')
  }
  return parts.join('；')
}

async function createWithPayload(payload: Record<string, unknown>) {
  submitting.value = true
  try {
    const { data } = await api.post('/tasks', payload)
    ElMessage.success(successMessage(data))
    sessionStorage.removeItem(WRITE_TASK_DRAFT_KEY)
    router.push(props.embedded ? WRITE_TASK_RETURN : '/personal')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409 && detail && typeof detail === 'object') {
      const sim = (detail.similar || []) as SimilarTask[]
      const first = sim[0]
      const msg = detail.message || '已有相似未完成任务'
      try {
        await ElMessageBox.confirm(
          first
            ? `${msg}\n已有：#${first.id} ${first.title}`
            : msg,
          '疑似重复任务',
          {
            type: 'warning',
            confirmButtonText: first ? '打开已有' : '知道了',
            cancelButtonText: '仍要新建',
            distinguishCancelAndClose: true,
          },
        )
        if (first) {
          router.push(`/tasks/${first.id}`)
          return
        }
      } catch (action: any) {
        if (action === 'cancel') {
          try {
            await ElMessageBox.confirm(
              '确定仍要新建？请确认不是同一件事。',
              '二次确认',
              { type: 'warning', confirmButtonText: '仍要新建', cancelButtonText: '取消' },
            )
            await createWithPayload({ ...payload, force_create: true })
          } catch {
            /* 取消 */
          }
        }
      }
      return
    }
    const text =
      typeof detail === 'string'
        ? detail
        : detail?.message || e?.message || '创建失败'
    ElMessage.error(text)
  } finally {
    submitting.value = false
  }
}

async function submit() {
  if (!canSelf.value) {
    ElMessage.error('无写任务权限')
    return
  }
  if (!form.value.project_id || !form.value.title.trim()) {
    ElMessage.warning('请填写项目与标题')
    return
  }
  const owner = effectiveOwnerId.value
  const assistants = form.value.assistant_ids.filter((id) => id !== owner)
  const delegated = isDelegatingOther.value
  const payload: Record<string, unknown> = {
    project_id: form.value.project_id,
    title: form.value.title.trim(),
    goal: form.value.goal.trim(),
    due_date: form.value.due_date || null,
    phase_id: form.value.phase_id || null,
    priority: form.value.priority || 'p3',
    assistant_ids: assistants,
    assignment_type: delegated ? 'delegated' : 'self',
  }
  if (delegated) {
    payload.assignee_id = form.value.assignee_id
    if (form.value.accept_by) {
      payload.accept_by = new Date(form.value.accept_by).toISOString()
    }
  }

  try {
    const { data: check } = await api.post<CheckSimilarOut>('/tasks/check-similar', {
      title: payload.title,
      goal: payload.goal,
      project_id: payload.project_id,
      assignee_id: payload.assignee_id ?? null,
      assignment_type: payload.assignment_type,
    })
    if (check.vague) {
      await ElMessageBox.alert(check.message || '请写清具体事项', '请想清楚', {
        type: 'warning',
        confirmButtonText: '去修改',
      })
      return
    }
    if (check.level === 'hard' && check.similar?.[0]) {
      const first = check.similar[0]
      try {
        await ElMessageBox.confirm(
          `${check.message || '已有高度相似未完成任务'}\n已有：#${first.id} ${first.title}`,
          '疑似重复任务',
          {
            type: 'warning',
            confirmButtonText: '打开已有',
            cancelButtonText: '仍要新建',
            distinguishCancelAndClose: true,
          },
        )
        router.push(`/tasks/${first.id}`)
        return
      } catch (action: any) {
        if (action !== 'cancel') return
        try {
          await ElMessageBox.confirm(
            '确定仍要新建？请确认不是同一件事。',
            '二次确认',
            { type: 'warning', confirmButtonText: '仍要新建', cancelButtonText: '取消' },
          )
          payload.force_create = true
        } catch {
          return
        }
      }
    } else if (check.level === 'soft' && check.similar?.[0]) {
      try {
        await ElMessageBox.confirm(
          `${check.message || '疑似重复'}（「${check.similar[0].title}」），仍要创建吗？`,
          '请想清楚',
          {
            type: 'info',
            confirmButtonText: '仍要创建',
            cancelButtonText: '再改改',
          },
        )
      } catch {
        return
      }
    } else if (check.continuation_warning) {
      try {
        await ElMessageBox.confirm(check.continuation_warning, '续推提示', {
          type: 'info',
          confirmButtonText: '知道了，继续创建',
          cancelButtonText: '再改改',
        })
      } catch {
        return
      }
    }
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') {
      /* check-similar 失败不阻断，交给创建接口兜底 */
    }
  }

  await createWithPayload(payload)
}

onMounted(async () => {
  if (!userStore.user) await userStore.fetchMe()
  if (!canSelf.value) {
    ElMessage.error('无写任务权限')
    return
  }
  const restored = restoreDraft()
  if (restored && form.value.project_id) projectTouched.value = true
  const rawId = route.query.selectProjectId
  const selectId =
    typeof rawId === 'string' && Number.isFinite(Number(rawId)) && Number(rawId) > 0
      ? Number(rawId)
      : null
  await loadProjects({
    preferProjectId: selectId,
    skipDefault: restored,
  })
  if (selectId) {
    const name = projects.value.find((p) => p.id === selectId)?.name
    ElMessage.success(`已选中新项目「${name || '#' + selectId}」`)
    const nextQuery = { ...route.query }
    delete nextQuery.selectProjectId
    // 嵌入模式下保持写任务 Tab
    if (props.embedded && !nextQuery.tab) nextQuery.tab = 'write'
    router.replace({ path: route.path, query: nextQuery })
  }
})
</script>

<template>
  <div v-loading="loading">
    <template v-if="!embedded">
      <h1 class="wb-page-title">写任务</h1>
      <p class="wb-page-desc">
        保存后进入待办，并自动写入负责人本周周报草稿。
        <template v-if="canAssign">可选责任人：不选则任务归自己；选他人即为交办（会钉钉通知对方）。</template>
        <template v-else>你可为自己建任务，并选择辅助人。</template>
      </p>
    </template>
    <p v-else class="wb-page-desc embed-desc">
      <template v-if="canAssign">可选责任人：不选则任务归自己；选他人即为交办（会钉钉通知对方）。</template>
      <template v-else>你可为自己建任务，并选择辅助人。</template>
    </p>

    <div class="panel" :class="{ 'panel--embed': embedded }">
      <AgentNativeHint v-if="agentNative" tip="建任务请到「我的助手」里说，确认后再写入账本。" />
      <div v-else class="ai-box">
        <div class="label">一句话创建（可选）</div>
        <el-input
          v-model="aiText"
          type="textarea"
          :rows="2"
          placeholder="例：跟进开票，周三前完成；或交办张三明天前接收"
        />
        <el-button
          type="primary"
          plain
          style="margin-top:8px"
          :loading="aiLoading"
          @click="runAiDraft"
        >
          AI 生成草稿
        </el-button>
        <p v-if="aiHint" class="hint">{{ aiHint }}</p>
      </div>

      <el-form label-width="100px" style="margin-top:16px; max-width:640px">
        <el-form-item label="项目" required>
          <div class="project-pick">
            <el-select
              v-model="form.project_id"
              filterable
              clearable
              placeholder="输入关键字搜索项目"
              style="flex:1;min-width:0"
              @change="markProjectTouched"
            >
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button
              v-if="canCreateProject"
              link
              type="primary"
              @click="goCreateProject"
            >
              没有？去新建
            </el-button>
          </div>
          <div v-if="projectHint && !projectTouched" class="field-hint">{{ projectHint }}</div>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="任务标题（勿只写「继续推进」）" />
          <div v-if="similarHint" class="field-hint warn-hint">{{ similarHint }}</div>
        </el-form-item>
        <el-form-item label="目标">
          <el-input
            v-model="form.goal"
            type="textarea"
            :rows="2"
            placeholder="做成什么样 / 做到什么程度"
          />
        </el-form-item>
        <el-form-item label="级别">
          <el-select v-model="form.priority" style="width:100%">
            <el-option
              v-for="(label, key) in TASK_PRIORITY_MAP"
              :key="key"
              :label="key === 'p0' ? `${label}（最高，会提醒责任人）` : label"
              :value="key"
            />
          </el-select>
          <div class="field-hint">{{ TASK_PRIORITY_HINT }}</div>
        </el-form-item>
        <el-form-item label="所属阶段">
          <el-select
            v-model="form.phase_id"
            filterable
            clearable
            placeholder="可选，对应项目进度阶段"
            style="width:100%"
            :disabled="!form.project_id"
          >
            <el-option v-for="p in phases" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止日期">
          <el-date-picker
            v-model="form.due_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="可选"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item v-if="canAssign" label="责任人">
          <el-select
            v-model="form.assignee_id"
            filterable
            clearable
            placeholder="输入关键字搜索；不选则归自己"
            style="width:100%"
          >
            <el-option
              v-for="m in members"
              :key="m.id"
              :label="m.id === myId ? `${m.name}（我自己）` : m.name"
              :value="m.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="isDelegatingOther" label="接收时限">
          <el-date-picker
            v-model="form.accept_by"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="请于何时前接收（可选）"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="辅助人">
          <el-select
            v-model="form.assistant_ids"
            multiple
            filterable
            clearable
            placeholder="输入关键字搜索同项目成员"
            style="width:100%"
          >
            <el-option v-for="m in assistantOptions" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">确认创建</el-button>
          <el-button @click="cancelWrite">取消</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.embed-desc {
  margin-top: 0;
  margin-bottom: 12px;
}
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px 20px;
  box-shadow: var(--wb-shadow-soft);
}
.panel--embed {
  margin-top: 0;
}
.ai-box .label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--wb-text-primary);
}
.hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
.warn-hint {
  color: #b45309;
}
.project-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
</style>
