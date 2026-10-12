import { defineStore } from 'pinia'
import { ref } from 'vue'
import { clearToken, getToken, request, saveToken } from '@/utils/api'
import { requestDingTalkAuthCode } from '@/utils/dingtalkAuth'

export type MeUser = {
  id: number
  username: string
  name: string
  job_number?: string
  avatar_url?: string | null
  email?: string | null
  roles?: string[]
  permissions?: string[]
  dingtalk_bound?: boolean
  must_change_password?: boolean
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<MeUser | null>(null)
  const bindTicket = ref('')
  const denyMessage = ref('')
  const ready = ref(false)
  const loading = ref(false)

  async function fetchMe(): Promise<MeUser | null> {
    if (!getToken()) {
      user.value = null
      return null
    }
    try {
      const me = await request<MeUser>('/auth/me')
      user.value = me
      denyMessage.value = ''
      return me
    } catch {
      clearToken()
      user.value = null
      return null
    }
  }

  /** 钉钉 JSAPI 免登 → POST /auth/dingtalk */
  async function loginWithDingTalk(): Promise<'ok' | 'bind' | 'password' | 'fail'> {
    loading.value = true
    denyMessage.value = ''
    try {
      const code = await requestDingTalkAuthCode()
      const body = await request<{
        status: string
        access_token?: string | null
        bind_ticket?: string | null
        must_change_password?: boolean
        message?: string | null
      }>('/auth/dingtalk', 'POST', { auth_code: code })

      if (body.status === 'need_bind' && body.bind_ticket) {
        bindTicket.value = body.bind_ticket
        return 'bind'
      }
      if (body.access_token) {
        saveToken(body.access_token)
        await fetchMe()
        // 钉钉免登不强制改密
        return 'ok'
      }
      denyMessage.value = body.message || '登录失败'
      return 'fail'
    } catch (err) {
      denyMessage.value = err instanceof Error ? err.message : '登录失败'
      return 'fail'
    } finally {
      loading.value = false
      ready.value = true
    }
  }

  async function ensureLogin(): Promise<MeUser | null> {
    if (user.value) return user.value
    if (getToken()) {
      const me = await fetchMe()
      if (me) {
        ready.value = true
        return me
      }
    }
    const result = await loginWithDingTalk()
    if (result === 'ok' || result === 'password') return user.value
    return null
  }

  async function logout() {
    const accessToken = getToken()
    try {
      await fetch('/aiworkbench/api/dsh/session-end', {
        method: 'POST',
        credentials: 'same-origin',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      })
    } catch {
      // 助手没开着也不影响退出工作台
    }
    clearToken()
    user.value = null
    bindTicket.value = ''
  }

  return {
    user,
    bindTicket,
    denyMessage,
    ready,
    loading,
    fetchMe,
    loginWithDingTalk,
    ensureLogin,
    logout,
  }
})
