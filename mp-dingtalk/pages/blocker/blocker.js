const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

Page({
  data: {
    projects: [],
    projectNames: [],
    projectIndex: 0,
    description: '',
    responsible_party: '',
    next_action: '',
    next_follow_up: '',
    denyMessage: '',
  },
  async onShow() {
    const user = await ensureLogin()
    if (!user) {
      this.setData({ denyMessage: getApp().globalData.denyMessage })
      return
    }
    try {
      const data = await request('/personal/workspace')
      const projects = data.my_projects || []
      this.setData({
        projects,
        projectNames: projects.map((p) => p.name),
        denyMessage: '',
      })
    } catch (err) {
      this.setData({ denyMessage: err.message || '加载失败' })
    }
  },
  onProject(e) { this.setData({ projectIndex: Number(e.detail.value) }) },
  onDesc(e) { this.setData({ description: e.detail.value }) },
  onParty(e) { this.setData({ responsible_party: e.detail.value }) },
  onNext(e) { this.setData({ next_action: e.detail.value }) },
  pickDate() {
    dd.datePicker({
      format: 'yyyy-MM-dd',
      success: (res) => this.setData({ next_follow_up: res.date }),
    })
  },
  async submit() {
    const project = this.data.projects[this.data.projectIndex]
    if (!project) {
      dd.alert({ content: '请选择项目' })
      return
    }
    if (!this.data.description || !this.data.next_action || !this.data.next_follow_up) {
      dd.alert({ content: '请填写描述、下一步和跟进日期' })
      return
    }
    try {
      await request(`/blockers/project/${project.id}`, 'POST', {
        description: this.data.description,
        responsible_party: this.data.responsible_party || '待确认',
        party_type: 'internal',
        actions_taken: '钉钉端登记',
        next_action: this.data.next_action,
        next_follow_up: this.data.next_follow_up,
      })
      dd.showToast({ content: '卡点已登记' })
      dd.navigateBack()
    } catch (err) {
      dd.alert({ content: err.message || '提交失败' })
    }
  },
})
