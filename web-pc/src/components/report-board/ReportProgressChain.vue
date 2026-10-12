<script setup lang="ts">
defineProps<{
  nodes: Array<{
    item_id: number
    period_start: string
    period_end?: string
    task_title?: string
    result_text?: string
    quant_value?: string
    stuck_status?: string
    stuck_detail?: string
    next_goal?: string
    next_due_date?: string | null
    project_name?: string | null
  }>
  title?: string
}>()
</script>

<template>
  <div class="progress-chain">
    <div v-if="title" class="chain-title">{{ title }}</div>
    <div v-if="!nodes?.length" class="muted">暂无进度节点</div>
    <ol v-else class="timeline">
      <li v-for="n in nodes" :key="n.item_id" class="node">
        <div class="when">
          {{ n.period_start }}{{ n.period_end && n.period_end !== n.period_start ? ' ~ ' + n.period_end : '' }}
        </div>
        <div class="body">
          <div class="t">{{ n.task_title || '任务' }}</div>
          <div>做成了：{{ n.result_text || '—' }}
            <span v-if="n.quant_value" class="muted">（{{ n.quant_value }}）</span>
          </div>
          <div>下一步：{{ n.next_goal || '—' }}（{{ n.next_due_date || '未定日' }}）</div>
          <div v-if="n.stuck_status && n.stuck_status !== 'none'" class="stuck">
            卡点：{{ n.stuck_detail || '—' }}
          </div>
          <div v-if="n.project_name" class="muted">项目：{{ n.project_name }}</div>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.chain-title { font-weight: 600; margin-bottom: 8px; }
.timeline { list-style: none; margin: 0; padding: 0 0 0 14px; border-left: 2px solid #d9e4f5; }
.node { position: relative; padding: 0 0 14px 14px; }
.node::before {
  content: '';
  position: absolute;
  left: -18px;
  top: 6px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #5b8def;
  border: 2px solid #fff;
}
.when { font-size: 12px; color: #888; margin-bottom: 4px; }
.body { font-size: 13px; line-height: 1.5; background: #fafafa; border-radius: 8px; padding: 8px 10px; }
.t { font-weight: 600; margin-bottom: 4px; }
.stuck { color: #a65d00; }
.muted { color: #999; }
</style>
