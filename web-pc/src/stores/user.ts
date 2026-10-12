import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../api'

export interface UserInfo {
  id: number
  name: string
  username: string
  roles: string[]
  permissions: string[]
  must_change_password: boolean
}

export const useUserStore = defineStore('user', () => {
  const user = ref<UserInfo | null>(null)
  const token = ref(localStorage.getItem('token') || '')

  async function login(username: string, password: string) {
    const { data } = await api.post('/auth/login', { username, password })
    return applyToken(data.access_token, data.must_change_password as boolean)
  }

  /** 钉钉扫码 / 绑定成功后写入 JWT（免登不强制改密） */
  async function applyDingTalkLogin(accessToken: string, _mustChangePassword?: boolean) {
    await applyToken(accessToken, false)
    return false
  }

  async function applyToken(accessToken: string, mustChangePassword: boolean) {
    token.value = accessToken
    localStorage.setItem('token', accessToken)
    await fetchMe()
    return mustChangePassword || Boolean(user.value?.must_change_password)
  }

  async function fetchMe() {
    const { data } = await api.get('/auth/me')
    user.value = data
    return data
  }

  async function logout() {
    const accessToken = token.value
    try {
      await fetch(`${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/dsh/session-end`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      })
    } catch {
      // 助手没开着也不影响退出工作台
    }
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    try {
      await fetch('/logout?stay=1', { credentials: 'same-origin', redirect: 'manual' })
    } catch {
      // 浏览器票清不掉也不挡工作台退出
    }
  }

  function hasPermission(code: string) {
    return user.value?.permissions.includes(code) ?? false
  }

  function hasRole(code: string) {
    return user.value?.roles.includes(code) ?? false
  }

  /** 可改该项目：管理员，或本人是项目负责人 */
  function canEditOwnedProject(ownerId: number | null | undefined) {
    if (!hasPermission('project.edit')) return false
    if (hasRole('admin') || hasRole('dept_head')) return true
    return ownerId != null && user.value?.id === ownerId
  }

  return { user, token, login, applyDingTalkLogin, fetchMe, logout, hasPermission, hasRole, canEditOwnedProject }
})
