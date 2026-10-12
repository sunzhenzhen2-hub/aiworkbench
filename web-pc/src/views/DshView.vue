<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const ANALYZE_CTX_KEY = 'wb_assist_analyze_ctx'

const embedUrl = ref('')
const error = ref('')
const loading = ref(true)
const ctxHint = ref('')
const promptPack = ref('')
const slowHint = ref(false)
let slowTimer: number | undefined

onMounted(async () => {
  await openAssistant()
})

onUnmounted(() => {
  if (slowTimer) window.clearTimeout(slowTimer)
})

async function openAssistant() {
  loading.value = true
  error.value = ''
  embedUrl.value = ''
  slowHint.value = false
  if (slowTimer) window.clearTimeout(slowTimer)
  slowTimer = window.setTimeout(() => {
    if (embedUrl.value) slowHint.value = true
  }, 8000)
  try {
    const raw = sessionStorage.getItem(ANALYZE_CTX_KEY)
    if (raw) {
      try {
        const ctx = JSON.parse(raw)
        promptPack.value = ctx?.prompt_pack || ''
        ctxHint.value = ctx?.summary
          ? `已附带分析材料：${String(ctx.summary).slice(0, 80)}${String(ctx.summary).length > 80 ? '…' : ''}`
          : '已附带分析管控材料，可复制提问稿粘贴到对话中。'
      } catch {
        sessionStorage.removeItem(ANALYZE_CTX_KEY)
      }
    }

    const { data } = await api.post('/dsh/ticket')
    if (!data?.embed_url) {
      error.value = '智能助手还没准备好'
      return
    }
    embedUrl.value = data.embed_url
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '打不开智能助手，请稍后再试'
  } finally {
    loading.value = false
  }
}

async function copyCtx() {
  if (!promptPack.value) return
  try {
    await navigator.clipboard.writeText(promptPack.value)
    ElMessage.success('提问稿已复制，请粘贴到下方对话')
  } catch {
    ElMessage.error('复制失败，请手动选中复制')
  }
}

function dismissCtx() {
  sessionStorage.removeItem(ANALYZE_CTX_KEY)
  ctxHint.value = ''
  promptPack.value = ''
}

async function reloadAssistant() {
  slowHint.value = false
  await openAssistant()
}
</script>

<template>
  <div class="dsh-page">
    <div v-if="ctxHint" class="ctx-bar">
      <span class="ctx-text">{{ ctxHint }}</span>
      <div class="ctx-actions">
        <el-button size="small" type="primary" @click="copyCtx">复制提问稿</el-button>
        <el-button size="small" @click="dismissCtx">关闭</el-button>
      </div>
    </div>
    <p v-if="loading" class="hint">正在进入你的助手…</p>
    <p v-else-if="error" class="hint error">
      {{ error }}
      <button type="button" class="retry" @click="reloadAssistant">再试一次</button>
    </p>
    <template v-else>
      <div v-if="slowHint" class="slow-bar">
        <span>助手页面若一直是黑的，点这里重新打开</span>
        <el-button size="small" type="primary" @click="reloadAssistant">刷新助手</el-button>
      </div>
      <iframe :src="embedUrl" title="我的助手" class="dsh-frame" />
    </template>
  </div>
</template>

<style scoped>
.dsh-page {
  position: absolute;
  inset: 0;
  background: #111;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.ctx-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: #1e2a24;
  border-bottom: 1px solid #2d4036;
  color: #d8efe4;
  font-size: 13px;
  flex-shrink: 0;
}
.ctx-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.hint {
  margin: 0;
  padding: 24px;
  color: #666;
}
.hint.error {
  color: #c45656;
}
.retry {
  margin-left: 12px;
  border: 0;
  background: transparent;
  color: #7ee0a8;
  cursor: pointer;
  text-decoration: underline;
}
.slow-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
  background: #1a2330;
  border-bottom: 1px solid #2a3545;
  color: #c5cdd8;
  font-size: 13px;
  flex-shrink: 0;
}
.dsh-frame {
  display: block;
  width: 100%;
  flex: 1;
  min-height: 0;
  border: 0;
  background: #151517;
}
</style>
