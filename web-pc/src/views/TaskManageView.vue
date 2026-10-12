<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TaskAssignPanel from '../components/TaskAssignPanel.vue'
import WriteTaskView from './WriteTaskView.vue'
import { useUserStore } from '../stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 系统管理员、部门负责人看全部任务；其他人只看和自己有关的 */
const canSeeAll = computed(
  () => userStore.hasRole('admin') || userStore.hasRole('dept_head'),
)

const canWrite = computed(() => userStore.hasPermission('task.create.self'))

type TaskTab = 'list' | 'write'

const activeTab = computed<TaskTab>(() => {
  if (canWrite.value && route.query.tab === 'write') return 'write'
  return 'list'
})

function onTabChange(name: string | number) {
  const tab = String(name) as TaskTab
  if (tab === 'write' && canWrite.value) {
    router.replace({ path: '/tasks', query: { tab: 'write' } })
    return
  }
  router.replace({ path: '/tasks' })
}

/** 无写任务权限却带了 tab=write 时，清掉 query */
watch(
  () => [canWrite.value, route.query.tab] as const,
  ([can, tab]) => {
    if (!can && tab === 'write') {
      router.replace({ path: '/tasks' })
    }
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <h1 class="wb-page-title">任务管理</h1>
    <p class="wb-page-desc">
      <template v-if="activeTab === 'write'">
        保存后进入待办，并自动写入负责人本周周报草稿。
      </template>
      <template v-else-if="canSeeAll">你是管理员或部门负责人，可以查看全部任务。</template>
      <template v-else>
        只显示和你有关的任务：自己创建的（含派给别人的）、别人交办给你的、以及你当辅助人的。
      </template>
    </p>

    <el-tabs :model-value="activeTab" class="task-tabs" @tab-change="onTabChange">
      <el-tab-pane label="任务列表" name="list">
        <div class="panel">
          <TaskAssignPanel mode="related" />
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="canWrite" label="写任务" name="write" lazy>
        <WriteTaskView embedded />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.task-tabs {
  margin-top: 4px;
}

.panel {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: var(--wb-radius);
  padding: 16px 20px 8px;
  box-shadow: var(--wb-shadow-soft);
}
</style>
