<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import StatsBoardPanel from '../components/analyze/StatsBoardPanel.vue'
import { isAgentNativeProduct } from '../productLine'
import AgentNativeHint from '../components/AgentNativeHint.vue'

const ANALYZE_CTX_KEY = 'wb_assist_analyze_ctx'

interface ActionItem {
  type?: string
  priority?: string
  title?: string
  detail?: string
  issue_nature?: string
  party_type?: string
  user?: { id: number; name: string; username?: string } | null
  links?: Record<string, string>
}

interface FocusItem {
  text: string
  issue_nature?: string
}

interface Snapshot {
  on_track_hint?: string
  top_risk?: string
  decisions_needed?: { title?: string; detail?: string; issue_nature?: string; links?: Record<string, string> }[]
}

interface AnalyzePayload {
  kind?: string
  summary?: string
  conclusion?: string
  focus?: string[]
  focus_items?: FocusItem[]
  snapshot?: Snapshot
  source?: string
  period_start?: string
  period_end?: string
  stats?: Record<string, number | boolean | string>
  actions?: ActionItem[]
  prompt_pack?: string
  missing_reporters?: { id: number; name: string }[]
  guidance_draft?: string[]
  phase1_review?: { summary?: string; risks?: string[]; strengths?: string[] } | null
  related_reports?: { report_id: number; user?: { name: string }; task_title?: string; result_text?: string }[]
  project?: { id: number; name: string }
  user?: { id: number; name: string }
}

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()
const canAnalyze = computed(() => userStore.hasPermission('assist.analyze'))
const canStats = computed(() => userStore.hasPermission('stats.view'))

const tab = ref(canAnalyze.value ? 'team' : canStats.value ? 'stats' : 'team')
const loading = ref(false)
const streaming = ref(false)
const teamData = ref<AnalyzePayload | null>(null)
const projectData = ref<AnalyzePayload | null>(null)
const personData = ref<AnalyzePayload | null>(null)
const statsPanelRef = ref<{ load: () => Promise<void> } | null>(null)

const projects = ref<{ id: number; name: string }[]>([])
const users = ref<{ id: number; name: string; username: string }[]>([])
const selectedProjectId = ref<number | null>(null)
const selectedUserId = ref<number | null>(null)

const guidanceText = ref('')
const guidanceNotify = ref(true)
const savingGuidance = ref(false)
const nudgingIdx = ref<number | null>(null)

const NATURE_LABEL: Record<string, string> = {
  person: '人的问题',
  external: '外部依赖',
  process: '流程/审批',
  unclear: '待核实',
}
const NATURE_TAG: Record<string, 'danger' | 'warning' | 'info' | ''> = {
  person: 'danger',
  external: 'warning',
  process: 'info',
  unclear: '',
}

function natureLabel(n?: string) {
  return NATURE_LABEL[n || ''] || '待核实'
}

function natureTagType(n?: string) {
  return NATURE_TAG[n || ''] ?? ''
}

function idFromLink(path?: string): number | null {
  if (!path) return null
  const m = path.match(/\/(?:tasks|projects|reports\/person)\/(\d+)/)
  return m ? Number(m[1]) : null
}

async function nudgeAction(a: ActionItem, idx: number) {
  nudgingIdx.value = idx
  try {
    const taskId = idFromLink(a.links?.task)
    const projectId = idFromLink(a.links?.project)
    const personId = idFromLink(a.links?.person) || a.user?.id || null
    await api.post('/ai/analyze/nudge', {
      type: a.type || '',
      user_id: personId,
      task_id: taskId,
      project_id: projectId,
      title: a.title || '',
      detail: a.detail || '',
    })
    ElMessage.success('已发送催办')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '催办失败')
  } finally {
    nudgingIdx.value = null
  }
}

let streamAbort: AbortController | null = null

function analyzeStreamUrl(path: string) {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  return `${base}/api${path}`
}

function setCurrent(data: AnalyzePayload | null) {
  if (tab.value === 'project') projectData.value = data
  else if (tab.value === 'person') personData.value = data
  else if (tab.value === 'team') teamData.value = data
}

const current = computed(() => {
  if (tab.value === 'project') return projectData.value
  if (tab.value === 'person') return personData.value
  if (tab.value === 'team') return teamData.value
  return null
})

function resolveDefaultTab(): string {
  const q = typeof route.query.tab === 'string' ? route.query.tab : ''
  if (q === 'stats' && canStats.value) return 'stats'
  if (q === 'project' && canAnalyze.value) return 'project'
  if (q === 'person' && canAnalyze.value) return 'person'
  if (q === 'team' && canAnalyze.value) return 'team'
  if (canAnalyze.value) return 'team'
  if (canStats.value) return 'stats'
  return 'team'
}

async function refresh() {
  if (tab.value === 'stats') {
    await nextTick()
    await statsPanelRef.value?.load()
    return
  }
  if (!canAnalyze.value) return
  if (tab.value === 'team') await loadTeam()
  else if (tab.value === 'project') await loadProject()
  else if (tab.value === 'person') await loadPerson()
}

async function consumeAnalyzeStream(path: string) {
  streamAbort?.abort()
  streamAbort = new AbortController()
  loading.value = true
  streaming.value = false
  setCurrent(null)

  const token = localStorage.getItem('token') || ''
  try {
    const resp = await fetch(analyzeStreamUrl(path), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: streamAbort.signal,
    })
    if (!resp.ok || !resp.body) {
      const detail = await resp.text().catch(() => '')
      throw new Error(detail || `加载失败(${resp.status})`)
    }
    loading.value = false
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let currentEvent = 'message'
    let narrativeMode = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n')
      buffer = parts.pop() || ''
      for (const rawLine of parts) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          currentEvent = 'message'
          continue
        }
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
          continue
        }
        if (!line.startsWith('data:')) continue
        const dataStr = line.slice(5).trim()
        let data: AnalyzePayload & { text?: string; detail?: string } = {}
        try {
          data = JSON.parse(dataStr)
        } catch {
          continue
        }
        if (currentEvent === 'error') {
          throw new Error(data.detail || '分析失败')
        }
        if (currentEvent === 'partial') {
          setCurrent(data)
          if (tab.value === 'person') {
            guidanceText.value = ((data.guidance_draft as string[]) || []).join('\n')
          }
        } else if (currentEvent === 'narrative_start') {
          narrativeMode = true
          streaming.value = true
          const cur = current.value || {}
          setCurrent({ ...cur, summary: '', conclusion: '', source: undefined })
        } else if (currentEvent === 'delta') {
          streaming.value = true
          const cur = current.value || {}
          const next = (cur.summary || '') + String(data.text || '')
          setCurrent({ ...cur, summary: next, conclusion: next })
        } else if (currentEvent === 'done') {
          streaming.value = false
          narrativeMode = false
          setCurrent(data)
          if (tab.value === 'person') {
            guidanceText.value = ((data.guidance_draft as string[]) || []).join('\n')
          }
        }
      }
    }
    if (narrativeMode) streaming.value = false
  } catch (err: any) {
    if (err?.name === 'AbortError') return
    streaming.value = false
    loading.value = false
    throw err
  } finally {
    if (!streamAbort?.signal.aborted) {
      streaming.value = false
      loading.value = false
    }
  }
}

const STAT_LABELS: Record<string, string> = {
  report_expected: '应提交周报',
  report_submitted: '已提交周报',
  report_submit_rate: '周报提交率',
  task_total: '任务总数',
  task_done: '已完成任务',
  task_completion_rate: '任务完成率',
  report_quality_alerts: '质量告警',
  open_blockers: '未关卡点',
  overdue_tasks: '逾期任务',
  pending_accept: '待接收交办',
  related_report_items: '关联周报条数',
  weeks: '统计周数',
  submitted_weeks: '已交周数',
  this_week_submitted: '本周已交',
}

const statChips = computed(() => {
  const stats = current.value?.stats || {}
  return Object.entries(stats).map(([k, v]) => {
    let display: string | number | boolean = v as any
    if (typeof v === 'number' && String(k).includes('rate')) {
      display = `${Math.round(v * 100)}%`
    } else if (typeof v === 'boolean') {
      display = v ? '是' : '否'
    }
    return { key: k, label: STAT_LABELS[k] || k, value: display }
  })
})

async function loadOptions() {
  const [projRes, userRes] = await Promise.all([
    api.get('/projects', { params: { page_size: 100 } }),
    api.get('/admin/users'),
  ])
  projects.value = (projRes.data?.items || []).map((p: any) => ({ id: p.id, name: p.name }))
  users.value = (userRes.data || [])
    .filter((u: any) => u.is_active !== false)
    .map((u: any) => ({ id: u.id, name: u.name || u.username, username: u.username }))
  if (!selectedProjectId.value && projects.value.length) {
    selectedProjectId.value = projects.value[0].id
  }
  if (!selectedUserId.value && users.value.length) {
    selectedUserId.value = users.value[0].id
  }
}

async function loadTeam() {
  if (agentNative) return
  try {
    await consumeAnalyzeStream('/ai/analyze/team/stream')
  } catch (err: any) {
    ElMessage.error(err?.message || '加载团队分析失败')
  }
}

async function loadProject() {
  if (!selectedProjectId.value) return
  if (agentNative) return
  try {
    await consumeAnalyzeStream(`/ai/analyze/project/${selectedProjectId.value}/stream`)
  } catch (err: any) {
    ElMessage.error(err?.message || '加载项目分析失败')
  }
}

async function loadPerson() {
  if (!selectedUserId.value) return
  if (agentNative) return
  try {
    await consumeAnalyzeStream(`/ai/analyze/person/${selectedUserId.value}/stream`)
  } catch (err: any) {
    ElMessage.error(err?.message || '加载人员分析失败')
  }
}

function goLink(path?: string) {
  if (!path) return
  router.push(path)
}

function openInAssistant() {
  const pack = current.value?.prompt_pack || ''
  if (!pack) {
    ElMessage.warning('暂无可带入助手的材料')
    return
  }
  sessionStorage.setItem(
    ANALYZE_CTX_KEY,
    JSON.stringify({
      kind: current.value?.kind || tab.value,
      prompt_pack: pack,
      summary: current.value?.summary || '',
      at: Date.now(),
    }),
  )
  router.push('/dsh')
}

async function copyPrompt() {
  const pack = current.value?.prompt_pack || ''
  if (!pack) return
  try {
    await navigator.clipboard.writeText(pack)
    ElMessage.success('提问稿已复制，可粘贴到智能助手')
  } catch {
    ElMessage.error('复制失败，请手动选中复制')
  }
}

async function saveGuidance() {
  if (!selectedUserId.value) return
  const content = guidanceText.value.trim()
  if (!content) {
    ElMessage.warning('请先填写指导内容')
    return
  }
  savingGuidance.value = true
  try {
    await api.post('/ai/analyze/guidance', {
      target_user_id: selectedUserId.value,
      content,
      action_items: personData.value?.guidance_draft || [],
      project_id: null,
      source: 'admin',
      notify: guidanceNotify.value,
    })
    ElMessage.success(guidanceNotify.value ? '已保存并通知对方' : '已保存指导')
    await loadPerson()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  } finally {
    savingGuidance.value = false
  }
}

watch(tab, () => {
  refresh()
})

watch(selectedProjectId, () => {
  if (tab.value === 'project') loadProject()
})

watch(selectedUserId, () => {
  if (tab.value === 'person') loadPerson()
})

onMounted(async () => {
  const next = resolveDefaultTab()
  if (canAnalyze.value) {
    try {
      await loadOptions()
    } catch {
      /* 用户列表需 admin.user；失败时仍可看团队分析 */
    }
  }
  if (next !== tab.value) {
    tab.value = next
  } else {
    await refresh()
  }
})
</script>

<template>
  <div v-loading="loading && tab !== 'stats'" class="analyze-page" :class="{ 'analyze-page--wide': tab === 'stats' }">
    <div class="page-head">
      <div>
        <h1 class="wb-page-title">分析管控</h1>
        <p class="wb-page-desc">
          串联项目、任务与周报结论，并可切换到人员/项目数据看板（不做正式打分）。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="refresh">刷新</el-button>
        <template v-if="tab !== 'stats'">
          <el-button v-if="!agentNative" @click="copyPrompt">复制提问稿</el-button>
          <el-button type="primary" @click="openInAssistant">在智能助手中继续问</el-button>
        </template>
      </div>
    </div>
    <AgentNativeHint v-if="agentNative && tab !== 'stats'" tip="分析结论请到「我的助手」里问，这里只保留清单与看板。" />

    <el-tabs v-model="tab">
      <el-tab-pane v-if="canAnalyze" label="团队本周" name="team" />
      <el-tab-pane v-if="canAnalyze" label="按项目" name="project" />
      <el-tab-pane v-if="canAnalyze" label="按人员" name="person" />
      <el-tab-pane v-if="canStats" label="数据看板" name="stats" />
    </el-tabs>

    <el-alert
      v-if="!canAnalyze && canStats"
      type="info"
      :closable="false"
      show-icon
      title="当前账号仅有数据看板权限，结论/催办类 Tab 未开放。"
      class="perm-hint"
    />

    <StatsBoardPanel v-if="tab === 'stats' && canStats" ref="statsPanelRef" />

    <template v-if="tab !== 'stats' && canAnalyze">
    <div v-if="tab === 'project'" class="picker-row">
      <span class="picker-label">项目</span>
      <el-select v-model="selectedProjectId" filterable placeholder="选择项目" style="width: 320px">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
    </div>
    <div v-if="tab === 'person'" class="picker-row">
      <span class="picker-label">人员</span>
      <el-select v-model="selectedUserId" filterable placeholder="选择人员" style="width: 280px">
        <el-option
          v-for="u in users"
          :key="u.id"
          :label="`${u.name}（${u.username}）`"
          :value="u.id"
        />
      </el-select>
    </div>

    <section v-if="current || loading" class="panel">
      <div class="panel-head">
        结论
        <span v-if="loading" class="stream-hint">正在汇总数据…</span>
        <span v-else-if="streaming" class="stream-hint">AI 正在写结论…</span>
      </div>
      <div v-if="current?.snapshot" class="snapshot-box">
        <div class="snap-item">
          <span class="snap-label">是否按时</span>
          <span>{{ current.snapshot.on_track_hint }}</span>
        </div>
        <div class="snap-item">
          <span class="snap-label">最大风险</span>
          <span>{{ current.snapshot.top_risk }}</span>
        </div>
        <div v-if="current.snapshot.decisions_needed?.length" class="snap-item">
          <span class="snap-label">需要决策</span>
          <ul class="decision-list">
            <li v-for="(d, i) in current.snapshot.decisions_needed" :key="i">
              <el-tag size="small" :type="natureTagType(d.issue_nature)" class="nature-tag">
                {{ natureLabel(d.issue_nature) }}
              </el-tag>
              {{ d.title }}
              <span v-if="d.detail" class="decision-detail"> — {{ d.detail }}</span>
            </li>
          </ul>
        </div>
      </div>
      <p class="conclusion">
        {{ current?.summary || current?.conclusion || (loading ? '请稍候' : '') }}
        <span v-if="streaming" class="caret">▍</span>
      </p>
      <p v-if="current?.period_start" class="meta">
        周期 {{ current.period_start }}～{{ current.period_end || '' }}
        <span v-if="current.source"> · 来源 {{ current.source === 'llm' ? 'AI' : '规则' }}</span>
        <span v-else-if="streaming"> · 生成中</span>
      </p>
      <ul v-if="current?.focus_items?.length" class="focus-list">
        <li v-for="(f, i) in current!.focus_items" :key="i">
          <el-tag size="small" :type="natureTagType(f.issue_nature)" class="nature-tag">
            {{ natureLabel(f.issue_nature) }}
          </el-tag>
          {{ f.text }}
        </li>
      </ul>
      <ul v-else-if="current?.focus?.length" class="focus-list">
        <li v-for="(f, i) in current!.focus" :key="i">{{ f }}</li>
      </ul>
      <div v-if="statChips.length" class="stat-row">
        <span v-for="s in statChips" :key="s.key" class="stat-chip">
          {{ s.label }}：{{ s.value }}
        </span>
      </div>
    </section>

    <section v-if="current?.actions?.length" class="panel">
      <div class="panel-head">催办与指导清单</div>
      <div v-for="(a, idx) in current!.actions" :key="idx" class="action-row">
        <div class="action-main">
          <el-tag size="small" :type="a.priority === 'high' ? 'danger' : 'info'">
            {{ a.priority === 'high' ? '优先' : '普通' }}
          </el-tag>
          <el-tag size="small" :type="natureTagType(a.issue_nature)" class="nature-tag">
            {{ natureLabel(a.issue_nature) }}
          </el-tag>
          <strong>{{ a.title }}</strong>
          <span class="action-detail">{{ a.detail }}</span>
        </div>
        <div class="action-links">
          <el-button
            type="warning"
            link
            :loading="nudgingIdx === idx"
            @click="nudgeAction(a, idx)"
          >催一下</el-button>
          <el-button
            v-if="a.links?.person"
            link
            type="primary"
            @click="goLink(a.links.person)"
          >看人员</el-button>
          <el-button
            v-if="a.links?.task"
            link
            type="primary"
            @click="goLink(a.links.task)"
          >看任务</el-button>
          <el-button
            v-if="a.links?.project"
            link
            type="primary"
            @click="goLink(a.links.project)"
          >看项目</el-button>
        </div>
      </div>
    </section>

    <section v-if="tab === 'project' && projectData?.related_reports?.length" class="panel">
      <div class="panel-head">本周关联周报</div>
      <div
        v-for="(r, i) in projectData.related_reports"
        :key="i"
        class="related-row"
      >
        <div class="related-meta">{{ r.user?.name }} · {{ r.task_title || '条目' }}</div>
        <div class="related-body">{{ r.result_text }}</div>
      </div>
    </section>

    <section v-if="tab === 'person' && personData" class="panel">
      <div class="panel-head">指导话术</div>
      <div v-if="personData.phase1_review" class="review-box">
        <div class="related-meta">本周一阶段评价</div>
        <p>{{ personData.phase1_review.summary }}</p>
      </div>
      <el-input
        v-model="guidanceText"
        type="textarea"
        :rows="5"
        placeholder="可编辑后保存为指导备注"
      />
      <div class="guidance-actions">
        <el-checkbox v-model="guidanceNotify">保存时通知对方</el-checkbox>
        <el-button type="primary" :loading="savingGuidance" @click="saveGuidance">
          保存指导
        </el-button>
      </div>
    </section>
    </template>
  </div>
</template>

<style scoped>
.analyze-page {
  max-width: 1100px;
}
.analyze-page--wide {
  max-width: 1200px;
}
.perm-hint {
  margin-bottom: 12px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 8px;
}
.head-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
}
.picker-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 0 16px;
}
.picker-label {
  color: var(--wb-text-secondary);
  font-size: 14px;
}
.panel {
  background: var(--wb-bg-muted);
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 18px;
  margin-bottom: 14px;
}
.panel-head {
  font-weight: 600;
  margin-bottom: 10px;
  color: var(--wb-text-primary);
  display: flex;
  align-items: center;
  gap: 10px;
}
.stream-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--wb-accent);
}
.snapshot-box {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.snap-item {
  font-size: 14px;
  line-height: 1.5;
}
.snap-label {
  display: inline-block;
  min-width: 72px;
  font-weight: 600;
  color: var(--wb-text-secondary);
  margin-right: 8px;
}
.decision-list {
  margin: 6px 0 0;
  padding-left: 18px;
}
.decision-detail {
  color: var(--wb-text-secondary);
  font-size: 13px;
}
.nature-tag {
  margin-right: 6px;
  vertical-align: middle;
}
.conclusion {
  margin: 0 0 8px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--wb-text-primary);
  white-space: pre-wrap;
  min-height: 1.6em;
}
.caret {
  display: inline-block;
  width: 0.5em;
  color: var(--wb-accent);
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.meta {
  margin: 0 0 8px;
  color: var(--wb-text-muted);
  font-size: 13px;
}
.focus-list {
  margin: 0;
  padding-left: 18px;
  color: var(--wb-text-secondary);
  line-height: 1.7;
}
.stat-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.stat-chip {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  background: #fff;
  border: 1px solid var(--wb-border);
  color: var(--wb-text-secondary);
}
.action-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--wb-border);
}
.action-row:last-child {
  border-bottom: 0;
}
.action-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.action-detail {
  color: var(--wb-text-secondary);
  font-size: 13px;
}
.action-links {
  flex-shrink: 0;
}
.related-row {
  padding: 8px 0;
  border-bottom: 1px solid var(--wb-border);
}
.related-row:last-child {
  border-bottom: 0;
}
.related-meta {
  font-size: 13px;
  color: var(--wb-text-muted);
  margin-bottom: 4px;
}
.related-body {
  font-size: 14px;
  color: var(--wb-text-secondary);
  line-height: 1.5;
}
.review-box {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid var(--wb-border);
}
.guidance-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
</style>
