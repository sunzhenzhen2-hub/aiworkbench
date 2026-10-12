import axios from 'axios'

const baseUrl = import.meta.env.BASE_URL.replace(/\/$/, '')

const api = axios.create({
  baseURL: `${baseUrl}/api`,
  timeout: 15000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      const path = window.location.pathname
      const onLogin = /\/login\/?$/.test(path)
      if (!onLogin) {
        window.location.href = `${import.meta.env.BASE_URL}login`
      }
    }
    return Promise.reject(err)
  },
)

export default api
