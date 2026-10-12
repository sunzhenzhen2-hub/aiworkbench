<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'

type TaskRow = {
  id: number
  title: string
  status: string
  assignment_type?: string
  my_role?: string
  due_date?: string | null
  project_name?: string
  assignee?: { id: number; name: string } | null
}

type BlockerRow = {
  id: number
  description: string
  status: string
  project_name?: string
}

const TASK_STATUS: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
const BLOCKER_STATUS: Record<string, string> = {
  following: '跟进中',
  pending_decision: '待决策',
  escalated: '已升级',
  resolved: '已解决',
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const tasks = ref<TaskRow[]>([])
const departmentTasks = ref<TaskRow[]>([])
const showDepartment = ref(false)
const blockers = ref<BlockerRow[]>([])
const error = ref('')

const searchQ = computed(() =>
  typeof route.query.search === 'string' ? route.query.search.trim().toLowerCase() : '',
)

const filteredTasks = computed(() => {
  if (!searchQ.value) return tasks.value
  return tasks.value.filter(
    (t) =>
      (t.title || '').toLowerCase().includes(searchQ.value) ||
      (t.project_name || '').toLowerCase().includes(searchQ.value),
  )
})

const filteredDept = computed(() => {
  if (!searchQ.value) return departmentTasks.value
  return departmentTasks.value.filter(
    (t) =>
      (t.title || '').toLowerCase().includes(searchQ.value) ||
      (t.project_name || '').toLowerCase().includes(searchQ.value) ||
      (t.assignee?.name || '').toLowerCase().includes(searchQ.value),
  )
})

async function load() {
  try {
    const data = await request<{
      my_tasks?: TaskRow[]
      department_tasks?: TaskRow[]
      can_view_department_todos?: boolean
      my_blockers?: BlockerRow[]
    }>('/personal/workspace')
    tasks.value = data.my_tasks || []
    departmentTasks.value = data.department_tasks || []
    showDepartment.value = !!data.can_view_department_todos
    blockers.value = data.my_blockers || []
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
}

async function setBlockerStatus(id: number, status: string) {
  try {
    await request(`/blockers/${id}`, 'POST', { status })
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '更新失败')
  }
}

async function toggleComplete(t: TaskRow, ev: Event) {
  ev.stopPropagation()
  if (t.status === 'completed') return
  try {
    await request(`/tasks/${t.id}`, 'PATCH', {
      status: 'completed',
      result_text: '任务页快捷完成',
    })
    await load()
  } catch (err) {
    alert(err instanceof Error ? err.message : '完成失败（可能需在详情页补结果说明/附件）')
  }
}

onMounted(async () => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  await load()
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">任务</h1>
    <p class="page-desc">
      我的待办与卡点
      <template v-if="searchQ"> · 筛选「{{ route.query.search }}」</template>
    </p>
    <div v-if="error" class="deny">{{ error }}</div>

    <div class="btn-row" style="margin-bottom:14px">
      <button class="btn btn-mint" @click="router.push('/tasks/new')">写任务</button>
      <button class="btn btn-ghost" @click="router.push('/blocker/new')">登记卡点</button>
    </div>

    <template v-if="showDepartment">
      <h2 class="sec">部门待办</h2>
      <p class="muted" style="margin:-4px 0 8px">成员未完成待办汇总（只读）</p>
      <div v-if="!filteredDept.length" class="empty" style="padding:20px 0">暂无部门待办</div>
      <div
        v-for="t in filteredDept"
        :key="'d-' + t.id"
        class="card tap todo-row"
        @click="router.push(`/tasks/${t.id}`)"
      >
        <div class="check off">✓</div>
        <div class="todo-body">
          <div class="row">
            <span class="tag tag-warn">部门</span>
            <span class="muted" style="margin-left:auto">{{ TASK_STATUS[t.status] || t.status }}</span>
          </div>
          <div class="title">{{ t.title }}</div>
          <div class="muted">
            {{ t.project_name || '' }}
            <template v-if="t.assignee?.name"> · {{ t.assignee.name }}</template>
            {{ t.due_date ? ` · 截止 ${t.due_date}` : '' }}
          </div>
        </div>
      </div>
    </template>

    <h2 class="sec">个人待办</h2>
    <div v-if="!filteredTasks.length" class="empty">暂无任务</div>
    <div
      v-for="t in filteredTasks"
      :key="t.id"
      class="card tap todo-row"
      @click="router.push(`/tasks/${t.id}`)"
    >
      <button
        type="button"
        class="check"
        :class="{ on: t.status === 'completed' }"
        @click="toggleComplete(t, $event)"
      >
        ✓
      </button>
      <div class="todo-body">
        <div class="row">
          <span class="tag">{{ t.assignment_type === 'delegated' ? '交办' : '自建' }}</span>
          <span v-if="t.my_role" class="tag" style="margin-left:6px">
            {{ t.my_role === 'owner' ? '负责人' : t.my_role === 'assistant' ? '辅助' : '' }}
          </span>
          <span class="muted" style="margin-left:auto">{{ TASK_STATUS[t.status] || t.status }}</span>
        </div>
        <div class="title">{{ t.title }}</div>
        <div class="muted">{{ t.project_name || '' }} {{ t.due_date ? `· 截止 ${t.due_date}` : '' }}</div>
      </div>
    </div>

    <h2 class="sec">卡点</h2>
    <div v-if="!blockers.length" class="empty">暂无卡点</div>
    <div v-for="b in blockers" :key="b.id" class="card">
      <div class="row">
        <span :class="b.status === 'resolved' ? 'tag' : 'tag tag-warn'">
          {{ BLOCKER_STATUS[b.status] || b.status }}
        </span>
        <span class="muted" style="margin-left:auto">{{ b.project_name }}</span>
      </div>
      <div class="title">{{ b.description }}</div>
      <div class="chips">
        <button
          v-for="s in ['following', 'pending_decision', 'escalated', 'resolved']"
          :key="s"
          class="chip"
          :class="{ on: b.status === s }"
          @click="setBlockerStatus(b.id, s)"
        >{{ BLOCKER_STATUS[s] }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sec { font-size: 16px; font-weight: 700; margin: 16px 0 8px; }
.row { display: flex; align-items: center; margin-bottom: 6px; }
.title { font-weight: 600; margin-bottom: 4px; }
.tap { cursor: pointer; }
.chips { margin-top: 8px; }
.todo-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.check {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--wb-border);
  background: #fff;
  color: transparent;
  flex-shrink: 0;
  margin-top: 2px;
  font-size: 12px;
  padding: 0;
}
.check.on {
  background: var(--wb-mint);
  border-color: var(--wb-mint);
  color: #fff;
}
.check.off {
  border-style: dashed;
  color: transparent;
  pointer-events: none;
}
.todo-body { flex: 1; min-width: 0; }
</style>
