import axios from 'axios'

const TOKEN_KEY = 'wb_dt_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function saveToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/aiworkbench/api',
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('；')
          : detail && typeof detail === 'object' && typeof detail.message === 'string'
            ? detail.message
            : err.message || '请求失败'
    return Promise.reject(new Error(message))
  },
)

export async function request<T = unknown>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'GET',
  data?: Record<string, unknown>,
): Promise<T> {
  const res = await api.request<T>({
    url: path,
    method,
    params: method === 'GET' ? data : undefined,
    data: method !== 'GET' ? data : undefined,
  })
  return res.data
}

export default api
