const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

Page({
  data: { user: {}, stats: {}, denyMessage: '' },
  async onShow() {
    const user = await ensureLogin()
    const app = getApp()
    if (!user) {
      this.setData({ denyMessage: app.globalData.denyMessage, user: {}, stats: {} })
      return
    }
    try {
      const stats = await request('/projects/overview/stats')
      this.setData({ user, stats, denyMessage: '' })
    } catch (err) {
      this.setData({ user, denyMessage: err.message || '加载失败' })
    }
  },
  goTodo() { dd.switchTab({ url: '/pages/todo/todo' }) },
  goReport() { dd.switchTab({ url: '/pages/report/report' }) },
  goBlocker() { dd.navigateTo({ url: '/pages/blocker/blocker' }) },
})
