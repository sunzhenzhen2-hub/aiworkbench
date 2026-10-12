<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'
import SearchableSelect from '@/components/SearchableSelect.vue'

type Project = { id: number; name: string }

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const projects = ref<Project[]>([])
const projectId = ref<number | null>(null)
const projectOptions = computed(() =>
  projects.value.map((p) => ({ value: p.id, label: p.name })),
)
const description = ref('')
const responsibleParty = ref('')
const nextAction = ref('')
const nextFollowUp = ref('')
const error = ref('')
const submitting = ref(false)

onMounted(async () => {
  if (!auth.user) {
    error.value = auth.denyMessage || '未登录'
    return
  }
  try {
    const data = await request<{ my_projects?: Project[] }>('/personal/workspace')
    projects.value = data.my_projects || []
    const qid = Number(route.query.project_id)
    if (qid && !projects.value.some((p) => p.id === qid)) {
      try {
        const detail = await request<Project>(`/projects/${qid}`)
        projects.value = [detail, ...projects.value]
      } catch {
        /* 忽略，仍用列表 */
      }
    }
    if (qid && projects.value.some((p) => p.id === qid)) {
      projectId.value = qid
    } else if (projects.value[0]) {
      projectId.value = projects.value[0].id
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
})

async function submit() {
  if (!projectId.value) {
    alert('请选择项目')
    return
  }
  if (!description.value || !nextAction.value || !nextFollowUp.value) {
    alert('请填写描述、下一步和跟进日期')
    return
  }
  submitting.value = true
  try {
    await request(`/blockers/project/${projectId.value}`, 'POST', {
      description: description.value,
      responsible_party: responsibleParty.value || '待确认',
      party_type: 'internal',
      actions_taken: '钉钉端登记',
      next_action: nextAction.value,
      next_follow_up: nextFollowUp.value,
    })
    alert('卡点已登记')
    router.replace({ name: 'todo' })
  } catch (err) {
    alert(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="page no-tab">
    <button class="back" @click="router.back()">← 返回</button>
    <h1 class="page-title">登记卡点</h1>
    <p class="page-desc">记下卡在哪里、谁负责、下一步何时跟进</p>
    <div v-if="error" class="deny">{{ error }}</div>
    <template v-else>
      <div class="field">
        <div class="field-label">项目</div>
        <SearchableSelect
          v-model="projectId"
          :options="projectOptions"
          placeholder="输入关键字搜索项目"
        />
      </div>
      <div class="field">
        <div class="field-label">卡点描述</div>
        <textarea v-model="description" class="textarea" />
      </div>
      <div class="field">
        <div class="field-label">责任方</div>
        <input v-model="responsibleParty" class="input" placeholder="默认：待确认" />
      </div>
      <div class="field">
        <div class="field-label">下一步行动</div>
        <textarea v-model="nextAction" class="textarea" />
      </div>
      <div class="field">
        <div class="field-label">跟进日期</div>
        <input v-model="nextFollowUp" class="input" type="date" />
      </div>
      <button class="btn" :disabled="submitting" @click="submit">提交</button>
    </template>
  </div>
</template>

<style scoped>
.back {
  border: none;
  background: transparent;
  color: var(--wb-mint-text);
  font-size: 14px;
  padding: 0 0 12px;
  cursor: pointer;
}
</style>
