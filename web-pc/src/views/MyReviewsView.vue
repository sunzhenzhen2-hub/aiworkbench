<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

interface ReportReviewItem {
  report_id: number
  period_type: string
  period_start: string
  period_end: string | null
  status: string
  review: {
    summary: string
    strengths: string[]
    risks: string[]
    completion_comment?: string
    source?: string
  } | null
}

interface TaskReviewItem {
  task_id: number
  task_title: string
  project_id: number | null
  project_name: string | null
  my_role: string | null
  review: {
    summary: string
    strengths: string[]
    risks: string[]
    suggestions: string[]
    source?: string
  }
}

interface GuidanceItem {
  id: number
  content: string
  action_items?: string[]
  period_start?: string | null
  source?: string
  created_at?: string | null
  created_by?: { name?: string } | null
  read_at?: string | null
  improved_at?: string | null
  improve_note?: string
}

const router = useRouter()
const loading = ref(false)
const reportReviews = ref<ReportReviewItem[]>([])
const taskReviews = ref<TaskReviewItem[]>([])
const guidanceNotes = ref<GuidanceItem[]>([])
const actingId = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const [reviewsRes, guidanceRes] = await Promise.all([
      api.get('/personal/reviews', { params: { weeks: 8 } }),
      api.get('/personal/guidance', { params: { limit: 20 } }),
    ])
    reportReviews.value = reviewsRes.data.report_reviews || []
    taskReviews.value = reviewsRes.data.task_reviews || []
    guidanceNotes.value = guidanceRes.data?.items || []
  } finally {
    loading.value = false
  }
}

async function markRead(g: GuidanceItem) {
  actingId.value = g.id
  try {
    const { data } = await api.post(`/personal/guidance/${g.id}/read`)
    if (data?.note) {
      Object.assign(g, data.note)
    } else {
      g.read_at = new Date().toISOString()
    }
  } finally {
    actingId.value = null
  }
}

async function markImproved(g: GuidanceItem) {
  actingId.value = g.id
  try {
    const { data } = await api.post(`/personal/guidance/${g.id}/improved`, {
      improve_note: '',
    })
    if (data?.note) {
      Object.assign(g, data.note)
    } else {
      g.improved_at = new Date().toISOString()
      g.read_at = g.read_at || g.improved_at
    }
  } finally {
    actingId.value = null
  }
}

function roleLabel(role: string | null) {
  if (role === 'owner') return '负责人'
  if (role === 'assistant') return '辅助'
  return ''
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <h1 class="wb-page-title">看评价</h1>
    <p class="wb-page-desc">
      查看自己的周报评价、任务评价与收到的指导。暂无内容时，请先提交周报或完成任务后再来看。
    </p>

    <section class="panel section-block">
      <div class="panel-head">收到的指导</div>
      <div v-if="!guidanceNotes.length" class="empty">暂无指导备注。</div>
      <div v-for="g in guidanceNotes" :key="g.id" class="review-card">
        <div class="review-meta">
          <template v-if="g.period_start">周期 {{ g.period_start }} · </template>
          {{ g.created_by?.name || '管理员' }}
          <template v-if="g.read_at"> · 已读</template>
          <template v-if="g.improved_at"> · 已改进</template>
        </div>
        <p class="summary">{{ g.content }}</p>
        <ul v-if="g.action_items?.length">
          <li v-for="(s, i) in g.action_items" :key="'ga' + i">{{ s }}</li>
        </ul>
        <div class="guidance-actions">
          <el-button
            v-if="!g.read_at"
            size="small"
            :loading="actingId === g.id"
            @click="markRead(g)"
          >标记已读</el-button>
          <el-button
            v-if="!g.improved_at"
            size="small"
            type="primary"
            :loading="actingId === g.id"
            @click="markImproved(g)"
          >我已改进</el-button>
          <span v-else class="done-hint">已反馈改进</span>
        </div>
      </div>
    </section>

    <section class="panel section-block">
      <div class="panel-head row-between">
        <span>我的周报评价</span>
        <el-button link type="primary" @click="router.push({ path: '/reports', query: { tab: 'write' } })">去写周报</el-button>
      </div>
      <div v-if="!reportReviews.length" class="empty">最近暂无已提交周报，提交后可在此查看评价。</div>
      <div
        v-for="item in reportReviews"
        :key="item.report_id"
        class="review-card"
      >
        <div class="review-meta">
          周报周期 {{ item.period_start }}
          <template v-if="item.period_end && item.period_end !== item.period_start">
            ~ {{ item.period_end }}
          </template>
        </div>
        <template v-if="item.review">
          <p class="summary">{{ item.review.summary }}</p>
          <ul v-if="item.review.strengths?.length">
            <li v-for="(s, i) in item.review.strengths" :key="'rs' + i">优点：{{ s }}</li>
          </ul>
          <ul v-if="item.review.risks?.length">
            <li v-for="(s, i) in item.review.risks" :key="'rr' + i">关注：{{ s }}</li>
          </ul>
          <p v-if="item.review.completion_comment" class="hint">{{ item.review.completion_comment }}</p>
          <p class="hint">来源：{{ item.review.source === 'llm' ? 'AI' : '规则' }}</p>
        </template>
        <p v-else class="hint">本期周报已提交，评价尚未生成或暂不可用。</p>
      </div>
    </section>

    <section class="panel section-block">
      <div class="panel-head row-between">
        <span>我的任务评价</span>
        <el-button link type="primary" @click="router.push('/personal')">去我的待办</el-button>
      </div>
      <div v-if="!taskReviews.length" class="empty">暂无任务评价。有评价后会显示在这里。</div>
      <div
        v-for="item in taskReviews"
        :key="item.task_id"
        class="review-card clickable"
        @click="router.push('/personal')"
      >
        <div class="review-meta">
          {{ item.task_title }}
          <span v-if="item.project_name"> · {{ item.project_name }}</span>
          <span v-if="roleLabel(item.my_role)"> · {{ roleLabel(item.my_role) }}</span>
        </div>
        <p class="summary">{{ item.review.summary }}</p>
        <ul v-if="item.review.strengths?.length">
          <li v-for="(s, i) in item.review.strengths" :key="'ts' + i">优点：{{ s }}</li>
        </ul>
        <ul v-if="item.review.risks?.length">
          <li v-for="(s, i) in item.review.risks" :key="'tr' + i">关注：{{ s }}</li>
        </ul>
        <ul v-if="item.review.suggestions?.length">
          <li v-for="(s, i) in item.review.suggestions" :key="'tg' + i">建议：{{ s }}</li>
        </ul>
        <p class="hint">来源：{{ item.review.source === 'llm' ? 'AI' : '规则' }} · 点卡片可去待办查看任务</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.section-block {
  margin-bottom: 16px;
}
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px 20px;
  box-shadow: var(--wb-shadow-soft);
}
.panel-head {
  font-size: 14px;
  font-weight: 600;
  color: var(--wb-text-primary);
  margin-bottom: 12px;
}
.row-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.empty {
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
}
.review-card {
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 12px 14px;
  margin-bottom: 10px;
  background: #fafafa;
}
.review-card.clickable {
  cursor: pointer;
}
.review-card.clickable:hover {
  border-color: #9ecfb8;
  background: var(--wb-bg-active, #eef8f2);
}
.review-meta {
  font-size: 13px;
  font-weight: 600;
  color: var(--wb-text-primary);
  margin-bottom: 6px;
}
.summary {
  margin: 0 0 6px;
  font-size: 14px;
  color: var(--wb-text-primary);
}
.guidance-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.done-hint {
  font-size: 12px;
  color: #67a07a;
}
ul {
  margin: 4px 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
}
.hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--wb-text-muted, #999);
}
</style>
