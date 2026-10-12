<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { splitProjectAndTask } from '../constants/projectMeta'
import { useUserStore } from '../stores/user'
import { isAgentNativeProduct } from '../productLine'

const CONT_PREFIXES = [
  '继续推进',
  '继续跟进',
  '继续开展',
  '继续落实',
  '继续完善',
  '继续处理',
  '持续推进',
  '持续跟进',
  '接着推进',
  '下一步推进',
  '下一步跟进',
]

function stripContPrefix(text: string): string {
  let t = (text || '').trim()
  for (const p of CONT_PREFIXES) {
    if (t.startsWith(p)) {
      t = t.slice(p.length).replace(/^[：:，,、\s\-—]+/, '')
      break
    }
  }
  if (t.startsWith('继续')) t = t.slice(2).replace(/^[：:，,、\s\-—]+/, '')
  return t
}

function isVagueContTitle(title: string): boolean {
  const t = (title || '').trim()
  if (!t) return false
  if (
    [
      '继续推进',
      '持续推进',
      '继续跟进',
      '持续跟进',
      '继续',
      '下一步',
      '持续',
    ].includes(t)
  ) {
    return true
  }
  const starts =
    CONT_PREFIXES.some((p) => t.startsWith(p)) || t.startsWith('继续')
  if (!starts) return false
  const core = stripContPrefix(t).replace(/[：:，,、.。！!？?\s\-—]/g, '')
  return core.length < 4
}

function titlesHardSimilar(a: string, b: string): boolean {
  const x = stripContPrefix(a).replace(/\s/g, '')
  const y = stripContPrefix(b).replace(/\s/g, '')
  if (!x || !y || x.length < 2 || y.length < 2) return false
  if (x === y) return true
  if (x.includes(y) || y.includes(x)) {
    return Math.min(x.length, y.length) / Math.max(x.length, y.length) >= 0.85
  }
  return false
}

/** 嵌在周报管理 Tab 内时不显示页面标题 */
const props = withDefaults(
  defineProps<{
    embedded?: boolean
  }>(),
  { embedded: false },
)

const WRITE_RETURN = '/reports'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()
const canCreateProject = computed(() => userStore.hasPermission('project.create'))
const periodType = ref<'week' | 'day'>('week')
const periodStart = ref('')
const periodEnd = ref('')
const weekRange = ref<[string, string] | null>(null)
const items = ref<any[]>([])
const softWarnings = ref<string[]>([])
const hardErrors = ref<string[]>([])
const priorChecks = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const aiResult = ref<any>(null)
const status = ref('draft')
const reportId = ref<number | null>(null)
const attachments = ref<any[]>([])
const attachUploading = ref(false)
const phase1Review = ref<any | null>(null)
const reviewUnavailable = ref('')
/** 查看他人周报时为 true，不可编辑 */
const viewingOthers = ref(false)
/** 历史已交周期解锁后为 true，保存时带 force_edit_submitted */
const forceEditSubmitted = ref(false)
const ready = ref(false)
const myPeriods = ref<Array<{ period_start: string; period_end?: string; status: string; item_count: number }>>([])
const suppressPeriodWatch = ref(false)
/** 本周期无记录时，预填任务只作「待写入建议」，不直接铺成多张可编辑卡 */
const prefillSuggestions = ref<any[]>([])
/** 新建一条时的草稿（尚未并入 items） */
const draftItem = ref<any | null>(null)

/** 路由 edit=new | 数字下标；无参则为列表态 */
const editParam = computed(() => {
  const v = route.query.edit
  return typeof v === 'string' ? v : ''
})
const isEditMode = computed(() => !!editParam.value)
const isNewMode = computed(() => editParam.value === 'new')
const editIndex = computed(() => {
  if (!editParam.value || editParam.value === 'new') return -1
  const n = Number(editParam.value)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : -1
})
/** 编辑态表单用的逻辑下标：新建固定 0（suggestions 等），已有条用真实下标 */
const formIdx = computed(() => (isNewMode.value ? 0 : editIndex.value))

/** 已交且早于本周（周报）或早于今天（日报） */
const isHistoricalSubmitted = computed(() => {
  if (status.value !== 'submitted' || !periodStart.value) return false
  if (periodType.value === 'week') return periodStart.value < mondayStr()
  return periodStart.value < localYMD(new Date())
})

const readOnly = computed(
  () => viewingOthers.value || (isHistoricalSubmitted.value && !forceEditSubmitted.value),
)

function getItemAt(idx: number) {
  if (isEditMode.value) {
    if (isNewMode.value) return draftItem.value
    return items.value[editIndex.value] || null
  }
  return items.value[idx] || null
}

function buildWriteQuery(extra: Record<string, string> = {}) {
  const q: Record<string, string> = { tab: 'write', ...extra }
  if (periodType.value) q.period_type = periodType.value
  if (periodStart.value) q.period_start = periodStart.value
  if (periodEnd.value) q.period_end = periodEnd.value
  const view = route.query.view
  if (typeof view === 'string' && view) q.view = view
  return q
}

function goToList() {
  draftItem.value = null
  const q = buildWriteQuery()
  delete q.edit
  delete q.prefill
  delete q.paste
  router.replace({ path: WRITE_RETURN, query: q })
}

function goNewItem(opts?: { prefill?: number; paste?: boolean }) {
  draftItem.value = null
  const extra: Record<string, string> = { edit: 'new' }
  if (opts?.prefill != null && opts.prefill >= 0) extra.prefill = String(opts.prefill)
  if (opts?.paste) extra.paste = '1'
  router.replace({ path: WRITE_RETURN, query: buildWriteQuery(extra) })
}

function goEditItem(idx: number) {
  if (idx < 0 || idx >= items.value.length) return
  router.replace({ path: WRITE_RETURN, query: buildWriteQuery({ edit: String(idx) }) })
}

function initDraftFromRoute() {
  if (!isNewMode.value) {
    draftItem.value = null
    return
  }
  const prefillRaw = route.query.prefill
  const prefillIdx = typeof prefillRaw === 'string' ? Number(prefillRaw) : -1
  if (Number.isFinite(prefillIdx) && prefillIdx >= 0 && prefillSuggestions.value[prefillIdx]) {
    draftItem.value = mapItem(prefillSuggestions.value[prefillIdx])
  } else if (!draftItem.value) {
    draftItem.value = emptyItem()
  }
}

async function maybePasteFromQuery() {
  if (!isNewMode.value || route.query.paste !== '1' || readOnly.value) return
  if (!draftItem.value) initDraftFromRoute()
  const it = draftItem.value
  if (!it) return
  let text = ''
  try {
    text = await navigator.clipboard.readText()
  } catch {
    ElMessage.warning('无法读取剪贴板，请手动粘贴到「做成了什么」')
  }
  text = (text || '').trim()
  const nextQuery = { ...route.query }
  delete nextQuery.paste
  router.replace({ path: route.path, query: nextQuery })
  if (!text) {
    ElMessage.warning('剪贴板是空的')
    return
  }
  it.result_text = text.slice(0, 2000)
  if (!it.task_title?.trim()) {
    it.task_title = text.split(/[。\n]/)[0].slice(0, 40)
  }
  ElMessage.success('已粘贴，正在 AI 整理…')
  await runStructure(0)
}

/** 每张卡片的 AI 建议状态 */
const suggestions = reactive<Record<number, any>>({})
const suggestLoading = reactive<Record<number, boolean>>({})
const linkTaskChecked = reactive<Record<number, boolean>>({})
const createChainChecked = reactive<Record<number, boolean>>({})
const timers = reactive<Record<number, ReturnType<typeof setTimeout> | undefined>>({})
const lastFingerprint = reactive<Record<number, string>>({})

const timelineOpen = ref(false)
const timelineLoading = ref(false)
const timeline = ref<any>(null)
const workTagOptions = ref<string[]>([
  '售前',
  '售中',
  '售后',
  '运维',
  '项目管理',
  '流程跟进',
  '客户对接',
  '方案汇报',
])
const projectOptions = ref<Array<{ id: number; name: string }>>([])

async function loadWorkTags() {
  try {
    const { data } = await api.get('/progress-reports/work-tags')
    if (Array.isArray(data) && data.length) workTagOptions.value = data
  } catch {
    /* 用本地默认词表 */
  }
}

async function loadProjects() {
  try {
    const { data } = await api.get('/projects', { params: { page_size: 100 } })
    projectOptions.value = (data?.items || []).map((p: any) => ({ id: p.id, name: p.name }))
    // 「日常任务」置顶
    projectOptions.value.sort((a, b) => {
      if (a.name === '日常任务') return -1
      if (b.name === '日常任务') return 1
      return a.name.localeCompare(b.name, 'zh')
    })
  } catch {
    projectOptions.value = []
  }
}

function projectName(id: number | null | undefined) {
  if (!id) return ''
  return projectOptions.value.find((p) => p.id === id)?.name || ''
}

/** 概述写成「项目名-任务」时：自动选项目并把后半段留作任务名 */
function applyTitleSplit(idx: number) {
  if (readOnly.value) return
  const it = getItemAt(idx)
  if (!it) return
  const raw = (it.task_title || '').trim()
  const { projectName: pname, taskTitle } = splitProjectAndTask(raw)
  if (!taskTitle || !pname) return
  const hit = projectOptions.value.find(
    (p) => p.name === pname || p.name.includes(pname) || pname.includes(p.name),
  )
  if (hit) {
    it.project_id = hit.id
    it.task_title = taskTitle.slice(0, 120)
    ElMessage.success(`已归入项目「${hit.name}」，任务写为后半段`)
    scheduleStructure(idx)
  } else {
    ElMessage.info(
      `识别到项目「${pname}」、任务「${taskTitle.slice(0, 40)}…」。请先点「去新建」立项，或手动选择项目。`,
    )
  }
}

/** 去项目管理新建项目，创建成功后带回 selectProjectId 并自动选中 */
async function goCreateProject(idx: number) {
  if (readOnly.value || !canCreateProject.value) return
  try {
    if (isEditMode.value) {
      await saveCurrentItem({ stay: true, quiet: true })
    } else {
      await save(false)
    }
  } catch {
    /* 草稿保存失败仍允许去新建，避免卡死 */
  }
  const edit = isNewMode.value
    ? 'new'
    : isEditMode.value && editIndex.value >= 0
      ? String(editIndex.value)
      : String(idx)
  router.push({
    path: '/overview',
    query: {
      create: '1',
      returnTo: WRITE_RETURN,
      returnTab: 'write',
      edit,
      itemIndex: String(isNewMode.value ? 0 : editIndex.value >= 0 ? editIndex.value : idx),
    },
  })
}

/** 从新建页回来：刷新项目列表并选中新项目 */
async function applySelectProjectFromQuery() {
  const rawId = route.query.selectProjectId
  if (typeof rawId !== 'string' || !rawId) return
  const id = Number(rawId)
  if (!Number.isFinite(id) || id <= 0) return
  await loadProjects()
  if (isNewMode.value) {
    if (!draftItem.value) initDraftFromRoute()
    if (draftItem.value) {
      draftItem.value.project_id = id
      ElMessage.success(`已选中新项目「${projectName(id) || '#' + id}」`)
    }
  } else {
    const idxRaw = route.query.itemIndex
    const idx =
      editIndex.value >= 0
        ? editIndex.value
        : typeof idxRaw === 'string'
          ? Number(idxRaw)
          : 0
    const target = items.value[Number.isFinite(idx) && idx >= 0 ? idx : 0]
    if (target) {
      target.project_id = id
      ElMessage.success(`已选中新项目「${projectName(id) || '#' + id}」`)
    }
  }
  const nextQuery = { ...route.query }
  delete nextQuery.selectProjectId
  delete nextQuery.itemIndex
  router.replace({ path: route.path, query: nextQuery })
}

function localYMD(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function mondayStr(d = new Date()) {
  const day = d.getDay() || 7
  const m = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  m.setDate(m.getDate() - day + 1)
  return localYMD(m)
}

function sundayStr(d = new Date()) {
  const mon = new Date(mondayStr(d) + 'T12:00:00')
  mon.setDate(mon.getDate() + 6)
  return localYMD(mon)
}

function emptyItem() {
  return {
    id: null as number | null,
    project_id: null,
    chain_id: null,
    linked_task_id: null,
    task_title: '',
    no_action: false,
    result_text: '',
    quant_value: '',
    stuck_status: 'none',
    stuck_detail: '',
    stuck_next_action: '',
    stuck_follow_up: '',
    stuck_need_who: '',
    linked_blocker_id: null,
    next_goal: '',
    next_due_date: '',
    risk_level: 'normal',
    risk_reason: '',
    risk_impact_date: '',
    risk_coordinator: '',
    prior_done: null,
    tags: [],
    sort_order: 0,
    attachments: [] as any[],
  }
}

function mapItem(it: any) {
  return {
    ...emptyItem(),
    ...it,
    id: it.id ?? null,
    project_id: it.project_id ?? null,
    chain_id: it.chain_id ?? null,
    linked_task_id: it.linked_task_id ?? null,
    stuck_follow_up: it.stuck_follow_up || '',
    next_due_date: it.next_due_date || '',
    risk_impact_date: it.risk_impact_date || '',
    tags: Array.isArray(it.tags) ? [...it.tags] : [],
    attachments: Array.isArray(it.attachments) ? [...it.attachments] : [],
  }
}

/** 旧数据：整份周报级附件（未挂到任务卡片） */
const legacyAttachments = computed(() =>
  (attachments.value || []).filter((a) => a.item_id == null),
)

const previewLines = computed(() => {
  const lines: string[] = []
  for (const it of items.value) {
    if (it.no_action) continue
    const title = (it.task_title || '').trim() || '未命名任务'
    const chainHint = it.chain_id ? ' · 已入链' : ''
    lines.push(`${title}${chainHint}｜做成了：${it.result_text || '…'}｜下一步：${it.next_goal || '…'}（${it.next_due_date || '未定日'}）`)
    if (it.stuck_status !== 'none') {
      lines.push(`　卡点：${it.stuck_detail || '…'} → ${it.stuck_next_action || '…'} @ ${it.stuck_follow_up || '—'}`)
    }
    if (it.risk_level === 'escalate') {
      lines.push(`　需升级：${it.risk_reason || '…'}，协调 ${it.risk_coordinator || '—'}`)
    }
  }
  return lines
})

function syncRangeFromFields() {
  if (periodType.value === 'week' && periodStart.value && periodEnd.value) {
    weekRange.value = [periodStart.value, periodEnd.value]
  }
}

function onWeekRangeChange(val: [string, string] | null) {
  if (val && val.length === 2) {
    const startChanged = periodStart.value !== val[0]
    if (startChanged) forceEditSubmitted.value = false
    periodStart.value = val[0]
    periodEnd.value = val[1]
    if (!startChanged && !viewingOthers.value) loadExisting()
  }
}

function setDefaultPeriod() {
  if (periodType.value === 'week') {
    periodStart.value = mondayStr()
    periodEnd.value = sundayStr()
    weekRange.value = [periodStart.value, periodEnd.value]
  } else {
    const today = new Date().toISOString().slice(0, 10)
    periodStart.value = today
    periodEnd.value = today
    weekRange.value = null
  }
}

async function loadMyPeriods() {
  try {
    const { data } = await api.get('/progress-reports/my-periods', {
      params: { period_type: periodType.value },
    })
    let list: Array<{ period_start: string; period_end?: string; status: string; item_count: number }> =
      data || []
    // 本周/今日若库中尚无记录，芯片仍露出，避免只能点到历史周
    if (periodType.value === 'week') {
      const cur = mondayStr()
      if (!list.some((p) => p.period_start === cur)) {
        list = [
          { period_start: cur, period_end: sundayStr(), status: 'draft', item_count: 0 },
          ...list,
        ]
      }
    } else {
      const today = localYMD(new Date())
      if (!list.some((p) => p.period_start === today)) {
        list = [{ period_start: today, period_end: today, status: 'draft', item_count: 0 }, ...list]
      }
    }
    myPeriods.value = list
  } catch {
    myPeriods.value = []
  }
}

function unlockHistoricalEdit() {
  forceEditSubmitted.value = true
  ElMessage.info('已解锁，可修改历史已交周期；保存时将确认覆盖')
}

function pickMyPeriod(p: { period_start: string; period_end?: string }) {
  forceEditSubmitted.value = false
  periodStart.value = p.period_start
  periodEnd.value = p.period_end || p.period_start
  syncRangeFromFields()
  // 换周期时回到列表，避免下标错位
  if (isEditMode.value) {
    const q = buildWriteQuery()
    delete (q as any).edit
    router.replace({ path: WRITE_RETURN, query: q })
  }
  // periodStart 的 watch 会触发 loadExisting
}

async function loadExisting() {
  if (!periodStart.value) return
  loading.value = true
  hardErrors.value = []
  try {
    const { data } = await api.get('/progress-reports/mine', {
      params: {
        period_type: periodType.value,
        period_start: periodStart.value,
        period_end: periodEnd.value || periodStart.value,
      },
    })
    if (data) {
      status.value = data.status
      reportId.value = data.id || null
      attachments.value = data.attachments || []
      phase1Review.value = data.phase1_review || null
      reviewUnavailable.value = data.review_unavailable_reason || ''
      suppressPeriodWatch.value = true
      if (data.period_start) periodStart.value = data.period_start
      if (data.period_end) periodEnd.value = data.period_end
      syncRangeFromFields()
      suppressPeriodWatch.value = false
      items.value = (data.items || []).map(mapItem)
      prefillSuggestions.value = []
      softWarnings.value = data.soft_warnings || []
    } else {
      // 本周期无记录：留在当前周期写新内容，绝不自动切到历史周
      status.value = 'draft'
      reportId.value = null
      attachments.value = []
      phase1Review.value = null
      reviewUnavailable.value = ''
      softWarnings.value = []
      items.value = []
      await loadPrefill()
    }
    await loadMyPeriods()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 403) {
      hardErrors.value = [typeof detail === 'string' ? detail : '没有写周报权限，请联系管理员开通「提交周报」']
      items.value = []
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : '加载周报失败')
    }
  } finally {
    suppressPeriodWatch.value = false
    loading.value = false
  }
}

async function loadPrefill() {
  try {
    const { data } = await api.get('/progress-reports/prefill', {
      params: {
        period_type: periodType.value,
        period_start: periodStart.value,
        period_end: periodEnd.value || periodStart.value,
      },
    })
    priorChecks.value = data.prior_checks || []
    // 预填只进「待写入建议」，不铺成多张编辑卡
    prefillSuggestions.value = (data.items || []).map(mapItem)
    if (!items.value.length) items.value = []
  } catch (e: any) {
    prefillSuggestions.value = []
    if (!items.value.length) items.value = []
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 403) {
      hardErrors.value = [typeof detail === 'string' ? detail : '没有写周报权限，请联系管理员开通「提交周报」']
    }
  }
}

/** 粘贴原文：进新建页并带 paste=1 */
function pasteRawToCard() {
  if (readOnly.value) return
  goNewItem({ paste: true })
}

function clearTimer(idx: number) {
  if (timers[idx]) {
    clearTimeout(timers[idx])
    timers[idx] = undefined
  }
}

function fingerprint(it: any) {
  return `${(it.task_title || '').trim()}||${(it.result_text || '').trim()}`
}

function shouldTrigger(it: any) {
  if (readOnly.value || it.no_action) return false
  const title = (it.task_title || '').trim()
  const result = (it.result_text || '').trim()
  if (title.length >= 4) return true
  if (result.length >= 8) return true
  return false
}

function scheduleStructure(idx: number) {
  if (agentNative) return
  if (!ready.value || readOnly.value) return
  const it = getItemAt(idx)
  if (!it || !shouldTrigger(it)) return
  const key = isEditMode.value ? formIdx.value : idx
  clearTimer(key)
  timers[key] = setTimeout(() => runStructure(idx), 1200)
}

async function runStructure(idx: number) {
  if (agentNative) return
  const it = getItemAt(idx)
  if (!it || !shouldTrigger(it)) return
  const key = isEditMode.value ? formIdx.value : idx
  const fp = fingerprint(it)
  if (fp === lastFingerprint[key] && suggestions[key]) return
  suggestLoading[key] = true
  try {
    const { data } = await api.post('/progress-reports/ai-structure', {
      period_type: periodType.value,
      period_start: periodStart.value,
      period_end: periodEnd.value || periodStart.value,
      chain_id: it.chain_id || null,
      item: {
        ...it,
        project_id: it.project_id || null,
        stuck_follow_up: it.stuck_follow_up || null,
        next_due_date: it.next_due_date || null,
        risk_impact_date: it.risk_impact_date || null,
      },
    })
    lastFingerprint[key] = fp
    suggestions[key] = data
    linkTaskChecked[key] = !!data.task_suggestion
    createChainChecked[key] = !!data.create_new_chain && !data.chain_suggestion && !it.chain_id
    // 工作标签：模型返回后自动写入卡片（用户仍可改）
    const aiTags = data?.structured?.tags
    if (Array.isArray(aiTags) && aiTags.length) {
      it.tags = [...aiTags]
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    ElMessage.warning(typeof detail === 'string' ? detail : 'AI 整理暂时不可用')
  } finally {
    suggestLoading[key] = false
  }
}

async function applySuggestion(idx: number) {
  const key = isEditMode.value ? formIdx.value : idx
  const data = suggestions[key]
  const it = getItemAt(idx)
  if (!data || !it) return
  const s = data.structured || {}
  Object.assign(it, {
    task_title: s.task_title ?? it.task_title,
    result_text: s.result_text ?? it.result_text,
    quant_value: s.quant_value ?? it.quant_value,
    stuck_status: s.stuck_status ?? it.stuck_status,
    stuck_detail: s.stuck_detail ?? it.stuck_detail,
    stuck_next_action: s.stuck_next_action ?? it.stuck_next_action,
    stuck_follow_up: s.stuck_follow_up || '',
    stuck_need_who: s.stuck_need_who ?? it.stuck_need_who,
    next_goal: s.next_goal ?? it.next_goal,
    next_due_date: s.next_due_date || '',
    risk_level: s.risk_level ?? it.risk_level,
    risk_reason: s.risk_reason ?? it.risk_reason,
    risk_impact_date: s.risk_impact_date || '',
    risk_coordinator: s.risk_coordinator ?? it.risk_coordinator,
    tags: Array.isArray(s.tags) && s.tags.length ? [...s.tags] : it.tags || [],
  })

  if (data.project_suggestion?.project_id) {
    it.project_id = data.project_suggestion.project_id
  }

  if (data.chain_suggestion?.chain_id) {
    it.chain_id = data.chain_suggestion.chain_id
    if (data.chain_suggestion.project_id && !it.project_id) {
      it.project_id = data.chain_suggestion.project_id
    }
    if (data.chain_suggestion.seed_item_id) {
      try {
        await api.post('/progress-reports/chains/link-history', {
          chain_id: data.chain_suggestion.chain_id,
          seed_item_id: data.chain_suggestion.seed_item_id,
        })
      } catch {
        /* 历史补链失败不阻断采用 */
      }
    }
  } else if (createChainChecked[key] && !it.chain_id) {
    it.chain_id = crypto.randomUUID()
  }

  if (linkTaskChecked[key] && data.task_suggestion?.task_id) {
    it.linked_task_id = data.task_suggestion.task_id
    if (data.task_suggestion.project_id && !it.project_id) {
      it.project_id = data.task_suggestion.project_id
    }
  }

  delete suggestions[key]
  ElMessage.success('已采用 AI 整理（记得保存本条）')
}

function dismissSuggestion(idx: number) {
  const key = isEditMode.value ? formIdx.value : idx
  delete suggestions[key]
}

async function openTimeline(chainId: string) {
  if (!chainId) return
  timelineOpen.value = true
  timelineLoading.value = true
  timeline.value = null
  try {
    const { data } = await api.get(`/progress-reports/chains/${chainId}`)
    timeline.value = data
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '加载任务链失败')
    timelineOpen.value = false
  } finally {
    timelineLoading.value = false
  }
}

function payload(submit: boolean) {
  const end = periodType.value === 'day' ? periodStart.value : (periodEnd.value || periodStart.value)
  return {
    period_type: periodType.value,
    period_start: periodStart.value,
    period_end: end,
    submit,
    force_edit_submitted: forceEditSubmitted.value && isHistoricalSubmitted.value,
    items: items.value.map((it, idx) => ({
      ...it,
      project_id: it.project_id || null,
      chain_id: it.chain_id || null,
      linked_task_id: it.linked_task_id || null,
      task_title: (it.task_title || '').trim(),
      stuck_follow_up: it.stuck_follow_up || null,
      next_due_date: it.next_due_date || null,
      risk_impact_date: it.risk_impact_date || null,
      linked_blocker_id: it.linked_blocker_id || null,
      sort_order: idx,
    })),
  }
}

async function confirmBeforeSave(): Promise<boolean> {
  const vagueIdx = items.value.findIndex(
    (it) => !it.no_action && isVagueContTitle(it.task_title || ''),
  )
  if (vagueIdx >= 0) {
    try {
      await ElMessageBox.confirm(
        `第${vagueIdx + 1}条标题太笼统（如「继续推进」）。请想清楚具体事项后再写；仍要保存吗？`,
        '请想清楚',
        {
          type: 'warning',
          confirmButtonText: '仍要保存',
          cancelButtonText: '去修改',
        },
      )
    } catch {
      return false
    }
  }

  // 同报告内高度相似标题：提示去掉后面的重复卡
  const active = items.value
    .map((it, idx) => ({ it, idx }))
    .filter(({ it }) => !it.no_action && (it.task_title || '').trim())
  for (let i = 0; i < active.length; i++) {
    for (let j = i + 1; j < active.length; j++) {
      const a = active[i]
      const b = active[j]
      if (!titlesHardSimilar(a.it.task_title, b.it.task_title)) continue
      try {
        await ElMessageBox.confirm(
          `第${a.idx + 1}条与第${b.idx + 1}条内容很像。建议合并到第${a.idx + 1}条并去掉重复卡。`,
          '疑似重复卡片',
          {
            type: 'warning',
            confirmButtonText: `去掉第${b.idx + 1}条`,
            cancelButtonText: '仍都保留',
            distinguishCancelAndClose: true,
          },
        )
        items.value.splice(b.idx, 1)
        return true
      } catch (action: any) {
        if (action === 'cancel') return true
        return false
      }
    }
  }
  return true
}

/** 把当前编辑条合并进 items，再整份 POST（避免 clear 掉其它卡） */
async function saveCurrentItem(opts?: { stay?: boolean; quiet?: boolean }) {
  hardErrors.value = []
  if (saving.value || readOnly.value) return false
  const wasNew = isNewMode.value
  const current = wasNew ? draftItem.value : items.value[editIndex.value]
  if (!current) {
    ElMessage.warning('没有可保存的内容')
    return false
  }
  if (!(current.task_title || '').trim() && !current.no_action) {
    ElMessage.warning('请先填写任务概述')
    return false
  }
  if (!current.project_id && !current.no_action) {
    ElMessage.warning('请选择所属项目（杂项请选「日常任务」）')
    return false
  }

  // 合并：新建 push；编辑则已在 items 里就地改
  let mergedIdx = editIndex.value
  if (wasNew) {
    items.value = [...items.value, mapItem(current)]
    mergedIdx = items.value.length - 1
  }

  if (!(await confirmBeforeSave())) {
    if (wasNew && mergedIdx >= 0) {
      // 用户取消时撤回刚 push 的草稿，避免列表多一条未确认内容
      items.value.splice(mergedIdx, 1)
    }
    return false
  }

  saving.value = true
  try {
    const { data } = await api.post('/progress-reports', payload(false), { timeout: 30000 })
    status.value = data.status
    reportId.value = data.id || null
    attachments.value = data.attachments || []
    softWarnings.value = data.soft_warnings || []
    if (data.items) items.value = data.items.map(mapItem)
    // 若来自预填建议，保存后从建议列表去掉同下标
    const prefillRaw = route.query.prefill
    const prefillIdx = typeof prefillRaw === 'string' ? Number(prefillRaw) : -1
    if (Number.isFinite(prefillIdx) && prefillIdx >= 0) {
      prefillSuggestions.value = prefillSuggestions.value.filter((_, i) => i !== prefillIdx)
    }
    draftItem.value = null
    if (!opts?.quiet) ElMessage.success('本条已保存')
    if (!opts?.stay) {
      goToList()
    } else if (wasNew && mergedIdx >= 0) {
      // 去新建项目 / 上传附件：切到已保存下标，避免仍停在 edit=new
      const idx = Math.min(mergedIdx, Math.max(items.value.length - 1, 0))
      await router.replace({
        path: WRITE_RETURN,
        query: buildWriteQuery({ edit: String(idx) }),
      })
    }
    return true
  } catch (e: any) {
    if (wasNew && mergedIdx >= 0 && mergedIdx < items.value.length) {
      // 保存失败撤回 push，保留 draftItem 继续改
      const rolled = items.value[mergedIdx]
      draftItem.value = mapItem(rolled)
      items.value.splice(mergedIdx, 1)
    }
    const detail = e?.response?.data?.detail
    if (detail?.hard_errors) {
      hardErrors.value = detail.hard_errors
      softWarnings.value = detail.soft_warnings || []
      ElMessage.error('还有几处需要改一下才能保存')
    } else if (e?.response?.status === 409) {
      ElMessage.error(typeof detail === 'string' ? detail : '历史已交周期需先解锁再保存')
    } else if (e?.code === 'ECONNABORTED' || /timeout/i.test(String(e?.message || ''))) {
      ElMessage.error('保存时间较长，请刷新页面查看是否已保存')
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : '保存失败')
    }
    return false
  } finally {
    saving.value = false
  }
}

async function deleteCurrentItem() {
  if (readOnly.value) return
  if (isNewMode.value) {
    try {
      await ElMessageBox.confirm('放弃这条未保存内容？', '删除', { type: 'warning' })
    } catch {
      return
    }
    draftItem.value = null
    goToList()
    return
  }
  const idx = editIndex.value
  if (idx < 0 || idx >= items.value.length) return
  try {
    await ElMessageBox.confirm('删除后将从本周期周报中移除该条，确认？', '删除本条', {
      type: 'warning',
    })
  } catch {
    return
  }
  items.value.splice(idx, 1)
  saving.value = true
  try {
    const { data } = await api.post('/progress-reports', payload(false), { timeout: 30000 })
    status.value = data.status
    reportId.value = data.id || null
    attachments.value = data.attachments || []
    if (data.items) items.value = data.items.map(mapItem)
    else items.value = []
    ElMessage.success('已删除')
    goToList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
    await loadExisting()
  } finally {
    saving.value = false
  }
}

async function backToList() {
  if (readOnly.value) {
    goToList()
    return
  }
  try {
    await ElMessageBox.confirm('未保存的修改将丢失，确定返回列表？', '返回列表', {
      type: 'warning',
      confirmButtonText: '返回',
      cancelButtonText: '继续编辑',
    })
  } catch {
    return
  }
  goToList()
}

function itemSummary(it: any) {
  const title = (it.task_title || '').trim() || '未命名任务'
  const result = (it.result_text || '').trim()
  const short = result ? result.slice(0, 48) + (result.length > 48 ? '…' : '') : '尚未填写做成了什么'
  return { title, short }
}

async function save(submit: boolean) {
  hardErrors.value = []
  if (saving.value) return
  if (submit) {
    if (!items.value.length) {
      ElMessage.warning('请先新建并保存至少一条任务')
      return
    }
    const missing = items.value.findIndex((it) => !it.project_id)
    if (missing >= 0) {
      hardErrors.value = [`第${missing + 1}条：请选择所属项目（杂项请选「日常任务」）`]
      ElMessage.warning(hardErrors.value[0])
      return
    }
  }
  if (!(await confirmBeforeSave())) return
  saving.value = true
  try {
    const { data } = await api.post('/progress-reports', payload(submit), {
      timeout: 30000,
    })
    status.value = data.status
    reportId.value = data.id || null
    attachments.value = data.attachments || []
    softWarnings.value = data.soft_warnings || []
    ElMessage.success(
      submit
        ? '已提交，下周计划与「做成了什么」正在后台写入待办'
        : '草稿已保存',
    )
    if (data.items) {
      items.value = data.items.map(mapItem)
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (detail?.hard_errors) {
      hardErrors.value = detail.hard_errors
      softWarnings.value = detail.soft_warnings || []
      ElMessage.error('还有几处需要改一下才能提交')
    } else if (e?.response?.status === 409) {
      ElMessage.error(typeof detail === 'string' ? detail : '历史已交周期需先解锁再保存')
    } else if (e?.code === 'ECONNABORTED' || /timeout/i.test(String(e?.message || ''))) {
      ElMessage.error('保存时间较长，请刷新页面查看是否已保存')
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : '保存失败')
    }
  } finally {
    saving.value = false
  }
}

async function runAi() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const body = payload(false)
  const { data } = await api.post('/progress-reports/ai-coach', {
    period_type: body.period_type,
    period_start: body.period_start,
    period_end: body.period_end,
    items: body.items,
  })
  aiResult.value = data
  ElMessage.info(data.message || '检查完成')
}

async function ensureItemReady(idx: number): Promise<{ rid: number; itemId: number } | null> {
  if (readOnly.value) return null
  const it = getItemAt(idx)
  if (!it) return null
  if (reportId.value && it.id) {
    return { rid: reportId.value, itemId: it.id }
  }
  if (saving.value) {
    ElMessage.warning('正在保存中，请稍后再上传附件')
    return null
  }
  // 新建条尚未落库：先保存本条再上传
  if (isEditMode.value) {
    const ok = await saveCurrentItem({ stay: true, quiet: true })
    if (!ok) return null
    const saved =
      (editIndex.value >= 0 ? items.value[editIndex.value] : null) ||
      items.value[items.value.length - 1]
    if (!reportId.value || !saved?.id) {
      ElMessage.error('保存草稿后仍无法定位任务卡片，请稍后再试')
      return null
    }
    return { rid: reportId.value, itemId: saved.id }
  }
  saving.value = true
  try {
    const { data } = await api.post('/progress-reports', payload(false), { timeout: 30000 })
    status.value = data.status
    reportId.value = data.id || null
    attachments.value = data.attachments || []
    if (data.items) items.value = data.items.map(mapItem)
    const rid = reportId.value
    const itemId = items.value[idx]?.id
    if (!rid || !itemId) {
      ElMessage.error('保存草稿后仍无法定位任务卡片，请稍后再试')
      return null
    }
    return { rid, itemId }
  } catch (e: any) {
    if (e?.code === 'ECONNABORTED' || /timeout/i.test(String(e?.message || ''))) {
      ElMessage.error('保存时间较长，请刷新页面查看是否已保存')
    } else {
      ElMessage.error(e?.response?.data?.detail || e?.message || '请先保存草稿再上传附件')
    }
    return null
  } finally {
    saving.value = false
  }
}

function syncAttachmentsFromReport(list: any[]) {
  attachments.value = list || []
  const byItem: Record<number, any[]> = {}
  for (const a of attachments.value) {
    if (a.item_id == null) continue
    if (!byItem[a.item_id]) byItem[a.item_id] = []
    byItem[a.item_id].push(a)
  }
  for (const it of items.value) {
    it.attachments = it.id ? [...(byItem[it.id] || [])] : []
  }
}

async function onCardAttachChange(idx: number, uploadFile: any) {
  const raw = uploadFile?.raw as File | undefined
  if (!raw) return
  const readyPair = await ensureItemReady(idx)
  if (!readyPair) return
  attachUploading.value = true
  try {
    const form = new FormData()
    form.append('file', raw)
    form.append('item_id', String(readyPair.itemId))
    await api.post(`/progress-reports/${readyPair.rid}/attachments`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const { data } = await api.get(`/progress-reports/${readyPair.rid}/attachments`)
    syncAttachmentsFromReport(data || [])
    ElMessage.success('附件已上传')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '上传失败')
  } finally {
    attachUploading.value = false
  }
}

async function removeAttach(row: any) {
  if (!reportId.value || readOnly.value) return
  try {
    await api.delete(`/progress-reports/${reportId.value}/attachments/${row.id}`)
    syncAttachmentsFromReport(attachments.value.filter((a) => a.id !== row.id))
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function downloadAttach(row: any) {
  if (!reportId.value) return
  try {
    const res = await api.get(
      `/progress-reports/${reportId.value}/attachments/${row.id}/download`,
      { responseType: 'blob' },
    )
    const blobUrl = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = row.original_filename || '附件'
    a.click()
    URL.revokeObjectURL(blobUrl)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '下载失败')
  }
}

/** 编辑页当前这一条 */
const activeItem = computed(() => {
  if (!isEditMode.value) return null
  if (isNewMode.value) return draftItem.value
  if (editIndex.value < 0) return null
  return items.value[editIndex.value] || null
})

watch(periodType, () => {
  if (!ready.value) return
  forceEditSubmitted.value = false
  setDefaultPeriod()
  if (isEditMode.value) goToList()
})

watch(periodStart, (next, prev) => {
  if (suppressPeriodWatch.value) return
  if (next !== prev) forceEditSubmitted.value = false
  if (ready.value && periodStart.value && !viewingOthers.value) loadExisting()
})

watch(
  () => [route.query.edit, route.query.prefill, route.query.paste] as const,
  async () => {
    if (!ready.value) return
    if (isNewMode.value) {
      initDraftFromRoute()
      await maybePasteFromQuery()
    } else {
      draftItem.value = null
      // 编辑下标越界时回列表
      if (isEditMode.value && (editIndex.value < 0 || editIndex.value >= items.value.length)) {
        goToList()
      }
    }
  },
)

onMounted(async () => {
  await Promise.all([loadWorkTags(), loadProjects()])
  setDefaultPeriod()
  // 从「看」Tab 点选本人周期时带上 period_type / period_start
  const qType = route.query.period_type
  if (qType === 'day' || qType === 'week') periodType.value = qType
  const qStart = route.query.period_start
  if (typeof qStart === 'string' && qStart) {
    periodStart.value = qStart
    const qEnd = route.query.period_end
    periodEnd.value = typeof qEnd === 'string' && qEnd ? qEnd : qStart
    syncRangeFromFields()
  }
  const viewId = route.query.view
  if (viewId) {
    loading.value = true
    try {
      const { data } = await api.get(`/progress-reports/${viewId}`)
      // 创建人可改自己的周报/日报；看别人的仍只读
      const authorId = data.author?.id
      viewingOthers.value = authorId == null || authorId !== userStore.user?.id
      forceEditSubmitted.value = false
      status.value = data.status
      reportId.value = data.id || null
      attachments.value = data.attachments || []
      periodType.value = data.period_type
      periodStart.value = data.period_start
      periodEnd.value = data.period_end || data.period_start
      syncRangeFromFields()
      items.value = (data.items || []).map(mapItem)
      prefillSuggestions.value = []
    } finally {
      loading.value = false
    }
    ready.value = true
    if (isNewMode.value) initDraftFromRoute()
    return
  }
  await loadMyPeriods()
  await loadExisting()
  await applySelectProjectFromQuery()
  if (isNewMode.value) {
    initDraftFromRoute()
    await maybePasteFromQuery()
  } else if (isEditMode.value && (editIndex.value < 0 || editIndex.value >= items.value.length)) {
    goToList()
  }
  ready.value = true
})

onUnmounted(() => {
  Object.keys(timers).forEach((k) => clearTimer(Number(k)))
})
</script>

<template>
  <div v-loading="loading" class="page">
    <template v-if="!embedded">
      <h1 class="wb-page-title">写周报 / 日报</h1>
      <p class="wb-page-desc">
        一次写一条任务：保存后再新建下一条。停顿后 AI 会帮你整理，点「采用」才写入。
      </p>
    </template>
    <el-alert
      v-if="periodType === 'day'"
      type="info"
      :closable="false"
      show-icon
      title="日报是当天工作记录，不考核催办；周末请汇总到周报。"
      style="margin-bottom: 12px"
    />

    <div class="toolbar panel">
      <el-radio-group v-model="periodType" size="small" :disabled="isEditMode">
        <el-radio-button value="week">周报</el-radio-button>
        <el-radio-button value="day">日报</el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-if="periodType === 'week'"
        v-model="weekRange"
        type="daterange"
        value-format="YYYY-MM-DD"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        :disabled="isEditMode"
        @change="onWeekRangeChange"
      />
      <el-date-picker
        v-else
        v-model="periodStart"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="日期"
        :disabled="isEditMode"
        @change="(v: string) => { periodEnd = v }"
      />
      <el-tag v-if="status === 'submitted'" type="success" effect="plain">已提交</el-tag>
      <el-tag v-else effect="plain">草稿</el-tag>
      <el-button v-if="isEditMode" link type="primary" @click="backToList">← 返回列表</el-button>
    </div>

    <div v-if="phase1Review && !isEditMode" class="panel review-panel">
      <div class="panel-head">对本期周报的评价</div>
      <p>{{ phase1Review.summary }}</p>
      <ul v-if="phase1Review.strengths?.length">
        <li v-for="(s, i) in phase1Review.strengths" :key="'st' + i">优点：{{ s }}</li>
      </ul>
      <ul v-if="phase1Review.risks?.length">
        <li v-for="(s, i) in phase1Review.risks" :key="'rk' + i">关注：{{ s }}</li>
      </ul>
      <p v-if="phase1Review.completion_comment" class="hint">{{ phase1Review.completion_comment }}</p>
      <p class="hint">来源：{{ phase1Review.source === 'llm' ? 'AI' : '规则' }}</p>
    </div>
    <el-alert
      v-else-if="reviewUnavailable && !isEditMode"
      type="info"
      :closable="false"
      :title="reviewUnavailable"
      style="margin-bottom: 12px"
    />

    <el-alert
      v-if="isHistoricalSubmitted && !forceEditSubmitted && !viewingOthers"
      type="warning"
      :closable="false"
      style="margin-bottom: 12px"
    >
      <template #title>
        历史已交周期，仅查看。如需改动请
        <el-button type="warning" link @click="unlockHistoricalEdit">解锁修改</el-button>
      </template>
    </el-alert>

    <div v-if="myPeriods.length && !isEditMode" class="panel periods">
      <div class="panel-head">我已有的周期（点选切换）</div>
      <div class="period-chips">
        <el-tag
          v-for="p in myPeriods"
          :key="p.period_start + (p.period_end || '')"
          class="chip"
          :effect="p.period_start === periodStart ? 'dark' : 'plain'"
          :type="p.period_start === periodStart ? 'primary' : 'info'"
          style="cursor:pointer;margin:0 6px 6px 0"
          @click="pickMyPeriod(p)"
        >
          {{ p.period_start }}{{ p.period_end && p.period_end !== p.period_start ? ' ~ ' + p.period_end : '' }}
          · {{ p.status === 'submitted' ? '已交' : (p.item_count ? '草稿' : '未写') }}
          · {{ p.item_count }}条
        </el-tag>
      </div>
    </div>

    <div v-if="priorChecks.length && !isEditMode" class="panel prior">
      <div class="panel-head">上期计划核对</div>
      <div v-for="(p, i) in priorChecks" :key="i" class="prior-row">
        <span>
          <template v-if="p.task_title">{{ p.task_title }} · </template>
          {{ p.next_goal }}（{{ p.next_due_date || '无日期' }}）
        </span>
      </div>
      <p class="hint">写对应任务时可勾选「上期那件做成了吗」。</p>
    </div>

    <!-- 列表态 -->
    <div v-if="!isEditMode" class="layout">
      <div class="main-col">
        <div class="panel">
          <div class="panel-head list-head">
            <span>本周期已写（{{ items.length }} 条）</span>
            <div v-if="!readOnly" class="list-actions">
              <el-button type="primary" @click="goNewItem()">新建一条</el-button>
              <el-button @click="pasteRawToCard">粘贴原文一键成卡</el-button>
            </div>
          </div>
          <div v-if="!items.length" class="muted empty-list">
            还没有任务条目。点「新建一条」开始写；写好保存后会回到这里。
          </div>
          <ul v-else class="item-list">
            <li
              v-for="(it, idx) in items"
              :key="it.id || idx"
              class="item-row"
              @click="goEditItem(idx)"
            >
              <div class="item-main">
                <div class="item-title">
                  {{ idx + 1 }}. {{ itemSummary(it).title }}
                  <el-tag v-if="it.no_action" size="small" type="info" effect="plain">无实质动作</el-tag>
                  <el-tag v-if="it.chain_id" size="small" type="info" effect="plain">已入链</el-tag>
                </div>
                <div class="item-sub">
                  <span v-if="it.project_id">{{ projectName(it.project_id) || it.project_name || ('项目 #' + it.project_id) }} · </span>
                  {{ itemSummary(it).short }}
                </div>
              </div>
              <el-button link type="primary" @click.stop="goEditItem(idx)">
                {{ readOnly ? '查看' : '编辑' }}
              </el-button>
            </li>
          </ul>
        </div>

        <div v-if="prefillSuggestions.length && !readOnly" class="panel">
          <div class="panel-head">待写入建议（来自本周任务预填）</div>
          <p class="hint">点某一条会打开新建页并带入内容，不会一次摊开多张表单。</p>
          <ul class="item-list">
            <li
              v-for="(it, idx) in prefillSuggestions"
              :key="'pf' + idx"
              class="item-row suggest-row"
              @click="goNewItem({ prefill: idx })"
            >
              <div class="item-main">
                <div class="item-title">{{ itemSummary(it).title }}</div>
                <div class="item-sub">{{ itemSummary(it).short }}</div>
              </div>
              <el-button link type="primary" @click.stop="goNewItem({ prefill: idx })">去写</el-button>
            </li>
          </ul>
        </div>

        <div v-if="hardErrors.length" class="errs">
          <div v-for="(e, i) in hardErrors" :key="i">{{ e }}</div>
        </div>
        <div v-if="softWarnings.length" class="warns">
          <div v-for="(e, i) in softWarnings" :key="i">提示：{{ e }}</div>
        </div>

        <div v-if="!readOnly" class="actions">
          <el-button type="primary" @click="goNewItem()">新建一条</el-button>
          <el-button :loading="saving" :disabled="saving || !items.length" @click="save(false)">
            保存整份草稿
          </el-button>
          <el-button v-if="agentNative" @click="router.push('/dsh')">去助手里写周报</el-button>
          <el-button v-else :disabled="saving || !items.length" @click="runAi">帮我看看有没有没想清楚的</el-button>
          <el-button type="primary" :loading="saving" :disabled="saving || !items.length" @click="save(true)">
            提交整份周报
          </el-button>
        </div>
        <el-alert
          v-else-if="viewingOthers"
          type="info"
          :closable="false"
          title="只读查看他人周报"
          style="margin:16px 0"
        />
        <el-alert
          v-else
          type="warning"
          :closable="false"
          style="margin:16px 0"
        >
          <template #title>
            历史已交周期只读。
            <el-button type="warning" link @click="unlockHistoricalEdit">解锁修改</el-button>
          </template>
        </el-alert>

        <div v-if="legacyAttachments.length" class="panel attach-panel">
          <div class="panel-head">整份周报附件（历史）</div>
          <p class="hint">早期上传、未挂到具体任务的文件；新附件请在任务卡片内上传。</p>
          <ul class="attach-list">
            <li v-for="a in legacyAttachments" :key="a.id">
              <el-button link type="primary" @click="downloadAttach(a)">{{ a.original_filename }}</el-button>
              <span class="muted"> · {{ a.uploader_name || '' }}</span>
              <el-button
                v-if="!readOnly"
                link
                type="danger"
                @click="removeAttach(a)"
              >删除</el-button>
            </li>
          </ul>
        </div>

        <div v-if="aiResult" class="panel ai">
          <div class="panel-head">检查结果</div>
          <p class="hint">{{ aiResult.message }}</p>
          <ul>
            <li v-for="(q, i) in aiResult.clarity_questions || []" :key="'c'+i">{{ q }}</li>
            <li v-for="(q, i) in aiResult.risk_alerts || []" :key="'r'+i" class="risk">{{ q }}</li>
            <li v-for="(q, i) in aiResult.missing_quant || []" :key="'m'+i">{{ q }}</li>
            <li v-for="(q, i) in aiResult.rewrite_suggestions || []" :key="'w'+i">
              改写建议（第{{ q.item_index }}条 · {{ q.field_label || q.field }}）：{{ q.suggestion }}
            </li>
          </ul>
        </div>
      </div>

      <aside class="side panel">
        <div class="panel-head">我的三句话预览</div>
        <p class="hint">写的时候看看通不通顺——这就是理清思路。</p>
        <div v-if="!previewLines.length" class="muted">填几条任务后这里会汇总</div>
        <div v-for="(line, i) in previewLines" :key="i" class="preview-line">{{ line }}</div>
      </aside>
    </div>

    <!-- 单卡编辑态 -->
    <div v-else-if="activeItem" class="layout">
      <div class="main-col">
        <div class="panel card">
          <div class="card-head">
            <span>{{ isNewMode ? '新建任务' : `编辑任务 ${editIndex + 1}` }}</span>
            <div class="card-actions">
              <el-button
                v-if="activeItem.chain_id"
                link
                type="primary"
                @click="openTimeline(activeItem.chain_id)"
              >
                任务链 · 查看进度
              </el-button>
              <el-button
                v-if="!readOnly"
                link
                type="danger"
                @click="deleteCurrentItem"
              >删除</el-button>
            </div>
          </div>

          <div v-if="activeItem.chain_id || activeItem.project_id || activeItem.linked_task_id" class="meta-row">
            <el-tag v-if="activeItem.chain_id" size="small" type="info" effect="plain">已入任务链</el-tag>
            <el-tag v-if="activeItem.project_id" size="small" effect="plain">
              {{ projectName(activeItem.project_id) || activeItem.project_name || ('项目 #' + activeItem.project_id) }}
            </el-tag>
            <el-button
              v-if="activeItem.linked_task_id"
              link
              type="primary"
              size="small"
              @click="router.push(`/tasks/${activeItem.linked_task_id}`)"
            >
              关联任务 #{{ activeItem.linked_task_id }} →
            </el-button>
          </div>

          <el-form label-position="top">
            <el-form-item label="所属项目" required>
              <div class="project-pick">
                <el-select
                  v-model="activeItem.project_id"
                  filterable
                  clearable
                  placeholder="必选：立项项目或「日常任务」"
                  style="flex: 1; min-width: 0"
                  :disabled="readOnly"
                >
                  <el-option
                    v-for="p in projectOptions"
                    :key="p.id"
                    :label="p.name"
                    :value="p.id"
                  />
                </el-select>
                <el-button
                  v-if="canCreateProject && !readOnly"
                  link
                  type="primary"
                  @click="goCreateProject(formIdx)"
                >
                  没有？去新建
                </el-button>
              </div>
            </el-form-item>
            <el-form-item label="任务概述" required>
              <el-input
                v-model="activeItem.task_title"
                maxlength="120"
                show-word-limit
                placeholder="可用「项目名-任务」：如 智安校园-实验中学请假系统录入"
                :disabled="readOnly"
                @input="scheduleStructure(formIdx)"
                @blur="applyTitleSplit(formIdx)"
              />
            </el-form-item>

            <el-form-item label="工作标签">
              <el-select
                v-model="activeItem.tags"
                multiple
                filterable
                collapse-tags
                collapse-tags-tooltip
                placeholder="输入关键字搜索标签（选 1～3 个）"
                style="width: 100%"
                :disabled="readOnly"
              >
                <el-option v-for="t in workTagOptions" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>

            <el-checkbox v-model="activeItem.no_action" :disabled="readOnly">本周此项无实质动作（不占周报正文）</el-checkbox>

            <template v-if="!activeItem.no_action">
              <el-form-item label="1. 做成了什么？" required>
                <el-input
                  v-model="activeItem.result_text"
                  type="textarea"
                  :rows="2"
                  placeholder="写可核对的结果，不要写「跟进中」"
                  :disabled="readOnly"
                  @input="scheduleStructure(formIdx)"
                />
              </el-form-item>
              <el-form-item label="量化（可选）">
                <el-input v-model="activeItem.quant_value" placeholder="如 141 路 / 80% / 2 份方案" :disabled="readOnly" />
              </el-form-item>

              <el-form-item label="2. 卡住或要协调吗？">
                <el-radio-group v-model="activeItem.stuck_status" :disabled="readOnly">
                  <el-radio value="none">无</el-radio>
                  <el-radio value="blocker">有卡点</el-radio>
                  <el-radio value="coordination">需协调</el-radio>
                </el-radio-group>
              </el-form-item>

              <div v-if="activeItem.stuck_status !== 'none'" class="expand">
                <el-form-item label="卡在哪" required>
                  <el-input v-model="activeItem.stuck_detail" placeholder="一句话" :disabled="readOnly" />
                </el-form-item>
                <el-form-item label="下一步" required>
                  <el-input v-model="activeItem.stuck_next_action" placeholder="谁在什么时间前做什么" :disabled="readOnly" />
                </el-form-item>
                <el-form-item label="下次跟进日" required>
                  <el-date-picker v-model="activeItem.stuck_follow_up" type="date" value-format="YYYY-MM-DD" :disabled="readOnly" />
                </el-form-item>
                <el-form-item label="需要谁帮忙（选填）">
                  <el-input v-model="activeItem.stuck_need_who" :disabled="readOnly" />
                </el-form-item>
              </div>

              <el-form-item label="3. 下一步何时完成？" required>
                <el-input
                  v-model="activeItem.next_goal"
                  placeholder="做什么 + 做成什么样/做多少（提交后会写入我的待办，AI 会再规范）"
                  style="margin-bottom:8px"
                  :disabled="readOnly"
                />
                <el-date-picker v-model="activeItem.next_due_date" type="date" value-format="YYYY-MM-DD" placeholder="计划完成日（截止）" :disabled="readOnly" />
              </el-form-item>

              <el-form-item label="风险（默认正常，有问题再标）">
                <el-radio-group v-model="activeItem.risk_level" :disabled="readOnly">
                  <el-radio value="normal">正常</el-radio>
                  <el-radio value="watch">需关注</el-radio>
                  <el-radio value="escalate">需升级</el-radio>
                </el-radio-group>
              </el-form-item>
              <div v-if="activeItem.risk_level !== 'normal'" class="expand">
                <el-form-item label="原因">
                  <el-input v-model="activeItem.risk_reason" :disabled="readOnly" />
                </el-form-item>
                <el-form-item label="影响日期">
                  <el-date-picker v-model="activeItem.risk_impact_date" type="date" value-format="YYYY-MM-DD" :disabled="readOnly" />
                </el-form-item>
                <el-form-item v-if="activeItem.risk_level === 'escalate'" label="协调对象" required>
                  <el-input v-model="activeItem.risk_coordinator" :disabled="readOnly" />
                </el-form-item>
              </div>

              <el-form-item v-if="priorChecks.length" label="上期那件做成了吗？">
                <el-radio-group v-model="activeItem.prior_done" :disabled="readOnly">
                  <el-radio :value="true">是</el-radio>
                  <el-radio :value="false">否</el-radio>
                </el-radio-group>
              </el-form-item>
            </template>
          </el-form>

          <div class="card-attach">
            <div class="card-attach-head">附件</div>
            <p class="hint">证明材料挂在本任务下。还没有草稿时，上传会先自动保存本条。</p>
            <el-upload
              v-if="!readOnly"
              :show-file-list="false"
              :auto-upload="false"
              :disabled="attachUploading"
              :on-change="(f: any) => onCardAttachChange(formIdx, f)"
            >
              <el-button size="small" :loading="attachUploading">上传附件</el-button>
            </el-upload>
            <ul v-if="(activeItem.attachments || []).length" class="attach-list">
              <li v-for="a in activeItem.attachments" :key="a.id">
                <el-button link type="primary" @click="downloadAttach(a)">{{ a.original_filename }}</el-button>
                <span class="muted"> · {{ a.uploader_name || '' }}</span>
                <el-button
                  v-if="!readOnly"
                  link
                  type="danger"
                  @click="removeAttach(a)"
                >删除</el-button>
              </li>
            </ul>
            <p v-else class="hint">暂无附件</p>
          </div>

          <div v-if="suggestLoading[formIdx]" class="suggest-box loading">AI 正在整理…</div>
          <div v-else-if="suggestions[formIdx]" class="suggest-box">
            <div class="panel-head">AI 建议</div>
            <p class="hint">{{ suggestions[formIdx].message }}</p>
            <div class="suggest-block">
              <div class="suggest-label">整理后</div>
              <div class="suggest-line">
                <strong>{{ suggestions[formIdx].structured?.task_title || '（无概述）' }}</strong>
              </div>
              <div
                v-if="suggestions[formIdx].structured?.tags?.length"
                class="suggest-line"
                style="display:flex;flex-wrap:wrap;gap:4px;align-items:center"
              >
                <span>标签（已自动填入）：</span>
                <el-tag
                  v-for="t in suggestions[formIdx].structured.tags"
                  :key="t"
                  size="small"
                  type="success"
                  effect="plain"
                >{{ t }}</el-tag>
              </div>
              <div class="suggest-line">做成了：{{ suggestions[formIdx].structured?.result_text || '…' }}</div>
              <div v-if="suggestions[formIdx].structured?.quant_value" class="suggest-line">
                量化：{{ suggestions[formIdx].structured.quant_value }}
              </div>
              <div class="suggest-line">下一步：{{ suggestions[formIdx].structured?.next_goal || '…' }}</div>
            </div>
            <div v-if="suggestions[formIdx].project_suggestion" class="suggest-block">
              疑似项目：{{ suggestions[formIdx].project_suggestion.name }}
              <span class="muted">（置信 {{ suggestions[formIdx].project_suggestion.confidence }}）</span>
            </div>
            <div
              v-if="suggestions[formIdx].continuation_warning"
              class="suggest-block warn-block"
            >
              {{ suggestions[formIdx].continuation_warning }}
            </div>
            <div v-if="suggestions[formIdx].chain_suggestion" class="suggest-block">
              疑似任务链：{{ suggestions[formIdx].chain_suggestion.title }}
              <span class="muted"> · {{ suggestions[formIdx].chain_suggestion.reason }}</span>
            </div>
            <div v-else-if="!activeItem.chain_id" class="suggest-block">
              <el-checkbox v-model="createChainChecked[formIdx]">采用时新建任务链（方便下周延续）</el-checkbox>
            </div>
            <div v-if="suggestions[formIdx].task_suggestion" class="suggest-block">
              <el-checkbox v-model="linkTaskChecked[formIdx]">
                挂到交办「{{ suggestions[formIdx].task_suggestion.title }}」
              </el-checkbox>
            </div>
            <div class="suggest-actions">
              <el-button type="primary" size="small" @click="applySuggestion(formIdx)">采用</el-button>
              <el-button size="small" @click="dismissSuggestion(formIdx)">忽略</el-button>
            </div>
          </div>
        </div>

        <div v-if="hardErrors.length" class="errs">
          <div v-for="(e, i) in hardErrors" :key="i">{{ e }}</div>
        </div>
        <div v-if="softWarnings.length" class="warns">
          <div v-for="(e, i) in softWarnings" :key="i">提示：{{ e }}</div>
        </div>

        <div v-if="!readOnly" class="actions">
          <el-button type="primary" :loading="saving" :disabled="saving" @click="saveCurrentItem()">
            保存本条
          </el-button>
          <el-button :disabled="saving" @click="backToList">返回列表</el-button>
        </div>
      </div>

      <aside class="side panel">
        <div class="panel-head">本条预览</div>
        <p class="hint">保存后会回到列表，可继续新建下一条。</p>
        <div v-if="activeItem" class="preview-line">
          {{ (activeItem.task_title || '').trim() || '未命名' }}｜做成了：{{ activeItem.result_text || '…' }}｜下一步：{{ activeItem.next_goal || '…' }}
        </div>
      </aside>
    </div>

    <el-drawer v-model="timelineOpen" title="任务链进度" size="420px">
      <div v-loading="timelineLoading">
        <template v-if="timeline">
          <h3 class="tl-title">{{ timeline.title }}</h3>
          <p v-if="timeline.project_name" class="hint">项目：{{ timeline.project_name }}</p>
          <div v-for="n in timeline.nodes" :key="n.item_id" class="tl-node">
            <div class="tl-date">{{ n.period_start }}{{ n.period_end && n.period_end !== n.period_start ? ' ~ ' + n.period_end : '' }}</div>
            <div class="tl-body">
              <strong>{{ n.task_title || '未命名' }}</strong>
              <div>做成了：{{ n.result_text || '—' }}</div>
              <div v-if="n.stuck_status !== 'none'">卡点：{{ n.stuck_detail || '—' }}</div>
              <div>下一步：{{ n.next_goal || '—' }}（{{ n.next_due_date || '未定日' }}）</div>
            </div>
          </div>
          <div v-if="!timeline.nodes?.length" class="muted">暂无节点（保存并提交后会沉淀到时间线）</div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { max-width: 1100px; }
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px;
  margin-bottom: 16px;
  box-shadow: var(--wb-shadow-soft);
}
.panel-head { font-weight: 600; margin-bottom: 8px; }
.period-chips { display: flex; flex-wrap: wrap; }
.toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.layout { display: grid; grid-template-columns: 1fr 280px; gap: 16px; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.card-head { display: flex; justify-content: space-between; margin-bottom: 8px; font-weight: 600; align-items: center; gap: 8px; }
.card-actions { display: flex; gap: 4px; align-items: center; }
.meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.project-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.expand {
  background: #fafafa;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.suggest-box {
  margin-top: 12px;
  padding: 12px;
  border: 1px dashed #9bb6d4;
  border-radius: 8px;
  background: #f7fbff;
}
.suggest-box.loading { color: var(--wb-text-muted); font-size: 13px; }
.suggest-block { font-size: 13px; margin: 8px 0; line-height: 1.45; }
.warn-block { color: #b45309; background: #fffbeb; padding: 8px 10px; border-radius: 6px; }
.suggest-label { font-weight: 600; margin-bottom: 4px; }
.suggest-line { margin: 2px 0; }
.suggest-actions { display: flex; gap: 8px; margin-top: 10px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0 24px; }
.attach-panel { margin-bottom: 16px; }
.card-attach {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--wb-border);
}
.card-attach-head { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.attach-list { list-style: none; padding: 0; margin: 10px 0 0; }
.attach-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 6px 0;
  border-bottom: 1px solid var(--wb-border);
  font-size: 13px;
}
.errs { color: #c45656; margin: 12px 0; font-size: 13px; }
.warns { color: #b78105; margin: 8px 0; font-size: 13px; }
.hint { color: var(--wb-text-muted); font-size: 13px; }
.muted { color: #999; font-size: 13px; }
.preview-line { font-size: 13px; line-height: 1.5; margin-bottom: 10px; color: #333; }
.ai ul { padding-left: 18px; margin: 8px 0; font-size: 13px; }
.ai .risk { color: #c45656; }
.prior-row { font-size: 13px; margin-bottom: 6px; }
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.list-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.empty-list { padding: 8px 0 4px; }
.item-list { list-style: none; padding: 0; margin: 0; }
.item-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--wb-border);
  cursor: pointer;
}
.item-row:hover { background: #fafafa; }
.item-row:last-child { border-bottom: none; }
.item-main { min-width: 0; flex: 1; }
.item-title {
  font-weight: 600;
  font-size: 14px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.item-sub { font-size: 13px; color: var(--wb-text-muted); margin-top: 4px; line-height: 1.4; }
.suggest-row { background: #fbfcff; }
.tl-title { margin: 0 0 8px; font-size: 16px; }
.tl-node {
  border-left: 3px solid #5b8def;
  padding: 0 0 16px 12px;
  margin-left: 4px;
}
.tl-date { font-size: 12px; color: #666; margin-bottom: 4px; }
.tl-body { font-size: 13px; line-height: 1.5; }
</style>
