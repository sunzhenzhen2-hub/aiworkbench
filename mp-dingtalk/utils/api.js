const app = getApp()

function request(path, method = 'GET', data = {}) {
  return new Promise((resolve, reject) => {
    dd.httpRequest({
      url: `${app.globalData.apiBase}${path}`,
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : '',
      },
      data: method === 'GET' ? data : JSON.stringify(data),
      success: (res) => {
        const body = res.data
        const status = res.status || res.statusCode
        if (status >= 200 && status < 300) {
          resolve(body)
          return
        }
        const detail = (body && (body.detail || body.message)) || `请求失败 ${status}`
        const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
        err.status = status
        err.detail = detail
        reject(err)
      },
      fail: (err) => reject(err || new Error('网络错误')),
    })
  })
}

function saveToken(token) {
  app.globalData.token = token || ''
  if (token) {
    dd.setStorageSync({ key: 'token', data: token })
  } else {
    dd.removeStorageSync({ key: 'token' })
  }
}

module.exports = { request, saveToken }
