<script setup lang="ts">
import { useRouter } from 'vue-router'
import { AGENT_NATIVE_HINT } from '../productLine'

withDefaults(
  defineProps<{
    tip?: string
  }>(),
  { tip: AGENT_NATIVE_HINT },
)

const router = useRouter()
</script>

<template>
  <div class="agent-native-hint">
    <p>{{ tip }}</p>
    <el-button type="primary" plain size="small" @click="router.push('/dsh')">打开我的助手</el-button>
  </div>
</template>

<style scoped>
.agent-native-hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin: 8px 0 12px;
  padding: 10px 12px;
  background: #f4f8ff;
  border: 1px solid #d6e4ff;
  border-radius: 8px;
  color: #334155;
  font-size: 13px;
}
.agent-native-hint p {
  margin: 0;
  flex: 1;
  min-width: 180px;
}
</style>
