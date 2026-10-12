const { ensureLogin } = require('../../utils/auth')
const { request } = require('../../utils/api')

function mondayOfThisWeek() {
  const d = new Date()
  const day = d.getDay() || 7
  d.setDate(d.getDate() - day + 1)
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const dayNum = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${dayNum}`
}

function nextFriday(fromMonday) {
  const d = new Date(fromMonday)
  d.setDate(d.getDate() + 4)
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const dayNum = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${dayNum}`
}

Page({
  data: {
    projects: [],
    projectNames: [],
    projectIndex: 0,
    result_text: '',
    stuck: false,
    stuck_detail: '',
    next_goal: '',
    next_due_date: '',
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
      const period_start = mondayOfThisWeek()
      this.setData({
        projects,
        projectNames: projects.map((p) => p.name),
        next_due_date: nextFriday(period_start),
        denyMessage: '',
      })
      // 尝试预填
      try {
        const prefill = await request('/progress-reports/prefill', 'GET', {
          period_type: 'week',
          period_start,
        })
        if (prefill && prefill.items && prefill.items[0]) {
          const it = prefill.items[0]
          const idx = Math.max(0, projects.findIndex((p) => p.id === it.project_id))
          this.setData({
            projectIndex: idx >= 0 ? idx : 0,
            result_text: it.result_text || '',
            next_goal: it.next_goal || '',
            next_due_date: it.next_due_date || nextFriday(period_start),
            stuck: it.stuck_status && it.stuck_status !== 'none',
            stuck_detail: it.stuck_detail || '',
          })
        }
      } catch (ignored) {
        /* 预填失败不影响手写 */
      }
    } catch (err) {
      this.setData({ denyMessage: err.message || '加载失败' })
    }
  },
  onProject(e) { this.setData({ projectIndex: Number(e.detail.value) }) },
  onResult(e) { this.setData({ result_text: e.detail.value }) },
  onStuckToggle() { this.setData({ stuck: !this.data.stuck }) },
  onStuckDetail(e) { this.setData({ stuck_detail: e.detail.value }) },
  onGoal(e) { this.setData({ next_goal: e.detail.value }) },
  pickDue() {
    dd.datePicker({
      format: 'yyyy-MM-dd',
      success: (res) => this.setData({ next_due_date: res.date }),
    })
  },
  async submit() {
    const project = this.data.projects[this.data.projectIndex]
    if (!project) {
      dd.alert({ content: '请选择项目' })
      return
    }
    if (!(this.data.result_text || '').trim() || !(this.data.next_goal || '').trim() || !this.data.next_due_date) {
      dd.alert({ content: '请填写：做成了什么、下一步、完成日期' })
      return
    }
    const period_start = mondayOfThisWeek()
    const item = {
      project_id: project.id,
      task_title: `${project.name}-本周进展`,
      no_action: false,
      result_text: this.data.result_text.trim(),
      stuck_status: this.data.stuck ? 'blocker' : 'none',
      stuck_detail: this.data.stuck ? (this.data.stuck_detail || '有卡点') : '',
      stuck_next_action: this.data.stuck ? '跟进卡点' : '',
      stuck_follow_up: this.data.stuck ? this.data.next_due_date : null,
      next_goal: this.data.next_goal.trim(),
      next_due_date: this.data.next_due_date,
      risk_level: 'normal',
      sort_order: 0,
    }
    try {
      await request('/progress-reports', 'POST', {
        period_type: 'week',
        period_start,
        theme: item.task_title,
        items: [item],
        submit: true,
      })
      dd.showToast({ content: '周报已提交' })
      this.setData({ result_text: '', stuck: false, stuck_detail: '', next_goal: '' })
    } catch (err) {
      let msg = err.message || '提交失败'
      if (err.detail && typeof err.detail === 'object' && err.detail.hard_errors) {
        msg = (err.detail.hard_errors || []).join('；') || msg
      }
      dd.alert({ content: msg })
    }
  },
})
