const { request, saveToken } = require('./api')

const UNAUTH = '请联系管理员在电脑端「系统管理」中开通钉钉免登。'

function getAuthCode() {
  return new Promise((resolve, reject) => {
    dd.getAuthCode({
      success: (res) => resolve(res.authCode || res.code),
      fail: (err) => reject(err || new Error('无法获取钉钉登录码')),
    })
  })
}

function go(url) {
  dd.redirectTo({ url })
}

async function loadMe() {
  const me = await request('/auth/me')
  const app = getApp()
  app.globalData.user = me
  if (me.must_change_password) {
    go('/pages/password/password')
    return null
  }
  return me
}

async function ensureLogin() {
  const app = getApp()
  app.globalData.denyMessage = ''

  if (app.globalData.token) {
    try {
      return await loadMe()
    } catch (ignored) {
      saveToken('')
    }
  }

  try {
    const code = await getAuthCode()
    const body = await request('/auth/dingtalk', 'POST', { auth_code: code })
    if (body.status === 'need_bind') {
      app.globalData.bindTicket = body.bind_ticket
      go('/pages/bind/bind')
      return null
    }
    saveToken(body.access_token)
    return await loadMe()
  } catch (err) {
    const msg = (err && (err.detail || err.message)) || UNAUTH
    app.globalData.denyMessage = typeof msg === 'string' ? msg : UNAUTH
    return null
  }
}

module.exports = { ensureLogin, UNAUTH, getAuthCode }
