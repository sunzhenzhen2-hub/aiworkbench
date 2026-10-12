<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import { isAgentNativeProduct } from '../productLine'
import AgentNativeHint from '../components/AgentNativeHint.vue'

interface TagItem {
  id: number
  name: string
  source: string
}

interface Solution {
  id: number
  title: string
  summary: string
  original_filename: string
  mime_type: string
  file_size: number
  uploader_id: number
  uploader_name: string
  status: string
  error_message: string
  tags: TagItem[]
  created_at: string
  updated_at: string
}

interface TagStat {
  name: string
  count: number
}

const userStore = useUserStore()
const router = useRouter()
const agentNative = isAgentNativeProduct()
const loading = ref(false)
const uploading = ref(false)
const items = ref<Solution[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const selectedTag = ref('')
const tagStats = ref<TagStat[]>([])
const askText = ref('')
const askLoading = ref(false)
const askResult = ref<{
  keywords?: string[]
  suggested_tags?: string[]
  rationale?: string
  items?: Array<{ id: number; title: string; summary: string; tags: string[] }>
} | null>(null)

const drawerVisible = ref(false)
const current = ref<Solution | null>(null)
const editTitle = ref('')
const editTags = ref<string[]>([])
const saving = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null

const canUpload = computed(() => userStore.hasPermission('solution.upload'))

function statusLabel(status: string) {
  if (status === 'ready') return { text: '已就绪', type: 'success' as const }
  if (status === 'processing' || status === 'pending') return { text: '整理中', type: 'warning' as const }
  if (status === 'failed') return { text: '失败', type: 'danger' as const }
  return { text: status, type: 'info' as const }
}

function formatSize(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(iso: string) {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(0, 16)
}

function canManage(row: Solution) {
  if (!canUpload.value) return false
  return row.uploader_id === userStore.user?.id || userStore.hasPermission('admin.user')
}

async function loadTags() {
  const { data } = await api.get('/solutions/tags')
  tagStats.value = data
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/solutions', {
      params: {
        q: search.value || undefined,
        tag: selectedTag.value || undefined,
        page: page.value,
        page_size: pageSize.value,
      },
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function askAi() {
  if (agentNative) {
    router.push('/dsh')
    return
  }
  const text = askText.value.trim()
  if (!text) {
    ElMessage.warning('写一句你想找什么方案')
    return
  }
  askLoading.value = true
  try {
    const { data } = await api.post('/ai/solution/search', { text })
    askResult.value = data
    if (data.keywords?.length) {
      search.value = data.keywords.slice(0, 3).join(' ')
      page.value = 1
      await load()
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '检索失败')
  } finally {
    askLoading.value = false
  }
}

function hasProcessing() {
  return items.value.some((i) => i.status === 'processing' || i.status === 'pending')
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    if (!hasProcessing()) {
      stopPoll()
      return
    }
    await load()
    await loadTags()
    if (current.value) {
      const found = items.value.find((i) => i.id === current.value!.id)
      if (found) {
        current.value = found
        editTitle.value = found.title
        editTags.value = found.tags.map((t) => t.name)
      } else {
        try {
          const { data } = await api.get(`/solutions/${current.value.id}`)
          current.value = data
          editTitle.value = data.title
          editTags.value = data.tags.map((t: TagItem) => t.name)
        } catch {
          /* ignore */
        }
      }
    }
  }, 3000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(
  () => items.value.map((i) => i.status).join(','),
  () => {
    if (hasProcessing()) startPoll()
  },
)

async function onSearch() {
  page.value = 1
  await load()
}

function toggleTag(name: string) {
  selectedTag.value = selectedTag.value === name ? '' : name
  page.value = 1
  load()
}

async function customUpload(options: UploadRequestOptions) {
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', options.file as File)
    await api.post('/solutions/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    ElMessage.success('已上传，正在整理摘要与标签')
    options.onSuccess?.({})
    page.value = 1
    await load()
    await loadTags()
    startPoll()
  } catch (err: unknown) {
    const msg =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '上传失败'
    ElMessage.error(typeof msg === 'string' ? msg : '上传失败')
    options.onError?.({ name: 'UploadError', message: String(msg), status: 400, method: 'POST', url: '/solutions/upload' } as never)
  } finally {
    uploading.value = false
  }
}

function beforeUpload(file: File) {
  const okExt = /\.(pdf|docx|pptx|jpg|jpeg|png|webp|gif)$/i.test(file.name)
  if (!okExt) {
    ElMessage.error('仅支持 PDF、Word、PPT 与常见图片')
    return false
  }
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('单文件不能超过 20MB')
    return false
  }
  return true
}

async function openDetail(row: Solution) {
  const { data } = await api.get(`/solutions/${row.id}`)
  current.value = data
  editTitle.value = data.title
  editTags.value = data.tags.map((t: TagItem) => t.name)
  drawerVisible.value = true
}

async function openById(id: number) {
  await openDetail({ id } as Solution)
}

async function saveDetail() {
  if (!current.value) return
  saving.value = true
  try {
    const { data } = await api.patch(`/solutions/${current.value.id}`, {
      title: editTitle.value,
      tags: editTags.value,
    })
    current.value = data
    ElMessage.success('已保存')
    await load()
    await loadTags()
  } catch (err: unknown) {
    const msg =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '保存失败'
    ElMessage.error(typeof msg === 'string' ? msg : '保存失败')
  } finally {
    saving.value = false
  }
}

async function reprocess() {
  if (!current.value) return
  await api.post(`/solutions/${current.value.id}/reprocess`)
  ElMessage.success('已重新开始整理')
  const { data } = await api.get(`/solutions/${current.value.id}`)
  current.value = data
  await load()
  startPoll()
}

async function removeSolution(row: Solution) {
  await ElMessageBox.confirm(`确定删除「${row.title}」？文件也会一并删除。`, '删除确认', {
    type: 'warning',
  })
  await api.delete(`/solutions/${row.id}`)
  ElMessage.success('已删除')
  if (current.value?.id === row.id) drawerVisible.value = false
  await load()
  await loadTags()
}

async function download(row: Solution) {
  const res = await api.get(`/solutions/${row.id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = row.original_filename
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  await Promise.all([load(), loadTags()])
  if (hasProcessing()) startPoll()
})

onUnmounted(stopPoll)
</script>

<template>
  <div>
    <h1 class="wb-page-title">方案库</h1>
    <p class="wb-page-desc">上传历史方案，系统自动整理摘要与标签，方便大家学习查阅</p>

    <AgentNativeHint v-if="agentNative" tip="找案例请到「我的助手」里说。" />
    <div v-else class="ask-box">
      <el-input
        v-model="askText"
        clearable
        placeholder="用一句话问：比如「家庭医生开票怎么处理」"
        @keyup.enter="askAi"
      />
      <el-button type="primary" plain :loading="askLoading" @click="askAi">AI 帮找</el-button>
    </div>
    <div v-if="askResult" class="ask-result">
      <p v-if="askResult.rationale" class="muted">{{ askResult.rationale }}</p>
      <div v-if="askResult.suggested_tags?.length" class="tag-cloud" style="margin-bottom:8px">
        <button
          v-for="t in askResult.suggested_tags"
          :key="t"
          type="button"
          class="tag-chip"
          @click="toggleTag(t)"
        >{{ t }}</button>
      </div>
      <ul v-if="askResult.items?.length" class="ask-hits">
        <li v-for="it in askResult.items" :key="it.id">
          <button type="button" class="ask-hit" @click="openById(it.id)">{{ it.title }}</button>
          <span class="muted"> {{ it.summary }}</span>
        </li>
      </ul>
    </div>

    <div class="toolbar">
      <el-input
        v-model="search"
        clearable
        placeholder="搜索标题、摘要或文件名"
        style="width: 260px"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-button @click="onSearch">搜索</el-button>
      <el-upload
        v-if="canUpload"
        :show-file-list="false"
        :http-request="customUpload"
        :before-upload="beforeUpload"
        accept=".pdf,.docx,.pptx,.jpg,.jpeg,.png,.webp,.gif"
      >
        <el-button type="primary" :loading="uploading">上传方案</el-button>
      </el-upload>
    </div>

    <div v-if="tagStats.length" class="tag-cloud">
      <button
        type="button"
        class="tag-chip"
        :class="{ active: !selectedTag }"
        @click="toggleTag('')"
      >
        全部
      </button>
      <button
        v-for="t in tagStats"
        :key="t.name"
        type="button"
        class="tag-chip"
        :class="{ active: selectedTag === t.name }"
        @click="toggleTag(t.name)"
      >
        {{ t.name }}
        <span class="tag-count">{{ t.count }}</span>
      </button>
    </div>

    <el-table v-loading="loading" :data="items" empty-text="还没有方案，点「上传方案」开始共享">
      <el-table-column label="标题" min-width="200">
        <template #default="{ row }">
          <button type="button" class="link-btn" @click="openDetail(row)">{{ row.title }}</button>
        </template>
      </el-table-column>
      <el-table-column label="摘要" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.summary || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="标签" min-width="180">
        <template #default="{ row }">
          <el-tag
            v-for="t in row.tags.slice(0, 5)"
            :key="t.id"
            size="small"
            class="row-tag"
            @click="toggleTag(t.name)"
          >
            {{ t.name }}
          </el-tag>
          <span v-if="!row.tags.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusLabel(row.status).type" size="small">
            {{ statusLabel(row.status).text }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="uploader_name" label="上传人" width="100" />
      <el-table-column label="时间" width="150">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="download(row)">下载</el-button>
          <el-button v-if="canManage(row)" link type="danger" @click="removeSolution(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        layout="total, prev, pager, next"
        :total="total"
        @current-change="load"
      />
    </div>

    <el-drawer v-model="drawerVisible" title="方案详情" size="480px">
      <template v-if="current">
        <p class="meta">
          原文件：{{ current.original_filename }} · {{ formatSize(current.file_size) }} ·
          {{ current.uploader_name }}
        </p>
        <p class="meta">状态：{{ statusLabel(current.status).text }}</p>
        <el-alert
          v-if="current.status === 'failed' && current.error_message"
          :title="current.error_message"
          type="error"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />

        <template v-if="canManage(current)">
          <el-form label-position="top">
            <el-form-item label="标题">
              <el-input v-model="editTitle" maxlength="256" />
            </el-form-item>
            <el-form-item label="标签（回车添加）">
              <el-select
                v-model="editTags"
                multiple
                filterable
                allow-create
                default-first-option
                style="width: 100%"
                placeholder="输入标签后回车"
              />
            </el-form-item>
          </el-form>
          <div class="drawer-actions">
            <el-button type="primary" :loading="saving" @click="saveDetail">保存</el-button>
            <el-button @click="reprocess">重新整理</el-button>
            <el-button @click="download(current)">下载原文件</el-button>
            <el-button type="danger" plain @click="removeSolution(current)">删除</el-button>
          </div>
        </template>
        <template v-else>
          <h3 class="detail-title">{{ current.title }}</h3>
          <div class="tag-cloud" style="margin-bottom: 12px">
            <el-tag v-for="t in current.tags" :key="t.id" size="small" class="row-tag">
              {{ t.name }}
            </el-tag>
          </div>
          <p class="summary-block">{{ current.summary || '暂无摘要' }}</p>
          <el-button type="primary" @click="download(current)">下载原文件</el-button>
        </template>

        <div v-if="canManage(current)" class="summary-preview">
          <div class="label">摘要</div>
          <p>{{ current.summary || '整理完成后将显示摘要' }}</p>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.ask-box {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  max-width: 640px;
}
.ask-result {
  margin-bottom: 16px;
  padding: 12px;
  background: #f7faf8;
  border: 1px solid var(--wb-border);
  border-radius: 8px;
}
.ask-hits { margin: 8px 0 0; padding-left: 18px; }
.ask-hit {
  border: none;
  background: none;
  color: var(--wb-mint-text, #2a7a5a);
  cursor: pointer;
  padding: 0;
  font-weight: 600;
}
.ask-hit:hover { text-decoration: underline; }

.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.tag-chip {
  border: 1px solid var(--wb-border);
  background: #fff;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 13px;
  cursor: pointer;
  color: var(--wb-text);
}

.tag-chip.active {
  border-color: var(--wb-primary, #2563eb);
  color: var(--wb-primary, #2563eb);
  background: #eff6ff;
}

.tag-count {
  margin-left: 4px;
  opacity: 0.65;
  font-size: 12px;
}

.link-btn {
  border: none;
  background: none;
  padding: 0;
  color: var(--wb-primary, #2563eb);
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.row-tag {
  margin-right: 4px;
  margin-bottom: 2px;
  cursor: pointer;
}

.muted {
  color: #94a3b8;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.meta {
  font-size: 13px;
  color: #64748b;
  margin: 0 0 8px;
}

.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.summary-preview .label {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 4px;
}

.summary-block {
  line-height: 1.6;
  margin: 0 0 16px;
  white-space: pre-wrap;
}

.detail-title {
  margin: 0 0 12px;
  font-size: 18px;
}
</style>
