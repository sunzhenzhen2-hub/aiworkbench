<script setup lang="ts">
import { ref } from 'vue'
import { formatDateTime } from '../../utils/datetime'

defineProps<{
  events: Array<{
    id: number
    action: string
    actor_name?: string
    created_at?: string | null
    payload?: Record<string, unknown> | null
  }>
}>()

const open = ref(false)

const ACTION_LABEL: Record<string, string> = {
  created: '创建任务',
  created_from_report: '从周报创建',
  updated_from_report: '从周报更新',
  accepted: '已接收',
  rejected: '已拒绝',
  status_changed: '状态变更',
  priority_changed: '优先级变更',
  assigned: '改派',
  completed: '完成',
  comment: '备注',
}

function label(action: string) {
  return ACTION_LABEL[action] || action
}
</script>

<template>
  <div class="event-chain">
    <button type="button" class="toggle" @click="open = !open">
      事件链（{{ events?.length || 0 }}）
      <span class="muted">{{ open ? '收起' : '展开' }}</span>
    </button>
    <ul v-if="open" class="list">
      <li v-if="!events?.length" class="muted">暂无关联交办事件</li>
      <li v-for="e in events" :key="e.id" class="ev">
        <div class="when">{{ formatDateTime(e.created_at) }}</div>
        <div>
          <strong>{{ label(e.action) }}</strong>
          <span v-if="e.actor_name" class="muted"> · {{ e.actor_name }}</span>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.toggle {
  border: none;
  background: transparent;
  padding: 0;
  cursor: pointer;
  font-size: 13px;
  color: #3a6fd8;
  display: flex;
  gap: 8px;
  align-items: center;
}
.list { list-style: none; margin: 8px 0 0; padding: 0; }
.ev {
  font-size: 13px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--wb-border);
}
.when { font-size: 12px; color: #888; }
.muted { color: #999; }
</style>
