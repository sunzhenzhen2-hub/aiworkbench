<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { request } from '@/utils/api'

type ReportReviewItem = {
  report_id: number
  period_start: string
  period_end: string | null
  review: {
    summary: string
    strengths?: string[]
    risks?: string[]
    completion_comment?: string
    source?: string
  } | null
}

type TaskReviewItem = {
  task_id: number
  task_title: string
  project_name: string | null
  my_role: string | null
  review: {
    summary: string
    strengths?: string[]
    risks?: string[]
    suggestions?: string[]
    source?: string
  }
}

const router = useRouter()
const error = ref('')
const loading = ref(false)
const reportReviews = ref<ReportReviewItem[]>([])
const taskReviews = ref<TaskReviewItem[]>([])

function roleLabel(role: string | null) {
  if (role === 'owner') return '负责人'
  if (role === 'assistant') return '辅助'
  return ''
}

async function load() {
  loading.value = true
  try {
    const data = await request<{
      report_reviews?: ReportReviewItem[]
      task_reviews?: TaskReviewItem[]
    }>('/personal/reviews?weeks=8')
    reportReviews.value = data.report_reviews || []
    taskReviews.value = data.task_reviews || []
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page no-tab">
    <button class="back" type="button" @click="router.back()">← 返回</button>
    <h1 class="page-title">看评价</h1>
    <p class="muted" style="margin-top:-8px;margin-bottom:12px">
      查看自己的周报评价与任务评价
    </p>
    <div v-if="error" class="deny">{{ error }}</div>
    <div v-else-if="loading" class="muted">加载中…</div>
    <template v-else>
      <h2 class="sec">我的周报评价</h2>
      <button class="link" type="button" style="margin-bottom:8px" @click="router.push('/report')">
        去写周报
      </button>
      <div v-if="!reportReviews.length" class="card empty-sm">
        最近暂无已提交周报，提交后可在此查看评价。
      </div>
      <div v-for="item in reportReviews" :key="item.report_id" class="card">
        <div class="meta">
          周期 {{ item.period_start }}
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
          <p v-if="item.review.completion_comment" class="muted">{{ item.review.completion_comment }}</p>
          <p class="muted">来源：{{ item.review.source === 'llm' ? 'AI' : '规则' }}</p>
        </template>
        <p v-else class="muted">本期已提交，评价尚未生成或暂不可用。</p>
      </div>

      <h2 class="sec" style="margin-top:18px">我的任务评价</h2>
      <button class="link" type="button" style="margin-bottom:8px" @click="router.push('/todo')">
        去我的待办
      </button>
      <div v-if="!taskReviews.length" class="card empty-sm">暂无任务评价</div>
      <div
        v-for="item in taskReviews"
        :key="item.task_id"
        class="card tap"
        @click="router.push(`/tasks/${item.task_id}`)"
      >
        <div class="meta">
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
        <p class="muted">来源：{{ item.review.source === 'llm' ? 'AI' : '规则' }} · 点击查看任务</p>
      </div>
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
.sec {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 6px;
}
.meta {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.summary {
  margin: 0 0 6px;
  font-size: 14px;
}
ul {
  margin: 4px 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
}
.link {
  border: none;
  background: none;
  color: var(--wb-mint-text);
  font-size: 13px;
  padding: 0;
  cursor: pointer;
}
.empty-sm {
  padding: 16px;
  color: var(--wb-text-secondary, #666);
  font-size: 13px;
}
</style>
