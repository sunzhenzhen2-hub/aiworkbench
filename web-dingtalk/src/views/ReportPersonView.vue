<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request, getToken } from '@/utils/api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const userId = computed(() => Number(route.params.userId))
const periodStart = computed(() =>
  typeof route.query.period === 'string' ? route.query.period : '',
)

const loading = ref(false)
const error = ref('')
const workbench = ref<any>(null)
const timelines = ref<{ chains?: any[] }>({ chains: [] })
const historyReport = ref<any>(null)
const selectedHistoryId = ref<number | null>(null)
const eventOpen = ref<Record<string, boolean>>({})

const ACTION_LABEL: Record<string, string> = {
  created: '创建任务',
  created_from_report: '从周报创建',
  updated_from_report: '从周报更新',
  accepted: '已接收',
  status_changed: '状态变更',
  priority_changed: '优先级变更',
  assigned: '改派',
  completed: '完成',
}

function pct(n?: number) {
  return Math.round((n || 0) * 100)
}

async function load() {
  if (!userId.value) return
  loading.value = true
  error.value = ''
  try {
    const params: Record<string, string> = { period_type: 'week' }
    if (periodStart.value) params.period_start = periodStart.value
    const [wb, tl] = await Promise.all([
      request<any>(`/progress-reports/users/${userId.value}/workbench`, 'GET', params),
      request<any>(`/progress-reports/users/${userId.value}/timelines`, 'GET', {
        ...params,
        include_history: 'true',
      }),
    ])
    workbench.value = wb
    timelines.value = tl || { chains: [] }
    if (wb?.report?.id) {
      selectedHistoryId.value = wb.report.id
      historyReport.value = wb.report
    } else {
      historyReport.value = null
      selectedHistoryId.value = null
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function pickHistory(h: any) {
  if (!h.report_id) return
  selectedHistoryId.value = h.report_id
  if (workbench.value?.report?.id === h.report_id) {
    historyReport.value = workbench.value.report
    return
  }
  historyReport.value = await request(`/progress-reports/${h.report_id}`, 'GET')
}

async function rereview() {
  const rid = workbench.value?.report?.id
  if (!rid) return
  const data = await request(`/progress-reports/${rid}/ai-review?force=true`, 'POST')
  if (workbench.value) workbench.value.phase1_review = data
}

async function downloadAttach(a: { id: number; original_filename: string }) {
  const rid = historyReport.value?.id || selectedHistoryId.value
  if (!rid) return
  const base = import.meta.env.VITE_API_BASE || '/aiworkbench/api'
  try {
    const res = await fetch(
      `${base}/progress-reports/${rid}/attachments/${a.id}/download`,
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

onMounted(() => {
  if (!auth.user?.permissions?.includes('report.view.all')) {
    router.replace({ name: 'reports', query: { tab: 'mine' } })
    return
  }
  load()
})

function goBack() {
  const query: Record<string, string> = { tab: 'team' }
  if (periodStart.value) query.period = periodStart.value
  router.push({ name: 'reports', query })
}
</script>

<template>
  <div class="page">
    <button type="button" class="back" @click="goBack">
      ← 返回看板
    </button>
    <h1 class="page-title">{{ workbench?.user?.name || '人员' }} · 周报</h1>

    <div v-if="error" class="deny">{{ error }}</div>
    <div v-else-if="loading" class="muted">加载中…</div>
    <template v-else-if="workbench">
      <div class="card stats">
        <div class="stat">
          <div class="n">{{ pct(workbench.completion_stats?.submit_rate) }}%</div>
          <div class="l">提交率</div>
        </div>
        <div class="stat">
          <div class="n">{{ pct(workbench.completion_stats?.prior_done_rate) }}%</div>
          <div class="l">兑现率</div>
        </div>
        <div class="stat">
          <div class="n">{{ workbench.submitted ? '已交' : '未交' }}</div>
          <div class="l">本期</div>
        </div>
      </div>

      <div class="card">
        <div class="sec-title row">
          <span>一阶段评价</span>
          <button v-if="workbench.submitted" type="button" class="link" @click="rereview">重评</button>
        </div>
        <p v-if="workbench.review_unavailable_reason" class="muted">{{ workbench.review_unavailable_reason }}</p>
        <template v-else-if="workbench.phase1_review">
          <p>{{ workbench.phase1_review.summary }}</p>
          <ul v-if="workbench.phase1_review.strengths?.length">
            <li v-for="(s, i) in workbench.phase1_review.strengths" :key="'s'+i">{{ s }}</li>
          </ul>
          <ul v-if="workbench.phase1_review.risks?.length" class="risks">
            <li v-for="(s, i) in workbench.phase1_review.risks" :key="'r'+i">{{ s }}</li>
          </ul>
          <p class="muted">{{ workbench.phase1_review.completion_comment }}</p>
        </template>
      </div>

      <div class="card">
        <div class="sec-title">历史周期</div>
        <div class="chips">
          <button
            v-for="h in workbench.history || []"
            :key="h.period_start + h.status"
            type="button"
            class="chip"
            :class="{ on: selectedHistoryId === h.report_id }"
            @click="pickHistory(h)"
          >
            {{ h.period_start }} · {{ h.status === 'submitted' ? '已交' : '草稿' }}
          </button>
        </div>
      </div>

      <div class="card">
        <div class="sec-title">周报正文</div>
        <div v-if="!historyReport" class="muted">本期未提交</div>
        <div v-for="it in historyReport?.items || []" :key="it.id" class="item">
          <div class="t">{{ it.task_title || '未命名任务' }}</div>
          <button
            v-if="it.linked_task_id"
            type="button"
            class="link"
            @click="router.push(`/tasks/${it.linked_task_id}`)"
          >
            关联任务 #{{ it.linked_task_id }} →
          </button>
          <template v-if="!it.no_action">
            <div>做成了：{{ it.result_text || '—' }}</div>
            <div>下一步：{{ it.next_goal || '—' }}（{{ it.next_due_date || '未定日' }}）</div>
            <div v-if="it.stuck_status !== 'none'" class="stuck">卡点：{{ it.stuck_detail }}</div>
          </template>
          <div v-else class="muted">无实质动作</div>
        </div>
        <div v-if="historyReport" class="attach-block">
          <div class="sec-title" style="margin-top:12px">附件上传</div>
          <div v-if="!(historyReport.attachments || []).length" class="muted">暂无附件</div>
          <div v-for="a in historyReport.attachments || []" :key="a.id" class="attach-row">
            <button type="button" class="link" @click="downloadAttach(a)">
              {{ a.original_filename }}
            </button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="sec-title">进度链</div>
        <div v-if="!timelines.chains?.length" class="muted">暂无任务链</div>
        <div v-for="c in timelines.chains || []" :key="c.chain_id" class="chain">
          <div class="t">{{ c.title }}</div>
          <div v-for="n in c.progress_nodes || []" :key="n.item_id" class="node">
            <div class="when">{{ n.period_start }}</div>
            <div>做成了：{{ n.result_text || '—' }}</div>
            <div>下一步：{{ n.next_goal || '—' }}</div>
          </div>
          <button
            type="button"
            class="link"
            @click="eventOpen[c.chain_id] = !eventOpen[c.chain_id]"
          >
            事件链（{{ c.task_events?.length || 0 }}）{{ eventOpen[c.chain_id] ? '收起' : '展开' }}
          </button>
          <div v-if="eventOpen[c.chain_id]" class="events">
            <div v-for="e in c.task_events || []" :key="e.id" class="ev">
              {{ ACTION_LABEL[e.action] || e.action }}
              <span class="muted"> · {{ e.actor_name }}</span>
            </div>
            <div v-if="!(c.task_events || []).length" class="muted">无关联交办事件</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.back {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  padding: 0 0 8px;
  font-size: 14px;
  cursor: pointer;
}
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat {
  text-align: center;
  background: var(--wb-mint-bg);
  border-radius: 12px;
  padding: 10px 6px;
}
.stat .n { font-size: 18px; font-weight: 700; color: var(--wb-mint-text); }
.stat .l { font-size: 12px; color: var(--wb-muted); }
.sec-title { font-weight: 600; margin-bottom: 8px; }
.sec-title.row { display: flex; justify-content: space-between; align-items: center; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  border: 1px solid var(--wb-border);
  background: var(--wb-surface);
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--wb-text);
}
.chip.on {
  background: var(--wb-mint-bg);
  border-color: var(--wb-mint);
  color: var(--wb-mint-text);
}
.item, .node, .chain {
  background: var(--wb-bg);
  border-radius: var(--wb-radius-sm);
  padding: 10px;
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 1.5;
}
.t { font-weight: 600; margin-bottom: 4px; }
.when { font-size: 12px; color: var(--wb-muted); }
.stuck { color: var(--wb-warn); }
.risks { color: var(--wb-warn); }
.link {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  padding: 0;
  font-size: 13px;
  cursor: pointer;
}
.attach-block { margin-top: 8px; }
.attach-row { margin-top: 6px; }
.events { margin-top: 8px; }
.ev { font-size: 12px; padding: 4px 0; }
</style>
