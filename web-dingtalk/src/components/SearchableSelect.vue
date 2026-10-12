<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'

export type SearchableOption = { value: number | string; label: string }

const props = withDefaults(
  defineProps<{
    modelValue: number | string | null
    options: SearchableOption[]
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
  }>(),
  {
    placeholder: '输入关键字搜索',
    clearable: true,
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: number | string | null]
  change: [value: number | string | null]
}>()

const open = ref(false)
const query = ref('')
const rootEl = ref<HTMLElement | null>(null)

const selectedLabel = computed(() => {
  if (props.modelValue == null || props.modelValue === '') return ''
  const hit = props.options.find((o) => String(o.value) === String(props.modelValue))
  return hit?.label || ''
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.options
  return props.options.filter((o) => (o.label || '').toLowerCase().includes(q))
})

watch(
  () => props.modelValue,
  () => {
    if (!open.value) query.value = ''
  },
)

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) query.value = ''
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'E',location:'SearchableSelect.vue:toggle',message:'dropdown toggled',data:{open:open.value,optionCount:props.options.length,hasValue:props.modelValue!=null&&props.modelValue!==''},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
}

function pick(value: number | string) {
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'A',location:'SearchableSelect.vue:pick',message:'option picked',data:{valueType:typeof value,queryLen:query.value.length,filteredCount:filtered.value.length},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
  emit('update:modelValue', value)
  emit('change', value)
  open.value = false
  query.value = ''
}

function clear(e: Event) {
  e.stopPropagation()
  if (props.disabled) return
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'A',location:'SearchableSelect.vue:clear',message:'cleared selection',data:{wasOpen:open.value,prevHadValue:props.modelValue!=null&&props.modelValue!==''},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
  emit('update:modelValue', null)
  emit('change', null)
  query.value = ''
}

watch(query, (q) => {
  if (!open.value) return
  // #region agent log
  fetch('http://127.0.0.1:7460/ingest/fddc0a69-d1b7-4204-abb0-abc670f78360',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9cbb7d'},body:JSON.stringify({sessionId:'9cbb7d',runId:'adv-global',hypothesisId:'C',location:'SearchableSelect.vue:query',message:'filter query changed',data:{queryLen:(q||'').length,queryPreview:String(q||'').slice(0,20),hit:filtered.value.length,total:props.options.length},timestamp:Date.now()})}).catch(()=>{})
  // #endregion
})


function onDocClick(e: MouseEvent) {
  const el = rootEl.value
  if (!el) return
  if (e.target instanceof Node && !el.contains(e.target)) {
    open.value = false
    query.value = ''
  }
}

watch(open, (v) => {
  if (v) {
    document.addEventListener('click', onDocClick, true)
  } else {
    document.removeEventListener('click', onDocClick, true)
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick, true)
})
</script>

<template>
  <div ref="rootEl" class="ss" :class="{ disabled }">
    <button
      type="button"
      class="input ss-trigger"
      :disabled="disabled"
      :aria-expanded="open"
      @click="toggle"
    >
      <span class="ss-text" :class="{ placeholder: !selectedLabel }">
        {{ selectedLabel || placeholder }}
      </span>
      <span v-if="clearable && selectedLabel" class="ss-clear" @click="clear">×</span>
      <span class="ss-arrow">{{ open ? '▲' : '▼' }}</span>
    </button>
    <div v-if="open" class="ss-panel">
      <input
        v-model="query"
        class="input ss-search"
        type="search"
        :placeholder="placeholder"
        @click.stop
      />
      <div class="ss-list">
        <button
          v-for="o in filtered"
          :key="String(o.value)"
          type="button"
          class="ss-option"
          :class="{ active: String(o.value) === String(modelValue) }"
          @click="pick(o.value)"
        >
          {{ o.label }}
        </button>
        <p v-if="!filtered.length" class="ss-empty">无匹配项</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ss {
  position: relative;
}
.ss.disabled {
  opacity: 0.6;
  pointer-events: none;
}
.ss-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  text-align: left;
  cursor: pointer;
  width: 100%;
}
.ss-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ss-text.placeholder {
  color: var(--wb-muted);
}
.ss-clear {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  line-height: 20px;
  text-align: center;
  border-radius: 50%;
  color: var(--wb-muted);
  font-size: 16px;
}
.ss-arrow {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--wb-muted);
}
.ss-panel {
  margin-top: 6px;
  border: 1px solid var(--wb-border);
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
}
.ss-search {
  border: none;
  border-bottom: 1px solid var(--wb-border);
  border-radius: 0;
  width: 100%;
}
.ss-list {
  max-height: 220px;
  overflow-y: auto;
}
.ss-option {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  padding: 12px 14px;
  border-bottom: 1px solid var(--wb-border);
  font-size: 14px;
  color: var(--wb-text);
  cursor: pointer;
}
.ss-option:last-child {
  border-bottom: none;
}
.ss-option.active {
  background: var(--wb-mint-bg);
  color: var(--wb-mint-text);
}
.ss-empty {
  margin: 0;
  padding: 14px;
  color: var(--wb-muted);
  font-size: 13px;
}
</style>
