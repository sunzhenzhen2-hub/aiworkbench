<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { request } from '@/utils/api'

const auth = useAuthStore()
const router = useRouter()
const oldPassword = ref('')
const newPassword = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  loading.value = true
  error.value = ''
  try {
    await request('/auth/change-password', 'POST', {
      old_password: oldPassword.value,
      new_password: newPassword.value,
    })
    await auth.fetchMe()
    router.replace({ name: 'mine' })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '修改失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page no-tab">
    <h1 class="page-title">修改密码</h1>
    <p class="page-desc">请设置新密码（8 位以上，含大小写字母和数字）</p>
    <div v-if="error" class="deny">{{ error }}</div>
    <div class="card">
      <div class="field">
        <div class="field-label">当前密码</div>
        <input v-model="oldPassword" class="input" type="password" />
      </div>
      <div class="field">
        <div class="field-label">新密码</div>
        <input v-model="newPassword" class="input" type="password" />
      </div>
      <button class="btn" :disabled="loading" @click="submit">保存新密码</button>
      <button class="btn btn-ghost" style="margin-top:10px" type="button" @click="router.replace({ name: 'mine' })">
        返回我的
      </button>
    </div>
  </div>
</template>
