<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import ReportPeopleBoard from '../components/report-board/ReportPeopleBoard.vue'
import ReportProjectBoard from '../components/report-board/ReportProjectBoard.vue'

/** 嵌在周报管理「看」Tab 内时不显示页面标题 */
const props = withDefaults(
  defineProps<{
    embedded?: boolean
  }>(),
  { embedded: false },
)

const router = useRouter()
const route = useRoute()
const periodType = ref('week')
const periodStart = ref('')
const activeTab = ref('people')
const board = ref<any>({
  people: [],
  projects: [],
  unassigned_items: [],
  available_periods: [],
  auto_switched: false,
})
const matrix = ref<any>(null)
const loading = ref(false)
const showMatrix = ref(false)
const tagFilter = ref('')
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

const submittedPeople = computed(() => (board.value.people || []).filter((p: any) => p.submitted))
const pendingPeople = computed(() => (board.value.people || []).filter((p: any) => !p.submitted))

async function load() {
  loading.value = true
  try {
    const reqs: Promise<any>[] = [
      api.get('/progress-reports/board', {
        params: {
          period_type: periodType.value,
          period_start: periodStart.value,
          mode: 'summary',
        },
      }),
      api.get('/progress-reports/work-tags').catch(() => ({ data: null })),
    ]
    // 日报不考核：不拉周提交矩阵
    if (periodType.value === 'week') {
      reqs.push(
        api
          .get('/progress-reports/submission-matrix', {
            params: { weeks: 12, anchor: periodStart.value || undefined },
          })
          .catch(() => ({ data: null })),
      )
    }
    const [boardRes, tagsRes, matrixRes] = await Promise.all(reqs)
    board.value = boardRes.data
    matrix.value = periodType.value === 'week' ? matrixRes?.data ?? null : null
    if (periodType.value === 'day') {
      showMatrix.value = false
    }
    if (Array.isArray(tagsRes.data) && tagsRes.data.length) {
      workTagOptions.value = tagsRes.data
    }
    if (boardRes.data.period_start) periodStart.value = boardRes.data.period_start
    if (boardRes.data.auto_switched) {
      ElMessage.info(
        `本周暂无提交，已切换到有数据的周期：${boardRes.data.period_start} ~ ${boardRes.data.period_end || boardRes.data.period_start}`,
      )
    }
  } finally {
    loading.value = false
  }
}

function pickPeriod(p: any) {
  periodStart.value = p.period_start
  load()
}

function openPerson(userId: number) {
  router.push({
    path: `/reports/person/${userId}`,
    query: {
      period: periodStart.value || undefined,
      period_type: periodType.value,
    },
  })
}

async function ack(blockerId: number) {
  await api.post(`/progress-reports/blockers/${blockerId}/ack`)
  ElMessage.success('已确认已知悉')
  load()
}

function openReport(id: number | null) {
  if (!id) return
  router.push({ path: '/reports', query: { tab: 'write', view: String(id) } })
}

function cellSubmitted(periodStartKey: string, userId: number) {
  const period = (matrix.value?.periods || []).find((p: any) => p.period_start === periodStartKey)
  const cell = period?.cells?.find((c: any) => c.user_id === userId)
  return !!cell?.submitted
}

onMounted(() => {
  const q = route.query.period
  const qType = route.query.period_type
  if (qType === 'day' || qType === 'week') periodType.value = qType
  periodStart.value = typeof q === 'string' && q ? q : mondayStr()
  const uid = route.query.user
  if (typeof uid === 'string' && uid) {
    // 旧链接 ?user= 改为整页
    router.replace({
      path: `/reports/person/${uid}`,
      query: {
        period: periodStart.value || undefined,
        period_type: periodType.value,
      },
    })
    return
  }
  load()
})
</script>

<template>
  <div v-loading="loading">
    <template v-if="!embedded">
      <h1 class="wb-page-title">周报工作台</h1>
      <p class="wb-page-desc">
        {{
          periodType === 'day'
            ? '日报仅作当日工作记录，不考核催办；点开某人可查看当日记录。考核与催办请切回「周报」。'
            : '主页只看人员卡片与提交情况；点开某人后查看本期/历史周报、完成度与一阶段评价。'
        }}
      </p>
    </template>

    <div class="toolbar panel">
      <el-radio-group v-model="periodType" size="small" @change="load">
        <el-radio-button value="week">周报</el-radio-button>
        <el-radio-button value="day">日报</el-radio-button>
      </el-radio-group>
      <el-date-picker v-model="periodStart" type="date" value-format="YYYY-MM-DD" @change="load" />
      <span v-if="board.period_end" class="muted">周期：{{ board.period_start }} ~ {{ board.period_end }}</span>
      <el-button @click="load">刷新</el-button>
      <el-button v-if="periodType === 'week'" size="small" @click="showMatrix = !showMatrix">
        {{ showMatrix ? '收起历史矩阵' : '历史提交矩阵' }}
      </el-button>
      <el-select
        v-if="activeTab === 'projects'"
        v-model="tagFilter"
        filterable
        clearable
        placeholder="输入关键字筛选标签"
        style="width: 180px"
        size="small"
      >
        <el-option v-for="t in workTagOptions" :key="t" :label="t" :value="t" />
      </el-select>
    </div>

    <div v-if="board.available_periods?.length" class="panel periods">
      <div class="panel-head">切换周期（自 9 月 1 日起；含无提交周）</div>
      <div class="period-chips">
        <el-tag
          v-for="p in board.available_periods"
          :key="p.period_start"
          class="chip"
          :effect="p.period_start === board.period_start ? 'dark' : 'plain'"
          :type="p.period_start === board.period_start ? 'primary' : 'info'"
          style="cursor:pointer;margin:0 6px 6px 0"
          @click="pickPeriod(p)"
        >
          {{ p.period_start }}{{ p.period_end && p.period_end !== p.period_start ? ' ~ ' + p.period_end : '' }}
          （{{ p.submitted_count }}人）
        </el-tag>
      </div>
    </div>

    <div v-if="periodType === 'week' && showMatrix && matrix?.periods?.length" class="panel matrix">
      <div class="panel-head">
        近 {{ matrix.weeks }} 周提交矩阵
        <span class="muted head-stat">已交 {{ submittedPeople.length }} / 未交 {{ pendingPeople.length }}（本期）</span>
      </div>
      <div class="matrix-scroll">
        <table>
          <thead>
            <tr>
              <th>姓名</th>
              <th v-for="p in matrix.periods" :key="p.period_start">
                {{ String(p.period_start).slice(5) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in matrix.people" :key="u.id">
              <td>
                <button type="button" class="linkish" @click="openPerson(u.id)">{{ u.name }}</button>
              </td>
              <td v-for="p in matrix.periods" :key="u.id + '-' + p.period_start">
                <span :class="cellSubmitted(p.period_start, u.id) ? 'ok' : 'no'">
                  {{ cellSubmitted(p.period_start, u.id) ? '✓' : '·' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="人员" name="people">
        <ReportPeopleBoard
          :people="board.people || []"
          :period-type="(periodType as 'week' | 'day')"
          @open-person="openPerson"
        />
      </el-tab-pane>
      <el-tab-pane label="项目" name="projects">
        <ReportProjectBoard
          :projects="board.projects || []"
          :unassigned-items="board.unassigned_items || []"
          :tag-filter="tagFilter"
          @ack="ack"
          @open-report="openReport"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px;
  margin-bottom: 16px;
  box-shadow: var(--wb-shadow-soft);
}
.panel-head {
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.head-stat { font-weight: 400; font-size: 13px; }
.toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.period-chips { display: flex; flex-wrap: wrap; }
.matrix-scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 12px; min-width: 100%; }
th, td { border: 1px solid var(--wb-border); padding: 6px 8px; text-align: center; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; position: sticky; left: 0; background: #fff; }
.ok { color: var(--wb-accent); font-weight: 700; }
.no { color: #ccc; }
.linkish {
  border: none;
  background: transparent;
  color: #3a6fd8;
  cursor: pointer;
  padding: 0;
}
.muted { color: #999; }
</style>
