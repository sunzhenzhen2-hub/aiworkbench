<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  projects: any[]
  unassignedItems: any[]
  tagFilter: string
}>()

const emit = defineEmits<{
  ack: [blockerId: number]
  openReport: [reportId: number]
}>()

function itemMatchesTag(it: any) {
  if (!props.tagFilter) return true
  const tags = Array.isArray(it?.tags) ? it.tags : []
  return tags.includes(props.tagFilter)
}

/** 已选项目或推断归属都算挂在该项目上 */
function itemBelongsToProject(it: any, projectId: number) {
  return it?.project_id === projectId || it?.inferred_project_id === projectId
}

function filteredItems(items: any[]) {
  return (items || []).filter(itemMatchesTag)
}

function projectItems(r: any, projectId: number) {
  return filteredItems((r?.items || []).filter((x: any) => itemBelongsToProject(x, projectId)))
}

const filteredUnassigned = computed(() =>
  (props.unassignedItems || []).filter(itemMatchesTag),
)
</script>

<template>
  <div>
    <div class="panel">
      <div class="panel-head">
        未归类任务汇总
        <span class="muted head-stat">共 {{ unassignedItems?.length || 0 }} 条</span>
      </div>
      <div v-if="!filteredUnassigned.length" class="muted">本周期暂无未归类任务</div>
      <div v-for="(it, i) in filteredUnassigned" :key="i" class="item unassigned">
        <div class="who">
          {{ it.author_name }} · {{ it.task_title || '无概述' }}
          <el-tag v-for="t in it.tags || []" :key="t" size="small" type="success" effect="plain">{{ t }}</el-tag>
          <el-button link type="primary" size="small" @click="emit('openReport', it.report_id)">打开周报</el-button>
        </div>
        <div v-if="!it.no_action">
          <div>做成了：{{ it.result_text }}</div>
          <div>下一步：{{ it.next_goal }}（{{ it.next_due_date || '—' }}）</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">按项目 · 主辅对齐与风险</div>
      <div v-if="!projects?.length" class="muted">本周暂无已归类到项目的周报。</div>
      <div v-for="p in projects" :key="p.project_id" class="proj">
        <div class="proj-title">
          <strong>{{ p.project_name }}</strong>
          <el-tag v-if="!p.lead_aligned" type="warning" size="small" effect="plain">主责未对齐</el-tag>
          <el-tag v-if="p.escalate_count" type="danger" size="small" effect="plain">需升级 {{ p.escalate_count }}</el-tag>
          <el-tag v-if="p.unacked_blockers" type="warning" size="small" effect="plain">未确认卡点 {{ p.unacked_blockers }}</el-tag>
        </div>
        <div class="meta">
          项目经理：{{ p.lead?.name || '未指定' }}
          <span v-if="p.assistants?.length"> · 辅助：{{ p.assistants.map((a: any) => a.name).join('、') }}</span>
          · 未关闭卡点 {{ p.open_blockers }}
        </div>
        <div v-for="r in p.reports" :key="r.id" class="report-slice">
          <div class="who">{{ r.author.name }} · {{ r.theme || '无摘要' }}</div>
          <div
            v-for="it in projectItems(r, p.project_id)"
            :key="it.id"
            class="item"
          >
            <template v-if="!it.no_action">
              <div v-if="it.task_title">
                <strong>{{ it.task_title }}</strong>
                <el-tag
                  v-if="it.project_inferred && !it.project_id"
                  size="small"
                  type="info"
                  effect="plain"
                  style="margin-left:6px"
                >推断归属</el-tag>
              </div>
              <div>做成了：{{ it.result_text }}</div>
              <div>下一步：{{ it.next_goal }}（{{ it.next_due_date || '—' }}）</div>
              <div v-if="it.stuck_status !== 'none'" class="stuck">
                卡点：{{ it.stuck_detail }}
                <el-button
                  v-if="it.linked_blocker_id"
                  link
                  type="primary"
                  size="small"
                  @click="emit('ack', it.linked_blocker_id)"
                >主责已知悉</el-button>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
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
}
.head-stat { font-weight: 400; font-size: 13px; }
.proj { border-top: 1px solid var(--wb-border); padding: 14px 0; }
.proj:first-of-type { border-top: none; }
.proj-title { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.meta { font-size: 13px; color: var(--wb-text-muted); margin-bottom: 8px; }
.report-slice { background: #fafafa; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.who { font-weight: 500; margin-bottom: 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.item { font-size: 13px; line-height: 1.55; margin-bottom: 8px; }
.unassigned { background: #fafafa; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.stuck { color: #a65d00; }
.muted { color: #999; }
</style>
