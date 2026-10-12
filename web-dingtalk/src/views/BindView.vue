<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request, saveToken } from '@/utils/api'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!auth.bindTicket) {
    error.value = '绑定已过期，请返回首页重新打开'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const body = await request<{
      access_token?: string | null
      must_change_password?: boolean
    }>('/auth/dingtalk/bind', 'POST', {
      bind_ticket: auth.bindTicket,
      username: username.value.trim(),
      password: password.value,
    })
    if (!body.access_token) throw new Error('绑定失败')
    saveToken(body.access_token)
    auth.bindTicket = ''
    await auth.fetchMe()
    // 绑定后走免登，不强制改密
    router.replace({ name: 'home' })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '绑定失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page no-tab">
    <h1 class="page-title">绑定系统账号</h1>
    <p class="page-desc">首次使用请用管理员给你的账号密码绑定钉钉</p>
    <div v-if="error" class="deny">{{ error }}</div>
    <div class="card">
      <div class="field">
        <div class="field-label">用户名（工号）</div>
        <input v-model="username" class="input" autocomplete="username" />
      </div>
      <div class="field">
        <div class="field-label">密码</div>
        <input v-model="password" class="input" type="password" autocomplete="current-password" />
      </div>
      <button class="btn" :disabled="loading" @click="submit">绑定并进入</button>
    </div>
  </div>
</template>
