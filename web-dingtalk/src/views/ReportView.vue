<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { getToken, request } from '@/utils/api'
import SearchableSelect from '@/components/SearchableSelect.vue'
import AgentNativeHint from '@/components/AgentNativeHint.vue'
import { isAgentNativeProduct } from '@/productLine'

type Project = { id: number; name: string }

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const agentNative = isAgentNativeProduct()
const error = ref('')
const projects = ref<Project[]>([])
const projectId = ref<number | null>(null)
const projectOptions = computed(() =>
  projects.value.map((p) => ({ value: p.id, label: p.name })),
)
const taskTitle = ref('')
const resultText = ref('')
const stuck = ref(false)
const stuckDetail = ref('')
const nextGoal = ref('')
const nextDueDate = ref('')
const chainId = ref<string | null>(null)
const linkedTaskId = ref<number | null>(null)
const submitting = ref(false)
const savingDraft = ref(false)
/** 当前加载的周报周期（默认本周，可点选历史） */
const activePeriodStart = ref('')
const activePeriodEnd = ref('')
const mineItems = ref<any[]>([])
const mineStatus = ref('')
const reportId = ref<number | null>(null)
const attachments = ref<
  Array<{ id: number; original_filename: string; uploader_name?: string }>
>([])
const attachUploading = ref(false)
const periodHint = ref('')
const myPeriods = ref<
  Array<{ period_start: string; period_end?: string; status: string; item_count: number }>
>([])
const forceEditSubmitted = ref(false)
const phase1Review = ref<any | null>(null)
const reviewUnavailable = ref('')

const suggestLoading = ref(false)
const suggestion = ref<any>(null)
const linkTaskChecked = ref(false)
const createChainChecked = ref(true)
const lastFp = ref('')

const timelineOpen = ref(false)
const timelineLoading = ref(false)
const timeline = ref<any>(null)
/** 默认只留三问；AI、任务链、附件、已写内容收在这里 */
const moreOpen = ref(false)

const weekTitle = computed(() => {
  const start = activePeriodStart.value
  if (!start) return '写周报'
  const d = new Date(`${start}T12:00:00`)
  if (Number.isNaN(d.getTime())) return '写周报'
  const y0 = new Date(d.getFullYear(), 0, 1)
  const days = Math.floor((d.getTime() - y0.getTime()) / 86400000)
  const wn = Math.ceil((days + y0.getDay() + 1) / 7)
  return `第${wn}周周报`
})

const stuckCount = computed(
  () => mineItems.value.filter((it) => it.stuck_status && it.stuck_status !== 'none').length,
)

function localYMD(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function mondayOfThisWeek() {
  const d = new Date()
  const day = d.getDay() || 7
  const m = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  m.setDate(m.getDate() - day + 1)
  return localYMD(m)
}

function sundayOf(fromMonday: string) {
  const d = new Date(fromMonday + 'T12:00:00')
  d.setDate(d.getDate() + 6)
  return localYMD(d)
}

function nextFriday(fromMonday: string) {
  const d = new Date(fromMonday + 'T12:00:00')
  d.setDate(d.getDate() + 4)
  return localYMD(d)
}

function isHistoricalSubmittedPeriod() {
  return mineStatus.value === 'submitted' && activePeriodStart.value < mondayOfThisWeek()
}

function formReadOnly() {
  return isHistoricalSubmittedPeriod() && !forceEditSubmitted.value
}

function unlockHistoricalEdit() {
  forceEditSubmitted.value = true
  periodHint.value = '已解锁历史已交周期，保存将覆盖原内容'
}

function ensureCurrentWeekChip(
  list: Array<{ period_start: string; period_end?: string; status: string; item_count: number }>,
) {
  const cur = mondayOfThisWeek()
  if (list.some((p) => p.period_start === cur)) return list
  return [
    { period_start: cur, period_end: sundayOf(cur), status: 'draft', item_count: 0 },
    ...list,
  ]
}

function fingerprint() {
  return `${taskTitle.value.trim()}||${resultText.value.trim()}`
}

function shouldTrigger() {
  const title = taskTitle.value.trim()
  const result = resultText.value.trim()
  return title.length >= 4 || result.length >= 8
}

async function runStructure() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  if (!shouldTrigger()) {
    alert('先写任务概述或做成了什么，再整理')
    return
  }
  const fp = fingerprint()
  if (fp === lastFp.value && suggestion.value) return
  suggestLoading.value = true
  try {
    const periodStart = activePeriodStart.value || mondayOfThisWeek()
    const data = await request<any>('/progress-reports/ai-structure', 'POST', {
      period_type: 'week',
      period_start: periodStart,
      period_end: activePeriodEnd.value || sundayOf(periodStart),
      chain_id: chainId.value,
      item: {
        project_id: projectId.value,
        chain_id: chainId.value,
        linked_task_id: linkedTaskId.value,
        task_title: taskTitle.value.trim(),
        result_text: resultText.value.trim(),
        stuck_status: stuck.value ? 'blocker' : 'none',
        stuck_detail: stuck.value ? stuckDetail.value : '',
        next_goal: nextGoal.value.trim(),
        next_due_date: nextDueDate.value || null,
        risk_level: 'normal',
        sort_order: 0,
      },
    })
    lastFp.value = fp
    suggestion.value = data
    linkTaskChecked.value = !!data.task_suggestion
    createChainChecked.value = !!data.create_new_chain && !data.chain_suggestion && !chainId.value
  } catch (err) {
    /* 静默：不打断手机填写 */
    console.warn(err)
  } finally {
    suggestLoading.value = false
  }
}

async function applySuggestion() {
  const data = suggestion.value
  if (!data) return
  const s = data.structured || {}
  if (s.task_title) taskTitle.value = s.task_title
  if (s.result_text) resultText.value = s.result_text
  if (s.next_goal) nextGoal.value = s.next_goal
  if (s.next_due_date) nextDueDate.value = s.next_due_date
  if (s.stuck_status && s.stuck_status !== 'none') {
    stuck.value = true
    stuckDetail.value = s.stuck_detail || stuckDetail.value
  }
  if (data.project_suggestion?.project_id) {
    projectId.value = data.project_suggestion.project_id
  }
  if (data.chain_suggestion?.chain_id) {
    chainId.value = data.chain_suggestion.chain_id
    if (data.chain_suggestion.seed_item_id) {
      try {
        await request('/progress-reports/chains/link-history', 'POST', {
          chain_id: data.chain_suggestion.chain_id,
          seed_item_id: data.chain_suggestion.seed_item_id,
        })
      } catch {
        /* ignore */
      }
    }
  } else if (createChainChecked.value && !chainId.value) {
    chainId.value = crypto.randomUUID()
  }
  if (linkTaskChecked.value && data.task_suggestion?.task_id) {
    linkedTaskId.value = data.task_suggestion.task_id
    if (data.task_suggestion.project_id) {
      projectId.value = data.task_suggestion.project_id
    }
  }
  suggestion.value = null
  alert('已采用整理建议')
}

function dismissSuggestion() {
  suggestion.value = null
}

async function openTimeline() {
  if (!chainId.value) return
  timelineOpen.value = true
  timelineLoading.value = true
  timeline.value = null
  try {
    timeline.value = await request(`/progress-reports/chains/${chainId.value}`)
  } catch (err) {
    alert(err instanceof Error ? err.message : '加载任务链失败')
    timelineOpen.value = false
  } finally {
    timelineLoading.value = false
  }
}

async function loadPeriod(periodStart: string, periodEnd?: string) {
  forceEditSubmitted.value = false
  activePeriodStart.value = periodStart
  activePeriodEnd.value = periodEnd || sundayOf(periodStart)
  nextDueDate.value = nextFriday(periodStart)
  periodHint.value = ''
  const mine = await request<any>('/progress-reports/mine', 'GET', {
    period_type: 'week',
    period_start: periodStart,
    period_end: activePeriodEnd.value,
  })
  mineItems.value = mine?.items || []
  mineStatus.value = mine?.status || ''
  reportId.value = mine?.id || null
  attachments.value = mine?.attachments || []
  phase1Review.value = mine?.phase1_review || null
  reviewUnavailable.value = mine?.review_unavailable_reason || ''
  if (mine?.period_start) {
    activePeriodStart.value = mine.period_start
    activePeriodEnd.value = mine.period_end || mine.period_start
  }
  if (isHistoricalSubmittedPeriod() && !forceEditSubmitted.value) {
    periodHint.value = '历史已交周期，仅查看；点下方「解锁修改」后可改'
  }
  const it = mine?.items?.[0]
  if (it) {
    projectId.value = it.project_id || projectId.value
    taskTitle.value = it.task_title || ''
    resultText.value = it.result_text || ''
    nextGoal.value = it.next_goal || ''
    nextDueDate.value = it.next_due_date || nextDueDate.value
    stuck.value = !!(it.stuck_status && it.stuck_status !== 'none')
    stuckDetail.value = it.stuck_detail || ''
    chainId.value = it.chain_id || null
    linkedTaskId.value = it.linked_task_id || null
  } else if (periodStart === mondayOfThisWeek()) {
    const prefill = await request<{
      items?: Array<{
        project_id?: number
        task_title?: string
        result_text?: string
        next_goal?: string
        next_due_date?: string
        stuck_status?: string
        stuck_detail?: string
        chain_id?: string
        linked_task_id?: number
      }>
    }>('/progress-reports/prefill', 'GET', {
      period_type: 'week',
      period_start: periodStart,
    })
    const p = prefill?.items?.[0]
    if (p) {
      projectId.value = p.project_id || projectId.value
      taskTitle.value = p.task_title || ''
      resultText.value = p.result_text || ''
      nextGoal.value = p.next_goal || ''
      nextDueDate.value = p.next_due_date || nextDueDate.value
      stuck.value = !!(p.stuck_status && p.stuck_status !== 'none')
      stuckDetail.value = p.stuck_detail || ''
      chainId.value = p.chain_id || null
      linkedTaskId.value = p.linked_task_id || null
    }
  }
}

onMounted(async () => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  const qStart = typeof route.query.period_start === 'string' ? route.query.period_start : ''
  const qEnd = typeof route.query.period_end === 'string' ? route.query.period_end : ''
  const periodStart = qStart || mondayOfThisWeek()
  activePeriodStart.value = periodStart
  activePeriodEnd.value = qEnd || sundayOf(periodStart)
  nextDueDate.value = nextFriday(periodStart)
  try {
    const data = await request<{ my_projects?: Project[] }>('/personal/workspace')
    projects.value = (data.my_projects || []).slice().sort((a, b) => {
      if (a.name === '日常任务') return -1
      if (b.name === '日常任务') return 1
      return (a.name || '').localeCompare(b.name || '', 'zh')
    })
    const daily = projects.value.find((p) => p.name === '日常任务')
    projectId.value = daily?.id ?? projects.value[0]?.id ?? null
    try {
      const periods = await request<
        Array<{ period_start: string; period_end?: string; status: string; item_count: number }>
      >('/progress-reports/my-periods', 'GET', { period_type: 'week' })
      myPeriods.value = ensureCurrentWeekChip(periods || [])
      await loadPeriod(periodStart, activePeriodEnd.value)
    } catch {
      /* 预填失败可手写 */
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
})

function isVagueContTitle(title: string): boolean {
  const t = (title || '').trim()
  if (!t) return false
  if (['继续推进', '持续推进', '继续跟进', '持续跟进', '继续', '下一步', '持续'].includes(t)) {
    return true
  }
  const prefixes = [
    '继续推进',
    '继续跟进',
    '继续开展',
    '持续推进',
    '持续跟进',
    '接着推进',
  ]
  const hit = prefixes.some((p) => t.startsWith(p)) || t.startsWith('继续')
  if (!hit) return false
  let core = t
  for (const p of prefixes) {
    if (core.startsWith(p)) {
      core = core.slice(p.length)
      break
    }
  }
  if (core.startsWith('继续')) core = core.slice(2)
  core = core.replace(/^[：:，,、\s\-—]+/, '').replace(/[：:，,、.。！!？?\s\-—]/g, '')
  return core.length < 4
}

function confirmVagueIfNeeded(title: string): boolean {
  if (!isVagueContTitle(title)) return true
  return confirm(
    '标题太笼统（如「继续推进」）。请想清楚具体事项后再写；仍要保存吗？',
  )
}

async function saveDraft() {
  if (formReadOnly()) {
    alert('历史已交周期只读，请先解锁修改')
    return
  }
  if (!projectId.value) {
    alert('请选择所属项目（杂项请选「日常任务」）')
    return
  }
  const draftTitle =
    taskTitle.value.trim() ||
    projects.value.find((p) => p.id === projectId.value)?.name ||
    ''
  if (!confirmVagueIfNeeded(draftTitle)) return
  savingDraft.value = true
  try {
    const rid = await ensureReportId()
    if (rid) {
      mineStatus.value = 'draft'
      alert('草稿已保存')
    }
  } finally {
    savingDraft.value = false
  }
}

async function pickPeriod(p: { period_start: string; period_end?: string }) {
  try {
    await loadPeriod(p.period_start, p.period_end)
  } catch (err) {
    alert(err instanceof Error ? err.message : '切换周期失败')
  }
}

async function submit() {
  if (formReadOnly()) {
    alert('历史已交周期只读，请先解锁修改')
    return
  }
  if (!projectId.value) {
    alert('请选择所属项目（杂项请选「日常任务」）')
    return
  }
  if (!resultText.value.trim() || !nextGoal.value.trim() || !nextDueDate.value) {
    alert('请填写：做成了什么、下一步、完成日期')
    return
  }
  const title =
    taskTitle.value.trim() ||
    projects.value.find((p) => p.id === projectId.value)?.name ||
    '本周进展'
  if (!confirmVagueIfNeeded(title)) return
  submitting.value = true
  try {
    // 保存到当前选中的周期；本周默认 mondayOfThisWeek，点选历史则用 activePeriodStart
    const periodStart = activePeriodStart.value || mondayOfThisWeek()
    const saved = await request<any>('/progress-reports', 'POST', {
      period_type: 'week',
      period_start: periodStart,
      period_end: activePeriodEnd.value || sundayOf(periodStart),
      theme: title,
      force_edit_submitted: forceEditSubmitted.value && isHistoricalSubmittedPeriod(),
      items: [
        {
          project_id: projectId.value,
          chain_id: chainId.value,
          linked_task_id: linkedTaskId.value,
          task_title: title.slice(0, 120),
          no_action: false,
          result_text: resultText.value.trim(),
          stuck_status: stuck.value ? 'blocker' : 'none',
          stuck_detail: stuck.value ? stuckDetail.value || '有卡点' : '',
          stuck_next_action: stuck.value ? '跟进卡点' : '',
          stuck_follow_up: stuck.value ? nextDueDate.value : null,
          next_goal: nextGoal.value.trim(),
          next_due_date: nextDueDate.value,
          risk_level: 'normal',
          sort_order: 0,
        },
      ],
      submit: true,
    })
    reportId.value = saved?.id || null
    attachments.value = saved?.attachments || []
    mineStatus.value = saved?.status || 'submitted'
    alert('周报已提交')
  } catch (err) {
    alert(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

async function ensureReportId(): Promise<number | null> {
  if (reportId.value) return reportId.value
  if (formReadOnly()) {
    alert('历史已交周期只读，请先解锁修改')
    return null
  }
  // 草稿/附件绑定当前选中周期；默认进入时即本周
  const periodStart = activePeriodStart.value || mondayOfThisWeek()
  const title =
    taskTitle.value.trim() ||
    projects.value.find((p) => p.id === projectId.value)?.name ||
    '本周进展'
  try {
    const saved = await request<any>('/progress-reports', 'POST', {
      period_type: 'week',
      period_start: periodStart,
      period_end: activePeriodEnd.value || sundayOf(periodStart),
      theme: title,
      force_edit_submitted: forceEditSubmitted.value && isHistoricalSubmittedPeriod(),
      items: [
        {
          project_id: projectId.value,
          chain_id: chainId.value,
          linked_task_id: linkedTaskId.value,
          task_title: title.slice(0, 120),
          no_action: false,
          result_text: resultText.value.trim() || '（草稿）',
          stuck_status: 'none',
          next_goal: nextGoal.value.trim() || '待补充',
          next_due_date: nextDueDate.value || null,
          risk_level: 'normal',
          sort_order: 0,
        },
      ],
      submit: false,
    })
    reportId.value = saved?.id || null
    attachments.value = saved?.attachments || []
    return reportId.value
  } catch (err) {
    alert(err instanceof Error ? err.message : '请先填写并保存草稿再上传')
    return null
  }
}

async function onAttachFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!projectId.value) {
    alert('请先选择所属项目')
    return
  }
  const rid = await ensureReportId()
  if (!rid) return
  attachUploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const base = import.meta.env.VITE_API_BASE || '/aiworkbench/api'
    const res = await fetch(`${base}/progress-reports/${rid}/attachments`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
      body: form,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : '上传失败')
    }
    attachments.value = await request(`/progress-reports/${rid}/attachments`)
    alert('附件已上传')
  } catch (err) {
    alert(err instanceof Error ? err.message : '上传失败')
  } finally {
    attachUploading.value = false
  }
}

async function removeAttach(id: number) {
  if (!reportId.value) return
  try {
    await request(`/progress-reports/${reportId.value}/attachments/${id}`, 'DELETE')
    attachments.value = attachments.value.filter((a) => a.id !== id)
  } catch (err) {
    alert(err instanceof Error ? err.message : '删除失败')
  }
}

async function downloadAttach(a: { id: number; original_filename: string }) {
  if (!reportId.value) return
  const base = import.meta.env.VITE_API_BASE || '/aiworkbench/api'
  try {
    const res = await fetch(
      `${base}/progress-reports/${reportId.value}/attachments/${a.id}/download`,
      { headers: { Authorization: `Bearer ${getToken()}` } },
    )
    if (!res.ok) throw new Error('下载失败')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = a.original_filename || '附件'
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    alert(err instanceof Error ? err.message : '下载失败')
  }
}
</script>

<template>
  <div class="page report-write no-tab">
    <header class="write-head">
      <button type="button" class="back-ico" aria-label="返回" @click="router.back()">←</button>
      <h1 class="write-title">{{ weekTitle }}</h1>
      <button
        type="button"
        class="head-submit"
        :disabled="submitting || formReadOnly()"
        @click="submit"
      >
        提交
      </button>
    </header>

    <div v-if="error" class="deny">{{ error }}</div>
    <template v-else>
      <div class="card stats-card">
        <div class="stat">
          <div class="n">{{ mineItems.length }}</div>
          <div class="l">已写条目</div>
        </div>
        <div class="stat">
          <div class="n">{{ stuckCount }}</div>
          <div class="l">卡点</div>
        </div>
        <div class="stat">
          <div class="n">{{ mineStatus === 'submitted' ? '已交' : '草稿' }}</div>
          <div class="l">状态</div>
        </div>
      </div>

      <div v-if="periodHint" class="tip">
        {{ periodHint }}
        <button
          v-if="isHistoricalSubmittedPeriod() && !forceEditSubmitted"
          type="button"
          class="link"
          style="margin-left:8px"
          @click="unlockHistoricalEdit"
        >
          解锁修改
        </button>
      </div>
      <div v-else-if="activePeriodStart" class="tip">
        当前周期：{{ activePeriodStart }} ~ {{ activePeriodEnd || activePeriodStart }}
      </div>

      <div v-if="myPeriods.length" class="card" style="margin-bottom:12px">
        <div class="sec-title">我的周期（点选切换）</div>
        <div class="period-chips">
          <button
            v-for="p in myPeriods"
            :key="p.period_start"
            type="button"
            class="chip"
            :class="{ on: p.period_start === activePeriodStart }"
            @click="pickPeriod(p)"
          >
            {{ p.period_start }}
            · {{ p.status === 'submitted' ? '已交' : p.item_count ? '草稿' : '未写' }}
            · {{ p.item_count }}条
          </button>
        </div>
      </div>

      <fieldset :disabled="formReadOnly()" class="form-fields card">
      <div class="field">
        <div class="field-label">所属项目（必选）</div>
        <SearchableSelect
          v-model="projectId"
          :options="projectOptions"
          placeholder="输入关键字搜索项目"
        />
      </div>
      <div class="field">
        <div class="field-label">任务概述</div>
        <input
          v-model="taskTitle"
          class="input"
          maxlength="120"
          placeholder="例如：天网接入冲量"
        />
      </div>
      <div class="field">
        <div class="field-label">1. 做成了什么</div>
        <textarea v-model="resultText" class="textarea" placeholder="本周成果" />
      </div>
      <div class="field">
        <div class="field-label">2. 是否有卡点</div>
        <button type="button" class="chip" :class="{ on: stuck }" @click="stuck = !stuck">
          {{ stuck ? '有卡点' : '无卡点' }}
        </button>
        <textarea
          v-if="stuck"
          v-model="stuckDetail"
          class="textarea"
          style="margin-top:8px"
          placeholder="卡点说明"
        />
      </div>
      <div class="field">
        <div class="field-label">3. 下一步，哪天完成</div>
        <textarea v-model="nextGoal" class="textarea" placeholder="下一步做什么" />
        <input v-model="nextDueDate" class="input" type="date" style="margin-top:8px" />
      </div>
      </fieldset>

      <div class="tip tip-box">
        按任务写：概述 + 做成了什么 / 卡点 / 下一步。写完可点「帮我整理」，采用后才写入。
      </div>

      <button type="button" class="link more-toggle" @click="moreOpen = !moreOpen">
        {{ moreOpen ? '收起更多' : '更多（已写内容、整理建议、附件）' }}
      </button>

      <template v-if="moreOpen">
        <div v-if="phase1Review" class="card review-card">
          <div class="sec-title">对本期周报的评价</div>
          <p>{{ phase1Review.summary }}</p>
          <ul v-if="phase1Review.strengths?.length">
            <li v-for="(s, i) in phase1Review.strengths" :key="'st' + i">优点：{{ s }}</li>
          </ul>
          <ul v-if="phase1Review.risks?.length">
            <li v-for="(s, i) in phase1Review.risks" :key="'rk' + i">关注：{{ s }}</li>
          </ul>
          <p v-if="phase1Review.completion_comment" class="muted">{{ phase1Review.completion_comment }}</p>
          <p class="muted">来源：{{ phase1Review.source === 'llm' ? 'AI' : '规则' }}</p>
        </div>
        <div v-else-if="reviewUnavailable" class="tip">{{ reviewUnavailable }}</div>

        <div v-if="mineItems.length" class="card mine-list">
          <div class="sec-title">本周期已写（{{ mineItems.length }} 条）</div>
          <div v-for="it in mineItems" :key="it.id || it.task_title" class="mine-item">
            <div class="item-title">{{ it.task_title || '未命名任务' }}</div>
            <button
              v-if="it.linked_task_id"
              type="button"
              class="link"
              @click="$router.push(`/tasks/${it.linked_task_id}`)"
            >
              关联任务 #{{ it.linked_task_id }} →
            </button>
            <template v-if="!it.no_action">
              <div>做成了：{{ it.result_text || '—' }}</div>
              <div>下一步：{{ it.next_goal || '—' }}（{{ it.next_due_date || '未定日' }}）</div>
              <div v-if="it.stuck_status && it.stuck_status !== 'none'" class="stuck">
                卡点：{{ it.stuck_detail || '—' }}
              </div>
            </template>
            <div v-else class="muted">本周此项无实质动作</div>
          </div>
        </div>

        <div v-if="chainId" class="chain-bar">
          <span>已入任务链</span>
          <button type="button" class="link" @click="openTimeline">查看进度</button>
        </div>
        <div v-if="linkedTaskId" class="chain-bar">
          <span>关联任务 #{{ linkedTaskId }}</span>
          <button type="button" class="link" @click="$router.push(`/tasks/${linkedTaskId}`)">
            打开任务 →
          </button>
        </div>
        <AgentNativeHint v-if="agentNative" tip="周报整理请到「我的助手」里说，确认后再写入账本。" />
        <button v-else type="button" class="btn btn-ghost" :disabled="suggestLoading" @click="runStructure">
          {{ suggestLoading ? '整理中…' : '帮我整理' }}
        </button>
        <div v-if="suggestion" class="suggest">
          <div class="suggest-title">整理建议（需点采用）</div>
          <p class="hint">{{ suggestion.message }}</p>
          <div class="suggest-line">
            <strong>{{ suggestion.structured?.task_title || '（无概述）' }}</strong>
          </div>
          <div class="suggest-line">做成了：{{ suggestion.structured?.result_text || '…' }}</div>
          <div class="suggest-line">下一步：{{ suggestion.structured?.next_goal || '…' }}</div>
          <div v-if="suggestion.project_suggestion" class="suggest-line">
            疑似项目：{{ suggestion.project_suggestion.name }}
          </div>
          <div v-if="suggestion.continuation_warning" class="suggest-line warn">
            {{ suggestion.continuation_warning }}
          </div>
          <div v-if="suggestion.chain_suggestion" class="suggest-line">
            疑似任务链：{{ suggestion.chain_suggestion.title }}
            <span class="hint"> · {{ suggestion.chain_suggestion.reason }}</span>
          </div>
          <label v-else-if="!chainId" class="check">
            <input v-model="createChainChecked" type="checkbox" />
            采用时新建任务链
          </label>
          <label v-if="suggestion.task_suggestion" class="check">
            <input v-model="linkTaskChecked" type="checkbox" />
            挂到交办「{{ suggestion.task_suggestion.title }}」
          </label>
          <div class="suggest-actions">
            <button type="button" class="btn btn-sm" @click="applySuggestion">采用</button>
            <button type="button" class="btn btn-ghost btn-sm" @click="dismissSuggestion">忽略</button>
          </div>
        </div>

        <div class="card" style="margin-top:12px">
          <div class="field-label">附件</div>
          <p class="muted">可上传 PDF、Office、图片。没有草稿时会先自动保存草稿。</p>
          <label class="btn btn-ghost btn-sm" style="display:inline-block;margin-top:8px">
            {{ attachUploading ? '上传中…' : '上传附件' }}
            <input type="file" hidden :disabled="attachUploading" @change="onAttachFile" />
          </label>
          <div v-if="!attachments.length" class="muted" style="margin-top:8px">暂无附件</div>
          <div v-for="a in attachments" :key="a.id" class="attach-row">
            <button type="button" class="link" @click="downloadAttach(a)">{{ a.original_filename }}</button>
            <button type="button" class="link danger" @click="removeAttach(a.id)">删除</button>
          </div>
        </div>
      </template>
    </template>

    <div v-if="timelineOpen" class="drawer" @click.self="timelineOpen = false">
      <div class="drawer-panel">
        <div class="drawer-head">
          <strong>任务链进度</strong>
          <button type="button" class="link" @click="timelineOpen = false">关闭</button>
        </div>
        <div v-if="timelineLoading" class="hint">加载中…</div>
        <template v-else-if="timeline">
          <h3>{{ timeline.title }}</h3>
          <p v-if="timeline.project_name" class="hint">项目：{{ timeline.project_name }}</p>
          <div v-for="n in timeline.nodes" :key="n.item_id" class="tl-node">
            <div class="tl-date">{{ n.period_start }}</div>
            <div><strong>{{ n.task_title || '未命名' }}</strong></div>
            <div>做成了：{{ n.result_text || '—' }}</div>
            <div>下一步：{{ n.next_goal || '—' }}</div>
          </div>
        </template>
      </div>
    </div>

    <footer class="write-footer">
      <button
        type="button"
        class="btn btn-outline draft-btn"
        :disabled="savingDraft || formReadOnly()"
        @click="saveDraft"
      >
        {{ savingDraft ? '保存中…' : '存草稿' }}
      </button>
      <button
        type="button"
        class="btn btn-solid submit-btn"
        :disabled="submitting || formReadOnly()"
        @click="submit"
      >
        {{ submitting ? '提交中…' : '提交周报' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.write-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: -4px 0 12px;
}
.back-ico {
  border: none;
  background: transparent;
  font-size: 20px;
  padding: 4px 8px 4px 0;
  color: var(--wb-text);
}
.write-title {
  flex: 1;
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  text-align: center;
}
.head-submit {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 15px;
  font-weight: 600;
  padding: 4px 0;
}
.head-submit:disabled { opacity: 0.45; }
.stats-card {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.stat {
  flex: 1;
  text-align: center;
}
.stat .n { font-size: 18px; font-weight: 700; }
.stat .l { font-size: 12px; color: var(--wb-muted); }
.tip-box { margin-top: 4px; }
.write-footer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  gap: 10px;
  padding: 10px 16px calc(10px + var(--wb-safe-bottom));
  background: #fff;
  border-top: 1px solid var(--wb-border);
  z-index: 45;
}
.draft-btn { flex: 0 0 96px; width: auto; }
.submit-btn { flex: 1; }
.report-write {
  padding-bottom: calc(72px + var(--wb-safe-bottom)) !important;
}
.sec-title { font-weight: 600; margin-bottom: 8px; font-size: 14px; }
.period-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.form-fields { border: none; margin: 0; padding: 0; }
.form-fields:disabled { opacity: 0.72; }
.more-toggle {
  display: block;
  margin: 14px 0;
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0;
  cursor: pointer;
}
.mine-list { margin-bottom: 8px; }
.mine-item {
  background: var(--wb-bg);
  border-radius: var(--wb-radius-sm);
  padding: 10px 12px;
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 1.45;
}
.item-title { font-weight: 600; margin-bottom: 4px; }
.stuck { color: var(--wb-danger); margin-top: 4px; }
.field-label { font-weight: 600; }
.chain-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: var(--wb-mint-bg);
  border-radius: var(--wb-radius-sm);
  color: var(--wb-mint-text);
}
.link {
  border: none;
  background: none;
  color: var(--wb-mint-text);
  font-size: 13px;
  cursor: pointer;
}
.suggest {
  margin: 12px 0 16px;
  padding: 12px;
  border: 1px dashed var(--wb-mint);
  border-radius: var(--wb-radius-sm);
  background: var(--wb-mint-bg);
  font-size: 13px;
}
.suggest-title { font-weight: 600; margin-bottom: 6px; }
.suggest-line { margin: 4px 0; line-height: 1.4; }
.suggest-line.warn { color: #b45309; }
.suggest-actions { display: flex; gap: 8px; margin-top: 10px; }
.check { display: flex; gap: 6px; align-items: center; margin: 8px 0; font-size: 13px; }
.hint { color: var(--wb-muted); font-size: 12px; }
.drawer {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 40;
  display: flex;
  align-items: flex-end;
}
.drawer-panel {
  width: 100%;
  max-height: 70vh;
  overflow: auto;
  background: var(--wb-card);
  border-radius: 16px 16px 0 0;
  padding: 16px;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.tl-node {
  border-left: 3px solid var(--wb-mint);
  padding: 0 0 14px 10px;
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.45;
}
.tl-date { color: var(--wb-muted); font-size: 12px; margin-bottom: 2px; }
.attach-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  align-items: center;
}
.link.danger { color: #c45656; }
</style>
