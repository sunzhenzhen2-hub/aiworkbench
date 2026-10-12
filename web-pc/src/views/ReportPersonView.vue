<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import ReportProgressChain from '../components/report-board/ReportProgressChain.vue'
import ReportEventChain from '../components/report-board/ReportEventChain.vue'

const route = useRoute()
const router = useRouter()

const userId = computed(() => Number(route.params.userId))
const periodType = computed(() =>
  route.query.period_type === 'day' ? 'day' : 'week',
)
const periodStart = computed(() =>
  typeof route.query.period === 'string' ? route.query.period : '',
)

const loading = ref(false)
const workbench = ref<any>(null)
const timelines = ref<any>({ chains: [] })
const historyReport = ref<any>(null)
const selectedHistoryId = ref<number | null>(null)
const reviewLoading = ref(false)

const pageTitle = computed(() => {
  const name = workbench.value?.user?.name
  const kind = periodType.value === 'day' ? '日报' : '周报'
  return name ? `${name} · ${kind}详情` : `人员${kind}详情`
})

function pct(n: number | undefined) {
  return Math.round((n || 0) * 100)
}

/** 已选项目名，或推断归属名 */
function itemProjectLabel(it: any): string {
  return (it?.project_name || it?.inferred_project_name || '').trim()
}

async function load() {
  if (!userId.value || Number.isNaN(userId.value)) return
  loading.value = true
  historyReport.value = null
  selectedHistoryId.value = null
  try {
    const params: Record<string, string | boolean> = {
      period_type: periodType.value,
    }
    if (periodStart.value) params.period_start = periodStart.value
    const [wbRes, tlRes] = await Promise.all([
      api.get(`/progress-reports/users/${userId.value}/workbench`, { params }),
      api.get(`/progress-reports/users/${userId.value}/timelines`, {
        params: { ...params, include_history: true },
      }),
    ])
    workbench.value = wbRes.data
    timelines.value = tlRes.data
    if (wbRes.data?.report?.id) {
      selectedHistoryId.value = wbRes.data.report.id
      historyReport.value = wbRes.data.report
    }
  } catch (e: any) {
    workbench.value = null
    ElMessage.error(e?.response?.data?.detail || '加载人员详情失败')
  } finally {
    loading.value = false
  }
}

async function pickHistory(h: any) {
  if (!h.report_id) {
    ElMessage.info('该周期未提交，无正文')
    return
  }
  selectedHistoryId.value = h.report_id
  if (workbench.value?.report?.id === h.report_id) {
    historyReport.value = workbench.value.report
    return
  }
  try {
    const res = await api.get(`/progress-reports/${h.report_id}`)
    historyReport.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载该周期正文失败')
  }
}

async function rereview() {
  const rid = workbench.value?.report?.id
  if (!rid) return
  reviewLoading.value = true
  try {
    const res = await api.post(`/progress-reports/${rid}/ai-review`, null, {
      params: { force: true },
    })
    if (workbench.value) workbench.value.phase1_review = res.data
    ElMessage.success('已重新生成一阶段评价')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '评价失败')
  } finally {
    reviewLoading.value = false
  }
}

function goBack() {
  router.push({
    path: '/reports',
    query: {
      period: periodStart.value || undefined,
      period_type: periodType.value,
    },
  })
}

async function downloadAttach(row: any) {
  const rid = historyReport.value?.id || selectedHistoryId.value
  if (!rid) return
  try {
    const res = await api.get(`/progress-reports/${rid}/attachments/${row.id}/download`, {
      responseType: 'blob',
    })
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

watch(
  () => [userId.value, periodStart.value, periodType.value] as const,
  () => {
    if (userId.value) load()
  },
)

onMounted(() => {
  if (userId.value) load()
})
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <el-button text @click="goBack">← 返回周报管理</el-button>
      <h1 class="wb-page-title">{{ pageTitle }}</h1>
    </div>
    <p v-if="workbench?.period_start" class="wb-page-desc">
      周期：{{ workbench.period_start
      }}{{
        workbench.period_end && workbench.period_end !== workbench.period_start
          ? ' ~ ' + workbench.period_end
          : ''
      }}
    </p>

    <el-empty v-if="!loading && !workbench" description="未找到该人员详情" />

    <template v-if="workbench">
      <section class="sec panel">
        <div class="sec-title">概览</div>
        <div class="stats">
          <div class="stat">
            <div class="n">{{ pct(workbench.completion_stats?.submit_rate) }}%</div>
            <div class="l">近几周提交率</div>
          </div>
          <div class="stat">
            <div class="n">{{ pct(workbench.completion_stats?.prior_done_rate) }}%</div>
            <div class="l">历史任务兑现</div>
          </div>
          <div class="stat">
            <div class="n">{{ workbench.submitted ? '已交' : '未交' }}</div>
            <div class="l">本期状态</div>
          </div>
        </div>
      </section>

      <section class="sec panel">
        <div class="sec-title row">
          <span>一阶段评价</span>
          <el-button
            v-if="workbench.submitted"
            link
            type="primary"
            :loading="reviewLoading"
            @click="rereview"
          >重新评价</el-button>
        </div>
        <div v-if="workbench.review_unavailable_reason" class="muted">
          {{ workbench.review_unavailable_reason }}
        </div>
        <div v-else-if="workbench.phase1_review" class="review">
          <p>{{ workbench.phase1_review.summary }}</p>
          <div v-if="workbench.phase1_review.strengths?.length" class="block">
            <div class="sub">亮点</div>
            <ul>
              <li v-for="(s, i) in workbench.phase1_review.strengths" :key="'s' + i">{{ s }}</li>
            </ul>
          </div>
          <div v-if="workbench.phase1_review.risks?.length" class="block">
            <div class="sub">风险</div>
            <ul>
              <li v-for="(s, i) in workbench.phase1_review.risks" :key="'r' + i">{{ s }}</li>
            </ul>
          </div>
          <p v-if="workbench.phase1_review.completion_comment" class="muted">
            {{ workbench.phase1_review.completion_comment }}
          </p>
          <div class="muted tiny">
            来源：{{ workbench.phase1_review.source === 'llm' ? 'AI' : '规则' }}
          </div>
        </div>
      </section>

      <section class="sec panel">
        <div class="sec-title">历史周期</div>
        <div class="hist">
          <button
            v-for="h in workbench.history || []"
            :key="h.period_start + h.status"
            type="button"
            class="hist-chip"
            :class="{ on: selectedHistoryId === h.report_id, miss: !h.report_id }"
            @click="pickHistory(h)"
          >
            {{ h.period_start }}
            · {{ h.status === 'submitted' ? '已交' : '草稿' }}
            <span v-if="h.item_count"> · {{ h.item_count }}项</span>
          </button>
        </div>
      </section>

      <section class="sec panel">
        <div class="sec-title">
          {{ periodType === 'day' ? '日报' : '周报' }}正文
          <span v-if="historyReport?.items?.length" class="muted tiny-count">
            · 共 {{ historyReport.items.length }} 项
          </span>
        </div>
        <div v-if="!historyReport" class="muted">本期未提交，无正文可看</div>
        <div v-else>
          <div class="muted mb">主题：{{ historyReport.theme || '无' }}</div>
          <div
            v-for="it in historyReport.items || []"
            :key="it.id"
            class="item"
            :class="{ muted: it.no_action }"
          >
            <div class="item-title">
              <strong>{{ it.task_title || '未命名任务' }}</strong>
              <el-tag v-if="itemProjectLabel(it)" size="small" effect="plain">
                {{ itemProjectLabel(it) }}
              </el-tag>
              <el-tag
                v-if="it.project_inferred && !it.project_id"
                size="small"
                type="info"
                effect="plain"
              >推断归属</el-tag>
              <el-tag
                v-for="t in it.tags || []"
                :key="t"
                size="small"
                type="success"
                effect="plain"
              >{{ t }}</el-tag>
              <el-button
                v-if="it.linked_task_id"
                link
                type="primary"
                size="small"
                @click="router.push(`/tasks/${it.linked_task_id}`)"
              >
                关联任务 #{{ it.linked_task_id }} →
              </el-button>
            </div>
            <template v-if="!it.no_action">
              <div>
                做成了：{{ it.result_text || '—' }}
                <span v-if="it.quant_value" class="muted">（{{ it.quant_value }}）</span>
              </div>
              <div>下一步：{{ it.next_goal || '—' }}（{{ it.next_due_date || '未定日' }}）</div>
              <div v-if="it.stuck_status !== 'none'" class="stuck">卡点：{{ it.stuck_detail || '—' }}</div>
              <div v-if="it.risk_level !== 'normal'" class="risk">
                风险：{{ it.risk_reason || '—' }}
              </div>
            </template>
            <div v-else>本周此项无实质动作</div>
            <div v-if="(it.attachments || []).length" class="item-attach">
              <div class="sub">附件</div>
              <ul class="attach-list">
                <li v-for="a in it.attachments" :key="a.id">
                  <el-button link type="primary" @click="downloadAttach(a)">
                    {{ a.original_filename }}
                  </el-button>
                  <span class="muted"> · {{ a.uploader_name || '' }}</span>
                </li>
              </ul>
            </div>
          </div>
          <div
            v-if="(historyReport.attachments || []).some((a: any) => a.item_id == null)"
            class="attach-block"
          >
            <div class="sub">整份周报附件（历史）</div>
            <ul class="attach-list">
              <li
                v-for="a in (historyReport.attachments || []).filter((x: any) => x.item_id == null)"
                :key="a.id"
              >
                <el-button link type="primary" @click="downloadAttach(a)">
                  {{ a.original_filename }}
                </el-button>
                <span class="muted"> · {{ a.uploader_name || '' }}</span>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <section class="sec panel">
        <div class="sec-title">进度链 / 事件链</div>
        <div v-if="!timelines.chains?.length" class="muted">暂无跨周任务链</div>
        <div v-for="c in timelines.chains || []" :key="c.chain_id" class="chain-block">
          <ReportProgressChain :title="c.title" :nodes="c.progress_nodes || []" />
          <ReportEventChain :events="c.task_events || []" />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 960px;
  margin: 0 auto;
  padding: 0 8px 40px;
}
.page-head {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  margin-bottom: 8px;
}
.page-head .wb-page-title {
  margin: 0;
}
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px;
  margin-bottom: 16px;
  box-shadow: var(--wb-shadow-soft);
}
.sec-title {
  font-weight: 600;
  margin-bottom: 10px;
}
.sec-title.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.stat {
  background: #f7f9fc;
  border-radius: 10px;
  padding: 12px;
  text-align: center;
}
.stat .n {
  font-size: 22px;
  font-weight: 700;
}
.stat .l {
  font-size: 12px;
  color: #888;
  margin-top: 4px;
}
.hist {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hist-chip {
  border: 1px solid var(--wb-border);
  background: #fff;
  border-radius: 999px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 13px;
}
.hist-chip.on {
  border-color: #5b8def;
  background: #f0f5ff;
  color: #3a6fd8;
}
.hist-chip.miss {
  opacity: 0.55;
}
.item {
  background: #f7f9fc;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 1.55;
}
.item-title {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.item-attach {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--wb-border);
}
.chain-block {
  border: 1px solid var(--wb-border);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
}
.stuck {
  color: #a65d00;
}
.risk {
  color: #c45656;
}
.muted {
  color: #999;
}
.tiny {
  font-size: 12px;
}
.tiny-count {
  font-weight: 400;
  font-size: 13px;
}
.mb {
  margin-bottom: 8px;
}
.review ul {
  margin: 4px 0 8px;
  padding-left: 18px;
}
.sub {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 4px;
}
.attach-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--wb-border);
}
.attach-list {
  margin: 0;
  padding-left: 0;
  list-style: none;
}
</style>
