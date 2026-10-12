const { request, saveToken } = require('../../utils/api')

Page({
  data: { username: '', password: '' },
  onUser(e) { this.setData({ username: e.detail.value }) },
  onPass(e) { this.setData({ password: e.detail.value }) },
  async submit() {
    const app = getApp()
    if (!app.globalData.bindTicket) {
      dd.alert({ content: '绑定已过期，请从首页重新打开' })
      return
    }
    try {
      const body = await request('/auth/dingtalk/bind', 'POST', {
        bind_ticket: app.globalData.bindTicket,
        username: this.data.username,
        password: this.data.password,
      })
      saveToken(body.access_token)
      app.globalData.bindTicket = ''
      if (body.must_change_password) {
        dd.redirectTo({ url: '/pages/password/password' })
        return
      }
      dd.switchTab({ url: '/pages/index/index' })
    } catch (err) {
      dd.alert({ content: err.message || '绑定失败，请确认管理员已开通钉钉免登' })
    }
  },
})
