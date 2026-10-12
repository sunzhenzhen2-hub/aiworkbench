<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { roleLabels } from '@/utils/roles'

const auth = useAuthStore()
const router = useRouter()

const labels = computed(() => roleLabels(auth.user?.roles))
const jobNumber = computed(() => auth.user?.job_number || auth.user?.username || '-')
const initial = computed(() => {
  const n = auth.user?.name || auth.user?.username || '?'
  return n.slice(0, 1)
})

function goPassword() {
  router.push({ name: 'password' })
}

async function doLogout() {
  await auth.logout()
  auth.denyMessage = '已退出，请重新打开工作台或联系管理员'
  router.replace({ name: 'home' })
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">我的</h1>
    <p class="page-desc">个人信息与账号设置</p>

    <div v-if="!auth.user" class="deny">
      {{ auth.denyMessage || '未登录，请在钉钉内打开并确认已开通免登' }}
    </div>

    <template v-else>
      <div class="card profile-head">
        <div class="avatar">
          <img v-if="auth.user.avatar_url" :src="auth.user.avatar_url" alt="" />
          <span v-else>{{ initial }}</span>
        </div>
        <div class="meta">
          <div class="name">{{ auth.user.name || auth.user.username }}</div>
          <div class="muted">工号 {{ jobNumber }}</div>
          <div class="role-row">
            <span v-for="r in labels" :key="r" class="tag">{{ r }}</span>
          </div>
        </div>
      </div>

      <div class="card info-list">
        <div class="info-row">
          <span class="muted">登录名</span>
          <span>{{ auth.user.username }}</span>
        </div>
        <div class="info-row">
          <span class="muted">钉钉绑定</span>
          <span>{{ auth.user.dingtalk_bound ? '已绑定' : '未绑定' }}</span>
        </div>
        <div v-if="auth.user.email" class="info-row">
          <span class="muted">邮箱</span>
          <span>{{ auth.user.email }}</span>
        </div>
      </div>

      <div class="card menu-list">
        <button type="button" class="menu-row" @click="router.push('/reviews')">
          <span>看评价</span>
          <span class="muted">›</span>
        </button>
      </div>

      <div class="card">
        <button class="btn btn-ghost" style="margin-bottom:10px" @click="goPassword">修改密码</button>
        <button class="btn btn-mint" @click="doLogout">退出登录</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.profile-head {
  display: flex;
  gap: 14px;
  align-items: center;
}
.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--wb-mint-bg);
  color: var(--wb-mint-text);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 600;
  overflow: hidden;
  flex-shrink: 0;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.meta .name {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 2px;
}
.role-row {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.info-list .info-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--wb-border);
  font-size: 14px;
}
.info-list .info-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.info-list .info-row:first-child {
  padding-top: 0;
}
.menu-list {
  padding: 0;
  overflow: hidden;
}
.menu-row {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: none;
  background: transparent;
  font-size: 15px;
  color: var(--wb-text);
  cursor: pointer;
  text-align: left;
}
</style>
