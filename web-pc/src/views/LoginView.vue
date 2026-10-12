<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import {
  clearDingTalkQrContainer,
  friendlyDingTalkScanError,
  mountDingTalkQrLogin,
  shouldReloadDingTalkQr,
  type DingTalkQrConfig,
} from '../composables/dingtalkQrLogin'
import { useUserStore } from '../stores/user'
import { homePathAfterLogin, isAgentNativeProduct, workbenchHref } from '../productLine'

const router = useRouter()
const userStore = useUserStore()
const agentNative = isAgentNativeProduct()

const activeTab = ref<'dingtalk' | 'password'>('dingtalk')
const username = ref('manager')
const password = ref('')
const loading = ref(false)

const DT_CONTAINER_ID = 'dt-login-qr'
const dingtalkLoading = ref(false)
const dingtalkStatus = ref('')
const dingtalkError = ref('')
const bindTicket = ref('')
const bindUsername = ref('')
const bindPassword = ref('')
const bindLoading = ref(false)

let expectedOauthState = ''
let allowedMessageOrigin = ''
let scanGeneration = 0
let authExchangeInFlight = false
const processedAuthCodes = new Set<string>()

function goAfterLogin(mustChange: boolean) {
  const dest = mustChange ? '/change-password' : homePathAfterLogin(userStore.hasPermission('dsh.access'))
  void router.replace(dest)
}

function leaveLoginAfterDingTalk(mustChange: boolean) {
  const dest = mustChange ? '/change-password' : homePathAfterLogin(userStore.hasPermission('dsh.access'))
  window.location.replace(workbenchHref(dest))
}

async function handleLogin() {
  loading.value = true
  try {
    const mustChange = await userStore.login(username.value, password.value)
    goAfterLogin(mustChange)
  } catch {
    ElMessage.error('登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}

function onDingTalkMessage(ev: MessageEvent) {
  // 允许本页源，或本次 dingtalk-config 返回的 redirect_uri 源（VPN 跨源）
  const okOrigin =
    ev.origin === window.location.origin ||
    (allowedMessageOrigin !== '' && ev.origin === allowedMessageOrigin)
  if (!okOrigin) return
  const data = ev.data
  if (!data || data.type !== 'dingtalk-oauth' || !data.authCode) return
  if (data.state && expectedOauthState && data.state !== expectedOauthState) return
  void handleAuthCode(data.authCode, data.state)
}

async function handleAuthCode(authCode: string, state?: string) {
  if (!authCode) return
  if (dingtalkStatus.value === 'success') return
  if (authExchangeInFlight || processedAuthCodes.has(authCode)) return
  if (state && expectedOauthState && state !== expectedOauthState) {
    dingtalkStatus.value = 'error'
    dingtalkError.value = '登录状态校验失败，请刷新二维码'
    return
  }
  authExchangeInFlight = true
  processedAuthCodes.add(authCode)
  dingtalkLoading.value = true
  dingtalkStatus.value = 'pending'
  dingtalkError.value = ''
  bindTicket.value = ''
  try {
    const { data } = await api.post(
      '/auth/dingtalk-scan',
      {
        auth_code: authCode,
        state: state || expectedOauthState,
      },
      { timeout: 12000 },
    )
    if (data.status === 'need_bind') {
      dingtalkStatus.value = 'need_bind'
      bindTicket.value = data.bind_ticket || ''
      ElMessage.info(data.message || '请使用系统账号绑定钉钉')
      return
    }
    if (data.status === 'ok' && data.access_token) {
      dingtalkStatus.value = 'success'
      const mustChange = await userStore.applyDingTalkLogin(
        data.access_token,
        data.must_change_password,
      )
      leaveLoginAfterDingTalk(mustChange)
      return
    }
    throw new Error(data.message || '登录失败')
  } catch (e: unknown) {
    dingtalkStatus.value = 'error'
    dingtalkError.value = friendlyDingTalkScanError(e)
    processedAuthCodes.delete(authCode)
    ElMessage.error(dingtalkError.value)
  } finally {
    authExchangeInFlight = false
    dingtalkLoading.value = false
  }
}

let qrAutoReloads = 0

async function startDingtalkScan() {
  qrAutoReloads = 0
  await loadDingTalkQr(false)
}

async function loadDingTalkQr(fromAuto: boolean) {
  if (fromAuto) {
    if (qrAutoReloads >= 2) return
    qrAutoReloads += 1
  }
  const gen = ++scanGeneration
  dingtalkStatus.value = ''
  dingtalkError.value = ''
  bindTicket.value = ''
  dingtalkLoading.value = true
  processedAuthCodes.clear()
  authExchangeInFlight = false
  clearDingTalkQrContainer(DT_CONTAINER_ID)
  try {
    const { data } = await api.get<DingTalkQrConfig>('/auth/dingtalk-config', { timeout: 20000 })
    if (gen !== scanGeneration) return
    expectedOauthState = data.state || ''
    try {
      allowedMessageOrigin = data.redirect_uri ? new URL(data.redirect_uri).origin : ''
    } catch {
      allowedMessageOrigin = ''
    }
    await nextTick()
    await mountDingTalkQrLogin({
      containerId: DT_CONTAINER_ID,
      config: data,
      width: 280,
      height: 280,
      onSuccess: ({ authCode, state }) => {
        if (gen !== scanGeneration) return
        void handleAuthCode(authCode, state)
      },
      onError: (msg) => {
        if (gen !== scanGeneration) return
        if (
          authExchangeInFlight ||
          dingtalkStatus.value === 'pending' ||
          dingtalkStatus.value === 'success'
        ) {
          return
        }
        dingtalkStatus.value = 'error'
        dingtalkError.value = friendlyDingTalkScanError(msg)
        if (shouldReloadDingTalkQr(msg)) {
          window.setTimeout(() => {
            if (gen !== scanGeneration) return
            if (authExchangeInFlight || dingtalkStatus.value === 'success') return
            void loadDingTalkQr(true)
          }, 400)
        }
      },
    })
  } catch (e: unknown) {
    const status = (e as { response?: { status?: number } })?.response?.status
    dingtalkStatus.value = 'error'
    dingtalkError.value =
      status === 400
        ? '本机还没开通钉钉扫码，请改用账号密码登录'
        : friendlyDingTalkScanError(e)
  } finally {
    if (gen === scanGeneration) dingtalkLoading.value = false
  }
}

async function handleBind() {
  if (!bindTicket.value) {
    ElMessage.error('绑定已过期，请刷新二维码')
    return
  }
  bindLoading.value = true
  try {
    const { data } = await api.post('/auth/dingtalk/bind', {
      bind_ticket: bindTicket.value,
      username: bindUsername.value,
      password: bindPassword.value,
    })
    if (data.status === 'ok' && data.access_token) {
      const mustChange = await userStore.applyDingTalkLogin(
        data.access_token,
        data.must_change_password,
      )
      leaveLoginAfterDingTalk(mustChange)
      return
    }
    ElMessage.error(data.message || '绑定失败')
  } catch (e: unknown) {
    const detail =
      (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '绑定失败，请检查账号密码或权限'
    ElMessage.error(String(detail))
  } finally {
    bindLoading.value = false
  }
}

watch(
  activeTab,
  async (val) => {
    if (val === 'dingtalk') {
      await startDingtalkScan()
    } else {
      scanGeneration += 1
      clearDingTalkQrContainer(DT_CONTAINER_ID)
    }
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('message', onDingTalkMessage)
})

onUnmounted(() => {
  window.removeEventListener('message', onDingTalkMessage)
  scanGeneration += 1
  clearDingTalkQrContainer(DT_CONTAINER_ID)
})
</script>

<template>
  <div class="auth-page" :class="{ 'auth-page--native': agentNative }">
    <div class="auth-panel">
      <div class="logo-mark" aria-hidden="true">
        <svg v-if="agentNative" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <path d="M4 12a8 8 0 0 1 14.3-5.1L21 5v6h-6l2.2-2.2A6 6 0 1 0 18 13" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
        </svg>
        <svg v-else width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      </div>
      <h1 class="title">{{ agentNative ? '我的助手' : '项目支撑工作台' }}</h1>
      <p class="desc">{{
        agentNative
          ? '跟助手说话就能记周报、任务、项目；你确认后才写入账本'
          : '管理者一眼看清全局，团队记录不再零散'
      }}</p>

      <div class="form">
        <div class="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            class="tab"
            :class="{ active: activeTab === 'dingtalk' }"
            :aria-selected="activeTab === 'dingtalk'"
            @click="activeTab = 'dingtalk'"
          >
            钉钉扫码
          </button>
          <button
            type="button"
            role="tab"
            class="tab"
            :class="{ active: activeTab === 'password' }"
            :aria-selected="activeTab === 'password'"
            @click="activeTab = 'password'"
          >
            账号密码
          </button>
        </div>

        <div v-show="activeTab === 'dingtalk'" class="dingtalk-pane">
          <div v-if="dingtalkStatus !== 'need_bind'" class="qr-wrap">
            <div class="qr-box-wrap">
              <div :id="DT_CONTAINER_ID" class="qr-box" />
              <div v-if="dingtalkStatus === 'pending' || dingtalkStatus === 'success'" class="qr-mask">
                正在向钉钉确认身份，请稍等…
              </div>
            </div>
            <p v-if="dingtalkLoading && !dingtalkError && !dingtalkStatus" class="status-text">正在加载二维码…</p>
            <p v-if="dingtalkStatus === 'pending'" class="status-text">正在向钉钉确认身份，不是在建工作区</p>
            <p v-if="dingtalkError" class="error-text">{{ dingtalkError }}</p>
            <el-button
              v-if="dingtalkError.includes('连不上钉钉')"
              type="primary"
              class="refresh-btn"
              @click="activeTab = 'password'"
            >
              改用账号密码登录
            </el-button>
            <el-button text type="primary" class="refresh-btn" @click="startDingtalkScan">
              刷新二维码
            </el-button>
          </div>

          <form v-else class="bind-form" @submit.prevent="handleBind">
            <p class="bind-hint">请使用系统账号完成钉钉绑定（需已开通「钉钉免登」）</p>
            <div class="field">
              <label>用户名</label>
              <el-input v-model="bindUsername" placeholder="系统用户名" size="large" />
            </div>
            <div class="field">
              <label>密码</label>
              <el-input
                v-model="bindPassword"
                type="password"
                show-password
                placeholder="系统密码"
                size="large"
              />
            </div>
            <el-button
              type="primary"
              native-type="submit"
              :loading="bindLoading"
              size="large"
              class="submit-btn"
            >
              绑定并登录
            </el-button>
            <el-button text type="primary" class="refresh-btn" @click="startDingtalkScan">
              重新扫码
            </el-button>
          </form>
        </div>

        <form v-show="activeTab === 'password'" class="password-pane" @submit.prevent="handleLogin">
          <div class="field">
            <label>用户名</label>
            <el-input v-model="username" placeholder="manager" size="large" />
          </div>
          <div class="field">
            <label>密码</label>
            <el-input
              v-model="password"
              type="password"
              show-password
              placeholder="请输入初始密码"
              size="large"
            />
          </div>
          <el-button type="primary" native-type="submit" :loading="loading" size="large" class="submit-btn">
            登录
          </el-button>
        </form>
      </div>
      <p class="hint">钉钉扫码需已绑定账号并开通「钉钉免登」；首次密码登录须修改初始密码</p>
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
  max-width: 420px;
  text-align: center;
}

.logo-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: var(--wb-bg-active);
  color: var(--wb-accent);
  margin-bottom: 20px;
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
  margin: 0 0 32px;
  line-height: 1.6;
}

.form {
  background: #fff;
  border: 1px solid var(--wb-border);
  border-radius: 16px;
  padding: 20px 24px 28px;
  box-shadow: var(--wb-shadow-soft);
  text-align: left;
}

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 20px;
  background: var(--wb-bg-muted);
  border-radius: 10px;
  padding: 4px;
}

.tab {
  flex: 1;
  border: none;
  background: transparent;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--wb-text-secondary);
  cursor: pointer;
}

.tab.active {
  background: #fff;
  color: var(--wb-text-primary);
  box-shadow: var(--wb-shadow-soft);
}

.qr-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 320px;
}

.qr-box-wrap {
  position: relative;
  width: 280px;
  height: 280px;
}

.qr-box {
  width: 280px;
  height: 280px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.qr-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  text-align: center;
  font-size: 14px;
  line-height: 1.5;
  color: #e8eef6;
  background: rgba(12, 16, 22, 0.88);
}

.status-text {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--wb-text-secondary);
}

.error-text {
  margin: 8px 0 0;
  font-size: 13px;
  color: #c45656;
  text-align: center;
  line-height: 1.5;
}

.refresh-btn {
  margin-top: 8px;
}

.bind-hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--wb-text-secondary);
  line-height: 1.5;
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

.submit-btn {
  width: 100%;
  margin-top: 8px;
  height: 44px;
  font-size: 15px;
}

.hint {
  margin-top: 20px;
  font-size: 12px;
  color: var(--wb-text-muted);
  line-height: 1.5;
}

.auth-page--native {
  background:
    radial-gradient(1200px 500px at 50% -10%, rgba(61, 179, 112, 0.18), transparent 55%),
    #12151c;
}
.auth-page--native .title,
.auth-page--native .desc {
  color: #eef2f6;
}
.auth-page--native .desc {
  color: #b7c0cc;
}
.auth-page--native .logo-mark {
  background: rgba(61, 179, 112, 0.16);
  color: #7ee0a8;
}
.auth-page--native .hint {
  color: #8b95a5;
}
.auth-page--native .form {
  background: #1b2030;
  border-color: #2a3348;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
}
.auth-page--native .tabs {
  background: #12151c;
}
.auth-page--native .tab {
  color: #8b95a5;
}
.auth-page--native .tab.active {
  background: #252c40;
  color: #eef2f6;
  box-shadow: none;
}
.auth-page--native .field label,
.auth-page--native .bind-hint,
.auth-page--native .status-text {
  color: #b7c0cc;
}
.auth-page--native :deep(.el-input__wrapper) {
  background: #12151c;
  box-shadow: 0 0 0 1px #2a3348 inset;
}
.auth-page--native :deep(.el-input__inner) {
  color: #eef2f6;
}
</style>
