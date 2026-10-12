<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { TASK_PRIORITY_MAP } from '../constants/projectMeta'
import { useUserStore } from '../stores/user'

const props = defineProps<{
  projectId?: number | null
  /** personal：个人待办；department：部门待办汇总；project：项目全部任务；related：任务管理 */
  mode: 'personal' | 'project' | 'department' | 'related'
  /** @deprecated 状态筛已迁入 related 工具栏，保留 prop 以免外部误传报错 */
  status?: 'open' | 'completed' | 'all'
}>()

const router = useRouter()
const userStore = useUserStore()
const tasks = ref<any[]>([])
const loading = ref(false)

/** related 模式筛选：默认全部；下拉仅未完成/已完成，清空=全部 */
const filterStatus = ref<'open' | 'completed' | null>(null)
const search = ref('')
const filterAssigneeId = ref<number | null>(null)
const filterPriority = ref<string | null>(null)
/** 负责人下拉缓存：已选负责人时避免筛空后选项消失 */
const assigneeOptions = ref<{ id: number; name: string }[]>([])

/** related 分页 */
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

const statusMap: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
const priorityMap = TASK_PRIORITY_MAP

const canSelf = computed(() => userStore.hasPermission('task.create.self'))

function mergeAssigneeOptions(rows: any[], replace: boolean) {
  const map = new Map<number, string>()
  if (!replace) {
    for (const o of assigneeOptions.value) {
      map.set(o.id, o.name)
    }
  }
  for (const row of rows) {
    const a = row?.assignee
    if (a?.id != null && a.name) {
      map.set(a.id, a.name)
    }
  }
  assigneeOptions.value = [...map.entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh'))
}

async function load() {
  loading.value = true
  try {
    if (props.mode === 'related') {
      const params: Record<string, string | number> = {
        status: filterStatus.value || 'all',
        page: page.value,
        page_size: pageSize.value,
      }
      const keyword = search.value.trim()
      if (keyword) params.search = keyword
      if (filterAssigneeId.value != null) params.assignee_id = filterAssigneeId.value
      if (filterPriority.value) params.priority = filterPriority.value
      const { data } = await api.get('/tasks/related', { params })
      const items = data?.items ?? []
      tasks.value = items
      total.value = data?.total ?? 0
      if (data?.page) page.value = data.page
      if (data?.page_size) pageSize.value = data.page_size
      // 未选负责人：用当前页结果重建选项；已选：合并进缓存，避免下拉被筛空
      mergeAssigneeOptions(items, filterAssigneeId.value == null)
    } else if (props.mode === 'project' && props.projectId) {
      const { data } = await api.get(`/projects/${props.projectId}/tasks`)
      tasks.value = data
    } else {
      const { data } = await api.get('/personal/workspace')
      if (props.mode === 'department') {
        tasks.value = data.department_tasks || []
      } else {
        tasks.value = data.my_tasks || []
      }
    }
  } finally {
    loading.value = false
  }
}

function queryRelated() {
  page.value = 1
  load()
}

function onPageChange() {
  load()
}

function onPageSizeChange() {
  page.value = 1
  load()
}

watch(
  () => props.projectId,
  () => {
    if (props.mode === 'project') load()
  },
)

onMounted(load)

function openDetail(row: any) {
  router.push({ path: `/tasks/${row.id}` })
}

function priorityTagClass(p: string) {
  if (p === 'p0') return 'wb-tag--red'
  if (p === 'p1') return 'wb-tag--yellow'
  if (p === 'p2') return 'wb-tag--yellow'
  return 'wb-tag--green'
}

function acceptLabel(row: any) {
  if (row.assignment_type !== 'delegated') return '—'
  return (row.acceptance_status || 'accepted') === 'pending' ? '待接收' : '已接收'
}

function roleLabel(row: any) {
  if (row.my_role === 'owner') return '负责人'
  if (row.my_role === 'assistant') return '辅助'
  if (userStore.user?.id && row.created_by?.id === userStore.user.id) return '创建人'
  return '—'
}
</script>

<template>
  <div v-loading="loading">
    <div v-if="mode === 'related'" class="toolbar">
      <el-select
        v-model="filterStatus"
        placeholder="状态"
        clearable
        class="tool-item"
        @change="queryRelated"
      >
        <el-option label="未完成" value="open" />
        <el-option label="已完成" value="completed" />
      </el-select>
      <el-select
        v-model="filterAssigneeId"
        placeholder="负责人"
        clearable
        filterable
        class="tool-item"
        @change="queryRelated"
      >
        <el-option
          v-for="p in assigneeOptions"
          :key="p.id"
          :label="p.name"
          :value="p.id"
        />
      </el-select>
      <el-select
        v-model="filterPriority"
        placeholder="级别"
        clearable
        class="tool-item"
        @change="queryRelated"
      >
        <el-option
          v-for="(label, key) in priorityMap"
          :key="key"
          :label="label"
          :value="key"
        />
      </el-select>
      <el-input
        v-model="search"
        placeholder="搜索任务名"
        clearable
        class="tool-search"
        @keyup.enter="queryRelated"
        @clear="queryRelated"
      />
      <el-button type="primary" plain @click="queryRelated">查询</el-button>
      <el-button text @click="load">刷新</el-button>
    </div>
    <div v-else-if="mode !== 'department'" class="toolbar">
      <el-button
        v-if="canSelf"
        type="primary"
        @click="router.push({ path: '/tasks', query: { tab: 'write' } })"
      >
        写任务
      </el-button>
      <el-button text @click="load">刷新</el-button>
    </div>
    <div v-else class="toolbar">
      <el-button text @click="load">刷新</el-button>
    </div>

    <el-table :data="tasks" empty-text="暂无任务" @row-click="openDetail" class="clickable">
      <el-table-column
        v-if="mode !== 'project'"
        prop="project_name"
        label="所属项目"
        min-width="160"
        show-overflow-tooltip
      />
      <el-table-column prop="title" label="任务" min-width="180" show-overflow-tooltip />
      <el-table-column v-if="mode === 'related'" label="来源" width="110">
        <template #default="{ row }">
          <span v-if="row.source === 'report_done'" class="wb-tag wb-tag--green">周报总结</span>
          <span v-else-if="row.source === 'report_next'" class="wb-tag wb-tag--yellow">周报下一步</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="级别" width="80">
        <template #default="{ row }">
          <span class="wb-tag" :class="priorityTagClass(row.priority || 'p3')">
            {{ priorityMap[row.priority || 'p3'] || (row.priority || 'P3') }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          {{ row.assignment_type === 'delegated' ? '交办' : '自建' }}
        </template>
      </el-table-column>
      <el-table-column label="接收" width="90">
        <template #default="{ row }">{{ acceptLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="负责人" width="100">
        <template #default="{ row }">{{ row.assignee?.name || '—' }}</template>
      </el-table-column>
      <el-table-column v-if="mode !== 'department'" label="我的角色" width="90">
        <template #default="{ row }">
          {{ roleLabel(row) }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">{{ statusMap[row.status] || row.status }}</template>
      </el-table-column>
      <el-table-column prop="due_date" label="截止" width="110" />
    </el-table>

    <div v-if="mode === 'related'" class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 50, 100]"
        layout="total, sizes, prev, pager, next"
        :total="total"
        @current-change="onPageChange"
        @size-change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.tool-item {
  width: 140px;
}
.tool-search {
  width: 200px;
}
.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
.clickable {
  cursor: pointer;
}
.wb-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
}
.wb-tag--red {
  background: #fef0f0;
  color: #c45656;
}
.wb-tag--yellow {
  background: #fdf6ec;
  color: #b88230;
}
.wb-tag--green {
  background: #f0f9f4;
  color: #3d9970;
}
.muted {
  color: #999;
}
</style>
