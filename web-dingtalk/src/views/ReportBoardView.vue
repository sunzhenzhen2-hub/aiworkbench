<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'

withDefaults(
  defineProps<{
    /** 嵌在周报壳页时隐藏页头，由壳页统一展示 */
    embedded?: boolean
  }>(),
  { embedded: false },
)

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const error = ref('')
const loading = ref(false)
const showMatrix = ref(false)
const board = ref<{
  period_start?: string
  period_end?: string
  auto_switched?: boolean
  available_periods?: Array<{ period_start: string; period_end?: string; submitted_count: number }>
  people?: Array<{
    user: { id: number; name: string }
    submitted: boolean
    escalate_count?: number
    blocker_count?: number
    submit_rate?: number
    submitted_weeks?: number
    expected_weeks?: number
    report?: unknown | null
  }>
}>({})
const matrix = ref<{
  weeks?: number
  people?: Array<{ id: number; name: string }>
  periods?: Array<{
    period_start: string
    cells: Array<{ user_id: number; submitted: boolean }>
  }>
} | null>(null)

const periodStart = ref('')
const submittedPeople = computed(() => (board.value.people || []).filter((p) => p.submitted))
const pendingPeople = computed(() => (board.value.people || []).filter((p) => !p.submitted))

function rateText(p: { submit_rate?: number; submitted_weeks?: number; expected_weeks?: number }) {
  const rate = Math.round((p.submit_rate || 0) * 100)
  const sw = p.submitted_weeks ?? 0
  const ew = p.expected_weeks ?? 0
  return ew ? `${rate}%（${sw}/${ew}）` : `${rate}%`
}

async function load(ps?: string) {
  loading.value = true
  error.value = ''
  try {
    const params: Record<string, string> = { period_type: 'week', mode: 'summary' }
    if (ps) params.period_start = ps
    const data = await request<typeof board.value>('/progress-reports/board', 'GET', params)
    board.value = data || {}
    periodStart.value = data.period_start || ''
    const mx = await request<typeof matrix.value>('/progress-reports/submission-matrix', 'GET', {
      weeks: '12',
      anchor: periodStart.value || '',
    }).catch(() => null)
    matrix.value = mx
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载周报工作台失败'
  } finally {
    loading.value = false
  }
}

function pickPeriod(p: { period_start: string }) {
  load(p.period_start)
}

function openPerson(userId: number) {
  router.push({
    name: 'report-person',
    params: { userId: String(userId) },
    query: { period: periodStart.value || undefined },
  })
}

function cellOk(periodStartKey: string, userId: number) {
  const period = (matrix.value?.periods || []).find((p) => p.period_start === periodStartKey)
  return !!period?.cells?.find((c) => c.user_id === userId && c.submitted)
}

onMounted(() => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  if (!auth.user.permissions?.includes('report.view.all')) {
    router.replace({ name: 'reports', query: { tab: 'mine' } })
    return
  }
  const ps = typeof route.query.period === 'string' ? route.query.period : undefined
  load(ps)
})
</script>

<template>
  <div class="page" :class="{ embedded }">
    <template v-if="!embedded">
      <h1 class="page-title">周报工作台</h1>
      <p class="page-desc">点人员卡片查看本期/历史周报与评价；主页不展开全文。</p>
    </template>

    <div v-if="error" class="deny">{{ error }}</div>
    <div v-else-if="loading" class="muted">加载中…</div>
    <template v-else>
      <div v-if="board.auto_switched" class="tip">
        本周暂无提交，已自动切换到
        {{ board.period_start }} ~ {{ board.period_end || board.period_start }}
      </div>
      <div v-else class="tip">
        当前周期：{{ board.period_start }} ~ {{ board.period_end || board.period_start }}
        · 已交 {{ submittedPeople.length }} · 未交 {{ pendingPeople.length }}
      </div>

      <div v-if="board.available_periods?.length" class="card">
        <div class="sec-title">切换周期（自 9 月 1 日起）</div>
        <div class="chips">
          <button
            v-for="p in board.available_periods"
            :key="p.period_start"
            type="button"
            class="chip"
            :class="{ on: p.period_start === periodStart }"
            @click="pickPeriod(p)"
          >
            {{ p.period_start }}{{ p.period_end && p.period_end !== p.period_start ? ' ~ ' + p.period_end.slice(5) : '' }}
            （{{ p.submitted_count }}人）
          </button>
        </div>
      </div>

      <div class="card strip">
        <div class="sec-title">本期提交</div>
        <div class="strip-block">
          <div class="lab">已交</div>
          <div class="names">
            <button
              v-for="p in submittedPeople"
              :key="'ok-' + p.user.id"
              type="button"
              class="name"
              @click="openPerson(p.user.id)"
            >{{ p.user.name }}</button>
            <span v-if="!submittedPeople.length" class="muted">暂无</span>
          </div>
        </div>
        <div class="strip-block">
          <div class="lab warn">未交（催办）</div>
          <div class="names">
            <button
              v-for="p in pendingPeople"
              :key="'no-' + p.user.id"
              type="button"
              class="name warn"
              @click="openPerson(p.user.id)"
            >{{ p.user.name }}</button>
            <span v-if="!pendingPeople.length" class="muted">全部已交</span>
          </div>
        </div>
      </div>

      <div class="card actions-row">
        <button type="button" class="btn btn-mint" @click="showMatrix = !showMatrix">
          {{ showMatrix ? '收起矩阵' : '历史提交矩阵' }}
        </button>
      </div>

      <div v-if="showMatrix && matrix?.periods?.length" class="card matrix">
        <div class="sec-title">近 {{ matrix.weeks }} 周</div>
        <div class="matrix-scroll">
          <table>
            <thead>
              <tr>
                <th>姓名</th>
                <th v-for="p in matrix.periods" :key="p.period_start">{{ p.period_start.slice(5) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in matrix.people || []" :key="u.id">
                <td @click="openPerson(u.id)">{{ u.name }}</td>
                <td v-for="p in matrix.periods" :key="u.id + p.period_start">
                  {{ cellOk(p.period_start, u.id) ? '✓' : '·' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <button
        v-for="p in board.people || []"
        :key="p.user.id"
        type="button"
        class="card person-card"
        :class="{ pending: !p.submitted }"
        @click="openPerson(p.user.id)"
      >
        <div class="row">
          <strong>{{ p.user.name }}</strong>
          <span class="tag" :class="p.submitted ? 'ok' : 'no'">{{ p.submitted ? '已交' : '未交' }}</span>
        </div>
        <div class="muted">提交率 {{ rateText(p) }}</div>
        <div class="flags">
          <span v-if="p.escalate_count" class="danger">升级 {{ p.escalate_count }}</span>
          <span v-if="p.blocker_count" class="warn">卡点 {{ p.blocker_count }}</span>
        </div>
      </button>
    </template>
  </div>
</template>

<style scoped>
.sec-title { font-weight: 600; margin-bottom: 8px; font-size: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  border: 1px solid var(--wb-border);
  background: var(--wb-surface);
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--wb-text);
}
.chip.on {
  background: var(--wb-mint);
  color: #fff;
  border-color: var(--wb-mint);
}
.strip-block { margin-bottom: 10px; }
.lab { font-size: 12px; color: var(--wb-muted); margin-bottom: 6px; }
.lab.warn { color: var(--wb-warn); }
.names { display: flex; flex-wrap: wrap; gap: 8px; }
.name {
  border: 1px solid var(--wb-border);
  background: var(--wb-mint-bg);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 13px;
  color: var(--wb-mint-text);
}
.name.warn {
  background: var(--wb-warn-bg);
  color: var(--wb-warn-text);
  border-color: #f0e0c0;
}
.actions-row { display: flex; gap: 8px; }
.person-card {
  width: 100%;
  text-align: left;
  border-left: 3px solid var(--wb-mint);
}
.person-card.pending { border-left-color: #c0c4cc; }
.row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--wb-mint-bg);
  color: var(--wb-mint-text);
}
.tag.ok { background: var(--wb-mint-bg); color: var(--wb-mint-text); }
.tag.no { background: var(--wb-orange-bg); color: var(--wb-orange); }
.flags { display: flex; gap: 10px; margin-top: 6px; font-size: 12px; }
.danger { color: var(--wb-danger); }
.warn { color: var(--wb-warn); }
.matrix-scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 11px; }
th, td { border: 1px solid var(--wb-border); padding: 4px 6px; text-align: center; white-space: nowrap; }
</style>
