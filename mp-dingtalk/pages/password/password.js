const { request } = require('../../utils/api')

Page({
  data: { oldPassword: '', newPassword: '' },
  onOld(e) { this.setData({ oldPassword: e.detail.value }) },
  onNew(e) { this.setData({ newPassword: e.detail.value }) },
  async submit() {
    try {
      await request('/auth/change-password', 'POST', {
        old_password: this.data.oldPassword,
        new_password: this.data.newPassword,
      })
      dd.showToast({ content: '密码已更新' })
      dd.switchTab({ url: '/pages/index/index' })
    } catch (err) {
      dd.alert({ content: err.message || '修改失败' })
    }
  },
})
