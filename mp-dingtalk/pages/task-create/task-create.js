const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

Page({
  data: {
    mode: 'self',
    projects: [],
    projectNames: [],
    projectIndex: 0,
    title: '',
    dueDate: '',
    members: [],
    memberNames: [],
    ownerIndex: 0,
    assistQuery: '',
    filteredMembers: [],
    denyMessage: '',
  },
  onLoad(query) {
    this.setData({ mode: query.mode === 'delegated' ? 'delegated' : 'self' })
  },
  async onShow() {
    const user = await ensureLogin()
    if (!user) {
      this.setData({ denyMessage: getApp().globalData.denyMessage })
      return
    }
    if (this.data.mode === 'delegated' && !(user.permissions || []).includes('task.assign')) {
      this.setData({ denyMessage: '无交办权限' })
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
      if (projects[0]) await this.loadMembers(projects[0].id)
    } catch (err) {
      this.setData({ denyMessage: err.message || '加载失败' })
    }
  },
  async loadMembers(projectId) {
    const rows = await request(`/projects/${projectId}/members`)
    const members = rows.map((m) => ({
      id: m.user.id,
      name: m.user.name,
      selected: false,
    }))
    this.setData({
      members,
      memberNames: members.map((m) => m.name),
      ownerIndex: 0,
      assistQuery: '',
    })
    this.refreshFilteredMembers(members, '')
  },
  refreshFilteredMembers(members, assistQuery) {
    const list = members || this.data.members
    const q = (assistQuery != null ? assistQuery : this.data.assistQuery || '').trim().toLowerCase()
    const filteredMembers = !q
      ? list
      : list.filter((m) => String(m.name || '').toLowerCase().includes(q))
    this.setData({ filteredMembers })
  },
  async onProject(e) {
    const projectIndex = Number(e.detail.value)
    this.setData({ projectIndex })
    const p = this.data.projects[projectIndex]
    if (p) await this.loadMembers(p.id)
  },
  onTitle(e) { this.setData({ title: e.detail.value }) },
  onOwner(e) { this.setData({ ownerIndex: Number(e.detail.value) }) },
  onAssistQuery(e) {
    const assistQuery = e.detail.value || ''
    this.setData({ assistQuery })
    this.refreshFilteredMembers(this.data.members, assistQuery)
  },
  pickDate() {
    dd.datePicker({
      format: 'yyyy-MM-dd',
      success: (res) => this.setData({ dueDate: res.date }),
    })
  },
  toggleAssist(e) {
    const id = Number(e.currentTarget.dataset.id)
    const members = this.data.members.map((m) =>
      m.id === id ? { ...m, selected: !m.selected } : m
    )
    this.setData({ members })
    this.refreshFilteredMembers(members, this.data.assistQuery)
  },
  async submit() {
    const project = this.data.projects[this.data.projectIndex]
    if (!project || !this.data.title.trim()) {
      dd.alert({ content: '请填写项目和标题' })
      return
    }
    const assistant_ids = this.data.members.filter((m) => m.selected).map((m) => m.id)
    const payload = {
      project_id: project.id,
      title: this.data.title.trim(),
      due_date: this.data.dueDate || null,
      assignment_type: this.data.mode,
      assistant_ids,
    }
    if (this.data.mode === 'delegated') {
      const owner = this.data.members[this.data.ownerIndex]
      if (!owner) {
        dd.alert({ content: '请选择负责人' })
        return
      }
      payload.assignee_id = owner.id
      payload.assistant_ids = assistant_ids.filter((id) => id !== owner.id)
    }
    try {
      await request('/tasks', 'POST', payload)
      dd.showToast({ content: '已创建' })
      dd.navigateBack()
    } catch (err) {
      dd.alert({ content: err.message || '创建失败' })
    }
  },
})
