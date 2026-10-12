<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { request } from '@/utils/api'

withDefaults(
  defineProps<{
    embedded?: boolean
  }>(),
  { embedded: false },
)

type StatusFilter = 'all' | 'draft' | 'submitted' | 'passed'

type PeriodRow = {
  period_start: string
  period_end?: string
  status: string
  item_count: number
}

type ReportItem = {
  id?: number
  task_title?: string
  result_text?: string
}

type MineReport = {
  id?: number
  period_start?: string
  period_end?: string
  status?: string
  items?: ReportItem[]
  phase1_review?: { summary?: string } | null
  review_unavailable_reason?: string
}

type ListRow = PeriodRow & {
  summary?: string
  displayStatus: 'draft' | 'submitted' | 'passed'
}

const FILTERS: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'submitted', label: '已交' },
  { key: 'passed', label: '已通过' },
]

const router = useRouter()
const loading = ref(false)
const error = ref('')
const filter = ref<StatusFilter>('all')
const periods = ref<PeriodRow[]>([])
const detailCache = ref<Record<string, MineReport>>({})
const rows = ref<ListRow[]>([])

function weekLabel(start: string) {
  const d = new Date(`${start}T12:00:00`)
  if (Number.isNaN(d.getTime())) return start
  const y0 = new Date(d.getFullYear(), 0, 1)
  const days = Math.floor((d.getTime() - y0.getTime()) / 86400000)
  const wn = Math.ceil((days + y0.getDay() + 1) / 7)
  return `第${wn}周周报`
}

function statusTag(s: ListRow['displayStatus']) {
  if (s === 'draft') return { cls: 'tag-draft', text: '草稿' }
  if (s === 'passed') return { cls: 'tag-passed', text: '已通过' }
  return { cls: 'tag-submitted', text: '已交' }
}

async function enrich(period: PeriodRow): Promise<ListRow> {
  const key = period.period_start
  let mine = detailCache.value[key]
  if (!mine) {
    try {
      const params: Record<string, string> = {
        period_type: 'week',
        period_start: period.period_start,
      }
      if (period.period_end) params.period_end = period.period_end
      mine = (await request<MineReport | null>('/progress-reports/mine', 'GET', params)) || {
        status: period.status,
        items: [],
      }
      detailCache.value[key] = mine
    } catch {
      mine = { status: period.status, items: [] }
    }
  }
  const submitted = (mine.status || period.status) === 'submitted'
  const passed = submitted && !!mine.phase1_review
  const displayStatus: ListRow['displayStatus'] = !submitted ? 'draft' : passed ? 'passed' : 'submitted'
  const first = (mine.items || [])[0]
  const summary =
    mine.phase1_review?.summary ||
    first?.result_text ||
    first?.task_title ||
    (period.item_count ? `${period.item_count} 条记录` : '暂无摘要')
  return { ...period, summary, displayStatus }
}

async function rebuildRows() {
  loading.value = true
  error.value = ''
  try {
    const base =
      (await request<PeriodRow[]>('/progress-reports/my-periods', 'GET', {
        period_type: 'week',
      })) || []
    periods.value = base
    const enriched = await Promise.all(base.map((p) => enrich(p)))
    rows.value = enriched
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    rows.value = []
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  if (filter.value === 'all') return rows.value
  return rows.value.filter((r) => r.displayStatus === filter.value)
})

function openRow(r: ListRow) {
  router.push({
    path: '/report',
    query: {
      period_start: r.period_start,
      ...(r.period_end ? { period_end: r.period_end } : {}),
    },
  })
}

function goWrite() {
  router.push('/report')
}

watch(filter, () => {
  /* filtered is computed */
})

onMounted(rebuildRows)
</script>

<template>
  <div class="page mine-reports" :class="{ embedded }">
    <div v-if="!embedded" class="list-head">
      <h1 class="page-title">周报管理</h1>
      <button type="button" class="head-plus" aria-label="写周报" @click="goWrite">+</button>
    </div>
    <div v-else class="toolbar">
      <button type="button" class="btn btn-mint" @click="goWrite">写周报</button>
    </div>

    <div class="status-pills" role="tablist">
      <button
        v-for="f in FILTERS"
        :key="f.key"
        type="button"
        class="status-pill"
        :class="{ on: filter === f.key }"
        @click="filter = f.key"
      >
        {{ f.label }}
      </button>
    </div>

    <div v-if="error" class="deny">{{ error }}</div>
    <div v-else-if="loading" class="muted">加载中…</div>
    <template v-else>
      <div v-if="!filtered.length" class="card empty-sm">
        暂无{{ FILTERS.find((x) => x.key === filter)?.label || '' }}周报
      </div>
      <button
        v-for="r in filtered"
        :key="r.period_start + r.status"
        type="button"
        class="card list-row"
        @click="openRow(r)"
      >
        <div class="row-top">
          <strong>{{ weekLabel(r.period_start) }}</strong>
          <span class="tag" :class="statusTag(r.displayStatus).cls">
            {{ statusTag(r.displayStatus).text }}
          </span>
        </div>
        <p class="summary">{{ r.summary }}</p>
        <div class="muted">
          {{ r.period_start
          }}{{ r.period_end && r.period_end !== r.period_start ? ` ~ ${r.period_end}` : '' }}
          · {{ r.item_count }} 条
        </div>
      </button>
    </template>
  </div>
</template>

<style scoped>
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.list-head .page-title { margin: 0; }
.head-plus {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: var(--wb-surface);
  font-size: 22px;
  line-height: 1;
  color: var(--wb-text);
}
.toolbar {
  margin: 0 0 12px;
}
.toolbar .btn {
  margin: 0;
  width: 100%;
}
.list-row {
  width: 100%;
  text-align: left;
  border: 1px solid var(--wb-border);
  background: var(--wb-card);
  cursor: pointer;
}
.row-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  gap: 8px;
}
.summary {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--wb-label);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.empty-sm {
  text-align: center;
  color: var(--wb-muted);
  padding: 24px 8px;
}
</style>
