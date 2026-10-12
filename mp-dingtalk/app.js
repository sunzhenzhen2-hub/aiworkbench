// 钉钉小程序入口。密钥只在后端，这里只放企业标识和接口地址。
App({
  globalData: {
    apiBase: 'http://30.251.255.64/workbench/api',
    corpId: 'dingc4aa4304d998d0d2f5bf40eda33b7ba0',
    token: '',
    bindTicket: '',
    user: null,
    denyMessage: '',
  },
  onLaunch() {
    const stored = dd.getStorageSync({ key: 'token' })
    if (stored && stored.data) this.globalData.token = stored.data
  },
})
