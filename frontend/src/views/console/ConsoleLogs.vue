<template>
  <div class="logs">
    <div class="pd-panel toolbar">
      <el-select v-model="source" placeholder="选择日志文件" size="default" style="width: 260px" @change="loadTail">
        <el-option v-for="s in sources" :key="s.name" :label="s.name" :value="s.name" />
      </el-select>

      <el-select v-model="level" placeholder="级别" size="default" style="width: 130px" :disabled="!!activeRequestId" @change="loadTail">
        <el-option label="全部" value="" />
        <el-option label="INFO" value="INFO" />
        <el-option label="WARNING" value="WARNING" />
        <el-option label="ERROR" value="ERROR" />
      </el-select>

      <el-button :icon="Refresh" :loading="loading" @click="loadTail">刷新</el-button>

      <span v-if="activeRequestId" class="request-filter">
        已按 request_id 下钻：<code>{{ activeRequestId }}</code>
        <el-link type="primary" @click="clearRequestFilter">清除</el-link>
      </span>
    </div>

    <div v-if="isErrorView" class="pd-panel">
      <h2>异常时间线 <span class="pd-hint">按时间倒序的 ERROR / 异常日志</span></h2>
      <el-timeline v-if="lines.length" class="timeline">
        <el-timeline-item
          v-for="(row, i) in lines"
          :key="i"
          :timestamp="fmtTime(row.ts)"
          type="danger"
        >
          <div class="tl-msg">{{ row.message }}</div>
          <div class="pd-sub">{{ row.logger }}<template v-if="row.request_id"> · <code>{{ shortId(row.request_id) }}</code></template></div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="没有 ERROR 日志" :image-size="60" />
    </div>

    <div class="pd-panel">
      <el-table :data="lines" v-loading="loading" size="small" row-key="ts">
        <el-table-column label="时间" width="170" sortable prop="ts">
          <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag :type="levelTag(row.level)" size="small" effect="plain">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="消息" min-width="220" show-overflow-tooltip />
        <el-table-column label="request_id" width="150">
          <template #default="{ row }">
            <el-link v-if="row.request_id" type="primary" size="small" @click="drillDown(row.request_id)">
              {{ shortId(row.request_id) }}
            </el-link>
            <span v-else class="pd-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="70" />
        <el-table-column prop="duration_ms" label="耗时" width="80" />
        <el-table-column prop="lane" label="车道" width="90" />
        <el-table-column type="expand" width="40">
          <template #default="{ row }">
            <pre class="detail">{{ pretty(row) }}</pre>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="truncated" class="pd-hint" style="margin-top: 8px">仅显示文件尾部，更早的日志未加载。</div>
      <el-empty v-if="!loading && !lines.length" description="无日志" :image-size="60" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'
import type { LogEntry, LogSource, LogSources, LogTail } from '../../api/contract'

const sources = ref<LogSource[]>([])
const source = ref('')
const level = ref('')
const lines = ref<LogEntry[]>([])
const loading = ref(false)
const truncated = ref(false)
const activeRequestId = ref('')

const isErrorView = computed(() => level.value === 'ERROR' && !activeRequestId.value)

async function loadSources() {
  try {
    const res = await http<LogSources>(ENDPOINTS.logsSources)
    sources.value = res.sources
    const dash = res.sources.find((s) => s.name.startsWith('dashboard-'))
    if (dash) source.value = dash.name
  } catch {
    sources.value = []
  }
}

async function loadTail() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (source.value) params.set('source', source.value)
    if (level.value && !activeRequestId.value) params.set('level', level.value)
    if (activeRequestId.value) params.set('request_id', activeRequestId.value)
    params.set('limit', '300')
    const qs = params.toString()
    const res = await http<LogTail>(ENDPOINTS.logsTail + (qs ? `?${qs}` : ''))
    lines.value = res.lines
    truncated.value = res.truncated
  } finally {
    loading.value = false
  }
}

function drillDown(id: string) {
  activeRequestId.value = id
  loadTail()
}

function clearRequestFilter() {
  activeRequestId.value = ''
  loadTail()
}

function fmtTime(ts?: string): string {
  return ts ? ts.slice(11, 23) : '—'
}
function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 12) + '…' : id
}
function levelTag(level?: string) {
  if (level === 'ERROR') return 'danger'
  if (level === 'WARNING') return 'warning'
  return 'info'
}
function pretty(row: LogEntry): string {
  return JSON.stringify(row, null, 2)
}

onMounted(async () => {
  await loadSources()
  await loadTail()
})
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.request-filter { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
.request-filter code {
  background: #f0f2f7;
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
}
.detail {
  margin: 0;
  padding: 10px 12px;
  background: #f7f8fb;
  border-radius: 8px;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
.timeline { margin: 0; }
.tl-msg { font-size: 13px; word-break: break-all; }
</style>
