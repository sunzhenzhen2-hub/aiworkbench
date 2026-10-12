<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    people: Array<{
      user: { id: number; name: string }
      submitted: boolean
      escalate_count?: number
      blocker_count?: number
      submit_rate?: number
      submitted_weeks?: number
      expected_weeks?: number
      report_id?: number | null
    }>
    /** week=考核催办；day=仅记录已记/未记 */
    periodType?: 'week' | 'day'
  }>(),
  { periodType: 'week' },
)

const emit = defineEmits<{
  openPerson: [userId: number]
}>()

const isDay = computed(() => props.periodType === 'day')

const headTitle = computed(() => (isDay.value ? '今日记录' : '本期提交情况'))
const okLabel = computed(() => (isDay.value ? '已记' : '已交'))
const pendingLabel = computed(() => (isDay.value ? '今日未记' : '未交（催办）'))
const pendingEmpty = computed(() => (isDay.value ? '全部已记' : '全部已交'))
const statusOk = computed(() => (isDay.value ? '已记' : '已交'))
const statusPending = computed(() => (isDay.value ? '未记' : '未交'))

function rateText(p: { submit_rate?: number; submitted_weeks?: number; expected_weeks?: number }) {
  const rate = Math.round((p.submit_rate || 0) * 100)
  const sw = p.submitted_weeks ?? 0
  const ew = p.expected_weeks ?? 0
  return ew ? `${rate}%（${sw}/${ew}周）` : `${rate}%`
}
</script>

<template>
  <div class="people-board">
    <div class="strip panel">
      <div class="panel-head">
        {{ headTitle }}
        <span class="muted head-stat">
          {{ okLabel }} {{ people.filter((p) => p.submitted).length }} /
          {{ isDay ? '未记' : '未交' }} {{ people.filter((p) => !p.submitted).length }}
        </span>
      </div>
      <div class="strip-row">
        <div class="strip-col ok">
          <div class="strip-label">{{ okLabel }}</div>
          <div class="names">
            <template v-if="people.filter((p) => p.submitted).length">
              <button
                v-for="p in people.filter((x) => x.submitted)"
                :key="'ok-' + p.user.id"
                type="button"
                class="name-chip"
                @click="emit('openPerson', p.user.id)"
              >
                {{ p.user.name }}
              </button>
            </template>
            <span v-else class="muted">暂无</span>
          </div>
        </div>
        <div class="strip-col pending">
          <div class="strip-label">{{ pendingLabel }}</div>
          <div class="names">
            <template v-if="people.filter((p) => !p.submitted).length">
              <button
                v-for="p in people.filter((x) => !x.submitted)"
                :key="'no-' + p.user.id"
                type="button"
                class="name-chip warn"
                @click="emit('openPerson', p.user.id)"
              >
                {{ p.user.name }}
              </button>
            </template>
            <span v-else class="muted">{{ pendingEmpty }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="card-grid">
      <button
        v-for="p in people"
        :key="p.user.id"
        type="button"
        class="person-card"
        :class="{ submitted: p.submitted, pending: !p.submitted }"
        @click="emit('openPerson', p.user.id)"
      >
        <div class="card-top">
          <strong>{{ p.user.name }}</strong>
          <el-tag :type="p.submitted ? 'success' : 'info'" size="small" effect="plain">
            {{ p.submitted ? statusOk : statusPending }}
          </el-tag>
        </div>
        <div v-if="!isDay" class="card-meta">近几周提交率 {{ rateText(p) }}</div>
        <div class="card-flags">
          <span v-if="p.escalate_count" class="flag danger">升级 {{ p.escalate_count }}</span>
          <span v-if="p.blocker_count" class="flag warn">卡点 {{ p.blocker_count }}</span>
          <span v-if="!p.escalate_count && !p.blocker_count" class="muted">点击查看详情</span>
        </div>
      </button>
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
.strip-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) {
  .strip-row { grid-template-columns: 1fr; }
}
.strip-label { font-size: 13px; margin-bottom: 8px; color: var(--wb-text-muted); }
.names { display: flex; flex-wrap: wrap; gap: 8px; }
.name-chip {
  border: 1px solid var(--wb-border);
  background: #f5f8ff;
  border-radius: 999px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 13px;
}
.name-chip.warn { background: #fff7e8; border-color: #f0c78a; color: #8a5a00; }
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.person-card {
  text-align: left;
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  box-shadow: var(--wb-shadow-soft);
  transition: border-color 0.15s, transform 0.15s;
}
.person-card:hover {
  border-color: #5b8def;
  transform: translateY(-1px);
}
.person-card.pending { border-left: 3px solid #c0c4cc; }
.person-card.submitted { border-left: 3px solid var(--wb-accent); }
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.card-meta { font-size: 12px; color: var(--wb-text-muted); margin-bottom: 8px; }
.card-flags { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.flag.danger { color: #c45656; }
.flag.warn { color: #a65d00; }
.muted { color: #999; }
</style>
