<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/utils/api'

const embedUrl = ref('')
const error = ref('')
const loading = ref(true)

onMounted(async () => {
  try {
    const { data } = await api.post<{ embed_url?: string }>('/dsh/ticket')
    if (!data?.embed_url) {
      error.value = '智能助手还没准备好'
      return
    }
    embedUrl.value = data.embed_url
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '打不开智能助手，请稍后再试'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="dsh-page">
    <p v-if="loading" class="hint">正在进入你的助手…</p>
    <p v-else-if="error" class="hint error">{{ error }}</p>
    <iframe v-else :src="embedUrl" title="我的助手" class="dsh-frame" />
  </div>
</template>

<style scoped>
.dsh-page {
  height: calc(100dvh - var(--wb-tab-h) - var(--wb-safe-bottom));
  min-height: calc(100vh - var(--wb-tab-h) - var(--wb-safe-bottom));
  background: #111;
  overflow: hidden;
}
.hint {
  margin: 0;
  padding: 24px 16px;
  color: #999;
  font-size: 14px;
}
.hint.error {
  color: var(--wb-danger, #c45656);
}
.dsh-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: #111;
}
</style>
