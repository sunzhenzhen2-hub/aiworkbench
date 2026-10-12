<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'

interface PersonRow {
  user_id: number
  name: string
  username: string
  report_submitted_weeks: number
  report_expected_weeks: number
  report_submit_rate: number
  overdue_tasks: number
  open_blockers: number
}

interface ProjectRow {
  id: number
  name: string
  level: string
  status: string
  open_blockers: number
  overdue_tasks: number
  delay_tendency: string
  party_counts: Record<string, number>
  is_key: boolean
}

const router = useRouter()
const loading = ref(false)
const people = ref<PersonRow[]>([])
const projects = ref<ProjectRow[]>([])
const partyDist = ref<Record<string, number>>({})
const externalRank = ref<{ name: string; open_blockers: number }[]>([])
const weeks = ref<string[]>([])

const partyLabel: Record<string, string> = {
  third_party: '第三方',
  customer: '客户',
  internal: '内部',
  approval: '审批',
  other: '其他',
}
const delayLabel: Record<string, string> = {
  high: '偏高',
  medium: '留意',
  low: '平稳',
}

function pct(rate: number) {
  return `${Math.round((rate || 0) * 100)}%`
}

async function load() {
  loading.value = true
  try {
    const [ppl, proj] = await Promise.all([
      api.get('/stats/people'),
      api.get('/stats/projects'),
    ])
    weeks.value = ppl.data?.weeks || []
    people.value = ppl.data?.people || []
    projects.value = proj.data?.projects || []
    partyDist.value = proj.data?.party_distribution || {}
    externalRank.value = proj.data?.external_rank || []
  } finally {
    loading.value = false
  }
}

defineExpose({ load })

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="stats-board">
    <p class="board-desc">
      轻量看人与项目：近四周周报提交率、逾期与卡点；以及卡点责任方分布（不做正式打分）。
    </p>

    <section class="panel">
      <div class="panel-head">卡点责任方分布</div>
      <div class="dist-row">
        <div v-for="key in ['third_party', 'customer', 'internal', 'approval']" :key="key" class="dist-chip">
          <div class="dist-num">{{ partyDist[key] || 0 }}</div>
          <div class="dist-label">{{ partyLabel[key] }}</div>
        </div>
      </div>
      <div v-if="externalRank.length" class="rank-block">
        <div class="sub-head">第三方 / 客户拖期排行</div>
        <div v-for="(r, i) in externalRank" :key="i" class="rank-row">
          <span>{{ r.name }}</span>
          <span>{{ r.open_blockers }} 个未关卡点</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        人员工作视图
        <span v-if="weeks.length" class="hint">近 4 周（{{ weeks[weeks.length - 1] }}～{{ weeks[0] }}）</span>
      </div>
      <el-table :data="people" size="small" empty-text="暂无人员数据">
        <el-table-column label="姓名" min-width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/reports/person/${row.user_id}`)">
              {{ row.name }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="周报提交" width="140">
          <template #default="{ row }">
            {{ row.report_submitted_weeks }}/{{ row.report_expected_weeks }}（{{ pct(row.report_submit_rate) }}）
          </template>
        </el-table-column>
        <el-table-column label="逾期任务" prop="overdue_tasks" width="90" />
        <el-table-column label="未关卡点" prop="open_blockers" width="90" />
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-head">项目延期与卡点</div>
      <el-table
        :data="projects"
        size="small"
        empty-text="暂无项目数据"
        @row-click="(row: ProjectRow) => router.push(`/projects/${row.id}`)"
      >
        <el-table-column label="项目" min-width="160">
          <template #default="{ row }">
            <span :class="{ 'is-key': row.is_key }">{{ row.name }}</span>
            <el-tag v-if="row.is_key" size="small" type="danger" style="margin-left: 6px">重点</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="延期倾向" width="100">
          <template #default="{ row }">{{ delayLabel[row.delay_tendency] || row.delay_tendency }}</template>
        </el-table-column>
        <el-table-column label="逾期任务" prop="overdue_tasks" width="90" />
        <el-table-column label="未关卡点" prop="open_blockers" width="90" />
        <el-table-column label="卡点分布" min-width="180">
          <template #default="{ row }">
            三{{ row.party_counts?.third_party || 0 }} /
            客{{ row.party_counts?.customer || 0 }} /
            内{{ row.party_counts?.internal || 0 }} /
            审{{ row.party_counts?.approval || 0 }}
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.board-desc {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--wb-text-secondary, #666);
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
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  font-weight: 400;
  font-size: 12px;
  color: #888;
}
.dist-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.dist-chip {
  background: #fff;
  border: 1px solid #e8ebe9;
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}
.dist-num {
  font-size: 22px;
  font-weight: 700;
}
.dist-label {
  font-size: 12px;
  color: #666;
}
.rank-block {
  margin-top: 14px;
}
.sub-head {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.rank-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed #e8ebe9;
  font-size: 13px;
}
.is-key {
  font-weight: 600;
  color: #b45309;
}
@media (max-width: 640px) {
  .dist-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
