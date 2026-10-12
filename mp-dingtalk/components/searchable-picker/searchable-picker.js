Component({
  data: {
    open: false,
    query: '',
    label: '',
    filtered: [],
  },
  props: {
    names: [],
    index: 0,
    placeholder: '输入关键字搜索',
    onChange: () => {},
  },
  didMount() {
    this.syncLabel()
    this.applyFilter('')
  },
  didUpdate(prev) {
    if (prev.names !== this.props.names || prev.index !== this.props.index) {
      this.syncLabel()
      if (!this.data.open) this.applyFilter('')
    }
  },
  methods: {
    syncLabel() {
      const names = this.props.names || []
      const index = Number(this.props.index) || 0
      this.setData({ label: names[index] || '' })
    },
    applyFilter(query) {
      const names = this.props.names || []
      const q = (query || '').trim().toLowerCase()
      const filtered = []
      names.forEach((name, index) => {
        if (!q || String(name || '').toLowerCase().includes(q)) {
          filtered.push({ name, index })
        }
      })
      this.setData({ filtered, query: query || '' })
    },
    onToggle() {
      const open = !this.data.open
      this.setData({ open })
      if (open) this.applyFilter('')
    },
    onQuery(e) {
      this.applyFilter(e.detail.value || '')
    },
    onPick(e) {
      const index = Number(e.currentTarget.dataset.index)
      if (!Number.isFinite(index)) return
      this.setData({ open: false, query: '' })
      if (typeof this.props.onChange === 'function') {
        this.props.onChange({ detail: { value: index } })
      }
    },
  },
})
