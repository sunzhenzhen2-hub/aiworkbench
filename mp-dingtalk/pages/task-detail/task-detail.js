const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

const STATUS_TEXT = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
}
const EVENT_LABEL = {
  created: '创建任务',
  owner_changed: '更换负责人',
  assistant_added: '添加辅助人',
  assistant_removed: '移除辅助人',
  status_changed: '状态变更',
  result_updated: '更新内容',
}

Page({
  data: {
    taskId: 0,
    task: null,
    resultText: '',
    events: [],
    denyMessage: '',
    statusText: STATUS_TEXT,
    canManageAssist: false,
    members: [],
    memberNames: [],
    assistIndex: 0,
  },
  async onLoad(query) {
    this.setData({ taskId: Number(query.id || 0) })
  },
  async onShow() {
    const user = await ensureLogin()
    if (!user) {
      this.setData({ denyMessage: getApp().globalData.denyMessage })
      return
    }
    await this.load(user)
  },
  async load(user) {
    try {
      const task = await request(`/tasks/${this.data.taskId}`)
      const events = (task.events || []).map((e) => ({
        ...e,
        label: EVENT_LABEL[e.action] || e.action,
      }))
      const canManageAssist =
        (task.assignee && task.assignee.id === user.id) ||
        (user.permissions || []).includes('task.assign')
      let members = []
      let memberNames = []
      try {
        members = await request(`/projects/${task.project_id}/members`)
        memberNames = members.map((m) => m.user.name)
      } catch (e) {
        members = []
      }
      this.setData({
        task,
        resultText: task.result_text || '',
        events,
        canManageAssist,
        members,
        memberNames,
        denyMessage: '',
      })
    } catch (err) {
      this.setData({ denyMessage: err.message || '加载失败', task: null })
    }
  },
  onResult(e) { this.setData({ resultText: e.detail.value }) },
  onAssistPick(e) { this.setData({ assistIndex: Number(e.detail.value) }) },
  async saveResult() {
    try {
      const task = await request(`/tasks/${this.data.taskId}`, 'POST', {
        result_text: this.data.resultText,
      })
      this.setData({ task, resultText: task.result_text || '' })
      dd.showToast({ content: '已保存' })
    } catch (err) {
      dd.alert({ content: err.message || '保存失败' })
    }
  },
  async setStatus(e) {
    const status = e.currentTarget.dataset.status
    if (status === 'completed' && !(this.data.resultText || '').trim()) {
      dd.alert({ content: '完成前请填写结果说明' })
      return
    }
    try {
      const task = await request(`/tasks/${this.data.taskId}`, 'POST', {
        status,
        result_text: this.data.resultText,
      })
      const user = getApp().globalData.user
      await this.load(user || (await ensureLogin()))
      dd.showToast({ content: '状态已更新' })
      this.setData({ task })
    } catch (err) {
      dd.alert({ content: err.message || '更新失败' })
    }
  },
  async saveAndBack() {
    await this.saveResult()
    dd.navigateBack()
  },
  async addAssist() {
    const m = this.data.members[this.data.assistIndex]
    if (!m) return
    try {
      await request(`/tasks/${this.data.taskId}/assistants`, 'POST', {
        user_id: m.user.id,
      })
      dd.showToast({ content: '已添加' })
      const user = getApp().globalData.user || (await ensureLogin())
      await this.load(user)
    } catch (err) {
      dd.alert({ content: err.message || '添加失败' })
    }
  },
})
