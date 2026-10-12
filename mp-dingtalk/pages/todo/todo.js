const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

const STATUS_TEXT = {
  following: '跟进中',
  pending_decision: '待决策',
  escalated: '已升级',
  resolved: '已解决',
}
const TASK_STATUS = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}

Page({
  data: {
    tasks: [],
    blockers: [],
    denyMessage: '',
    canAssign: false,
  },
  async onShow() {
    const user = await ensureLogin()
    if (!user) {
      this.setData({ denyMessage: getApp().globalData.denyMessage, tasks: [], blockers: [] })
      return
    }
    this.setData({ canAssign: (user.permissions || []).includes('task.assign') })
    await this.load()
  },
  async load() {
    try {
      const data = await request('/personal/workspace')
      const tasks = (data.my_tasks || []).map((t) => ({
        ...t,
        typeLabel: t.assignment_type === 'delegated' ? '交办' : '自建',
        roleLabel: t.my_role === 'owner' ? '负责人' : t.my_role === 'assistant' ? '辅助' : '',
        statusLabel: TASK_STATUS[t.status] || t.status,
      }))
      const blockers = (data.my_blockers || []).map((b) => ({
        ...b,
        statusLabel: STATUS_TEXT[b.status] || b.status,
        statusCode: b.status,
      }))
      this.setData({ tasks, blockers, denyMessage: '' })
    } catch (err) {
      this.setData({ denyMessage: err.message || '加载失败' })
    }
  },
  goDetail(e) {
    const id = e.currentTarget.dataset.id
    dd.navigateTo({ url: `/pages/task-detail/task-detail?id=${id}` })
  },
  goCreateSelf() {
    dd.navigateTo({ url: '/pages/task-create/task-create?mode=self' })
  },
  goCreateDelegated() {
    dd.navigateTo({ url: '/pages/task-create/task-create?mode=delegated' })
  },
  goBlocker() { dd.navigateTo({ url: '/pages/blocker/blocker' }) },
  async setBlockerStatus(e) {
    const { id, status } = e.currentTarget.dataset
    try {
      await request(`/blockers/${id}`, 'POST', { status })
      dd.showToast({ content: '卡点已更新' })
      await this.load()
    } catch (err) {
      dd.alert({ content: err.message || '更新失败' })
    }
  },
})
