<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { homePathAfterLogin } from '../productLine'
import { PASSWORD_RULES, validateStrongPassword } from '../utils/password'

const router = useRouter()
const userStore = useUserStore()
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const rules = ref(PASSWORD_RULES)

const forced = computed(() => !!userStore.user?.must_change_password)
const pageTitle = computed(() => (forced.value ? '设置新密码' : '修改密码'))
const pageDesc = computed(() =>
  forced.value
    ? '首次登录须修改初始密码，完成后即可使用系统'
    : '请输入当前密码并设置新密码',
)
const submitLabel = computed(() => (forced.value ? '确认并进入系统' : '保存'))

onMounted(async () => {
  if (!userStore.user) await userStore.fetchMe()
  try {
    const { data } = await api.get('/auth/password-policy')
    if (data.rules?.length) rules.value = data.rules
  } catch {
    /* 使用本地默认规则 */
  }
})

async function handleSubmit() {
  const err = validateStrongPassword(newPassword.value)
  if (err) {
    ElMessage.error(err)
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    ElMessage.error('两次输入的新密码不一致')
    return
  }
  const wasForced = forced.value
  loading.value = true
  try {
    await api.post('/auth/change-password', {
      old_password: oldPassword.value,
      new_password: newPassword.value,
    })
    await userStore.fetchMe()
    ElMessage.success('密码修改成功')
    const home = homePathAfterLogin(userStore.hasPermission('dsh.access'))
    if (wasForced) {
      router.push(home)
    } else if (window.history.length > 1) {
      router.back()
    } else {
      router.push(home)
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '密码修改失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-panel">
      <h1 class="title">{{ pageTitle }}</h1>
      <p class="desc">{{ pageDesc }}</p>

      <form class="form" @submit.prevent="handleSubmit">
        <div class="field">
          <label>当前密码</label>
          <el-input
            v-model="oldPassword"
            type="password"
            show-password
            placeholder="请输入当前密码"
            size="large"
          />
        </div>
        <div class="field">
          <label>新密码</label>
          <el-input
            v-model="newPassword"
            type="password"
            show-password
            placeholder="8位以上，含大小写字母和数字"
            size="large"
          />
        </div>
        <div class="field">
          <label>确认新密码</label>
          <el-input v-model="confirmPassword" type="password" show-password size="large" />
        </div>
        <ul class="rules">
          <li v-for="(r, i) in rules" :key="i">{{ r }}</li>
        </ul>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" class="submit-btn">
          {{ submitLabel }}
        </el-button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--wb-bg-muted);
  padding: 40px 20px;
}

.auth-panel {
  width: 100%;
  max-width: 440px;
  text-align: center;
}

.title {
  font-size: 26px;
  font-weight: 600;
  color: var(--wb-text-primary);
  letter-spacing: -0.03em;
  margin: 0 0 8px;
}

.desc {
  font-size: 14px;
  color: var(--wb-text-secondary);
  margin: 0 0 28px;
}

.form {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: 16px;
  padding: 28px 24px;
  box-shadow: var(--wb-shadow-soft);
  text-align: left;
}

.field {
  margin-bottom: 18px;
}

.field label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--wb-text-secondary);
  margin-bottom: 8px;
}

.rules {
  margin: 0 0 20px;
  padding: 12px 14px 12px 28px;
  background: var(--wb-bg-muted);
  border-radius: 10px;
  font-size: 12px;
  color: var(--wb-text-muted);
  line-height: 1.7;
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
}
</style>
