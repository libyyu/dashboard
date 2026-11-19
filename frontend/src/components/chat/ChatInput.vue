<template>
  <div class="chat-input">
    <!-- 已选附件 -->
    <div v-if="attachments.length" class="attachments">
      <div v-for="a in attachments" :key="a.id" class="att-item">
        <img v-if="a.kind === 'image' && a.dataUrl" :src="a.dataUrl" class="att-thumb" :alt="a.name" />
        <div v-else class="att-file">
          <span class="att-name">{{ a.name }}</span>
          <span class="att-meta">{{ a.kind === 'text' ? '文本' : fmtSize(a.size) }}</span>
        </div>
        <button type="button" class="att-remove" aria-label="移除附件" @click="removeAttachment(a.id)">×</button>
      </div>
    </div>

    <div class="input-wrap" @paste="onPaste">
      <el-input
        ref="inputEl"
        v-model="text"
        type="textarea"
        :rows="2"
        :autosize="{ minRows: 2, maxRows: 6 }"
        :disabled="disabled && !streaming"
        resize="none"
        :placeholder="placeholder"
        @keydown="onKeydown"
      />
    </div>

    <input ref="fileInput" type="file" multiple hidden @change="onPickFiles" />

    <div class="input-toolbar">
      <el-button
        size="small"
        text
        :icon="Paperclip"
        class="attach-btn"
        title="添加文件 / 图片（或直接粘贴）"
        @click="openPicker"
      />
      <span v-if="hint" class="pd-hint">{{ hint }}</span>
      <span v-if="providersError" class="provider-err">{{ providersError }}</span>
      <span class="toolbar-spacer" />
      <el-button
        v-if="needsLogin && providerName"
        size="small"
        text
        type="primary"
        @click="triggerLogin(providerName)"
      >去登录</el-button>
      <el-popover placement="top-end" :width="380" trigger="click">
        <template #reference>
          <el-button size="small" class="model-trigger" :loading="providersLoading">
            <span class="model-label">{{ modelLabel }}</span>
            <span class="effort-tag">{{ effortLabel }}</span>
            <el-icon class="model-arrow"><ArrowDown /></el-icon>
          </el-button>
        </template>
        <div class="model-pop">
          <div class="pop-row">
            <span class="pop-label">模型</span>
            <el-select
              :model-value="selectedKey"
              size="small"
              class="pop-select"
              clearable
              filterable
              :loading="providersLoading"
              placeholder="默认模型"
              @change="onSelectModel"
            >
              <template v-for="p in providers" :key="p.name">
                <el-option-group
                  v-if="(modelsByProvider[p.name]?.length ?? 0) > 0"
                  :label="p.display_name || p.name"
                >
                  <el-option
                    v-for="m in modelsByProvider[p.name]"
                    :key="m"
                    :value="optionKey(p.name, m)"
                    :label="m"
                  />
                </el-option-group>
                <el-option
                  v-else-if="!providersLoading"
                  :value="optionKey(p.name, '')"
                  :label="`${p.display_name || p.name}（默认）`"
                />
              </template>
            </el-select>
            <el-button
              :icon="RefreshRight"
              circle
              text
              size="small"
              class="pop-refresh"
              :loading="providersLoading"
              @click="loadProviders"
            />
          </div>
          <div class="pop-row">
            <span class="pop-label">思考强度</span>
            <el-slider
              v-model="effortLevel"
              :min="0"
              :max="2"
              :step="1"
              :marks="effortMarks"
              class="effort-slider"
            />
          </div>
          <div class="pop-row-col">
            <span class="pop-label">系统提示</span>
            <el-input
              v-model="systemText"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              size="small"
              placeholder="自定义 Agent 角色与约束（可选）"
            />
          </div>
          <div class="pop-note">部分模型不支持思考，将自动忽略该设置</div>
        </div>
      </el-popover>
      <el-button
        v-if="streaming"
        type="danger"
        plain
        :icon="CircleClose"
        @click="$emit('stop')"
      >
        停止
      </el-button>
      <el-button
        v-else
        type="primary"
        :icon="Promotion"
        :disabled="disabled || !canSend"
        @click="submit"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, CircleClose, Paperclip, Promotion, RefreshRight } from '@element-plus/icons-vue'
import { useModelSelection, type ModelSelection, type ReasoningEffort } from './useModelSelection'
import { uploadFile } from '../../api/upload'
import type { ChatAttachment } from '../../types/chat'

const props = withDefaults(
  defineProps<{
    streaming?: boolean
    disabled?: boolean
    placeholder?: string
    hint?: string
    /** 外部共享的模型选择状态；不传时组件内部自建（可独立使用） */
    selection?: ModelSelection
  }>(),
  {
    streaming: false,
    disabled: false,
    placeholder: '输入消息，Enter 发送，Shift+Enter 换行；支持粘贴图片',
    hint: '',
    selection: undefined,
  },
)

const emit = defineEmits<{
  (e: 'send', payload: {
    text: string
    provider?: string
    model?: string
    reasoningEffort?: ReasoningEffort
    system?: string
    attachments: ChatAttachment[]
  }): void
  (e: 'stop'): void
}>()

// 超长文本自动转文件上传的阈值（字符数）
const LONG_TEXT_THRESHOLD = 2000
const MAX_FILE_SIZE = 20 * 1024 * 1024 // 20MB

// 优先复用外部传入的选择状态（与会话/登录引导共享同一份），否则自建
const sel: ModelSelection = props.selection ?? useModelSelection()
const {
  providers,
  providersLoading,
  providersError,
  providerName,
  modelName,
  modelsByProvider,
  reasoningEffort,
  currentProvider,
  loginStatus,
  loadProviders,
  triggerLogin,
} = sel

const text = ref('')
const attachments = ref<ChatAttachment[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const submitting = ref(false)
const inputEl = ref<{ focus: () => void } | null>(null)

// 系统提示词：本地持久化（与模型选择状态分离，归属输入区）
const SYSTEM_KEY = 'plateau.chat.system'
const systemText = ref(restoreSystem())
function restoreSystem(): string {
  try {
    return localStorage.getItem(SYSTEM_KEY) || ''
  } catch {
    return ''
  }
}
watch(systemText, (v) => {
  try {
    localStorage.setItem(SYSTEM_KEY, v)
  } catch {
    /* noop */
  }
})

function focus() {
  inputEl.value?.focus()
}
defineExpose({ focus })

// Ctrl/Cmd+K 聚焦输入框
function onGlobalKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    focus()
  }
}
onMounted(() => window.addEventListener('keydown', onGlobalKey))
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))

const canSend = computed(
  () => (text.value.trim().length > 0 || attachments.value.length > 0) && !submitting.value,
)
// 仅在登录型 provider 且未确认已登录（ready）时展示；未探测/探测失败时不显示，避免登录后仍常驻
const needsLogin = computed(() => {
  const p = currentProvider.value
  if (!p || p.auth !== 'login') return false
  const s = loginStatus.value[p.name]
  return s !== undefined && s !== 'ready'
})
const modelLabel = computed(() => {
  if (modelName.value) return modelName.value
  const p = currentProvider.value
  return p ? p.display_name || p.name : '默认模型'
})

// 思考强度：滑块 0/1/2 ↔ low/medium/high
const EFFORT_ORDER: ReasoningEffort[] = ['low', 'medium', 'high']
const EFFORT_TEXT: Record<ReasoningEffort, string> = { low: '低', medium: '中', high: '高' }
const effortMarks = { 0: '低', 1: '中', 2: '高' }
const effortLabel = computed(() => EFFORT_TEXT[reasoningEffort.value])
const effortLevel = computed({
  get: () => EFFORT_ORDER.indexOf(reasoningEffort.value),
  set: (v: number | string) => {
    reasoningEffort.value = EFFORT_ORDER[Number(v)] ?? 'medium'
  },
})

// 单列表下拉：value = "provider|model"，选中即同时定服务与模型
function optionKey(provider: string, model: string): string {
  return `${provider}|${model}`
}
const selectedKey = computed(() => `${providerName.value}|${modelName.value}`)
function onSelectModel(value: string | number | boolean | object | undefined) {
  const key = typeof value === 'string' ? value : ''
  const idx = key.indexOf('|')
  if (idx < 0) {
    modelName.value = ''
    return
  }
  providerName.value = key.slice(0, idx)
  modelName.value = key.slice(idx + 1)
}

// —— 附件 ——
function openPicker() {
  fileInput.value?.click()
}

function onPickFiles(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void addFiles(Array.from(input.files))
  input.value = ''
}

// 粘贴：剪贴板含图片则作为附件（并阻止把 base64 塞进文本框），纯文本走默认粘贴
function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  const files: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const f = item.getAsFile()
      if (f) files.push(f)
    }
  }
  if (files.length) {
    e.preventDefault()
    void addFiles(files)
  }
}

function removeAttachment(id: string) {
  attachments.value = attachments.value.filter((a) => a.id !== id)
}

async function addFiles(files: File[]) {
  for (const f of files) {
    const att = await readFile(f)
    if (att) attachments.value.push(att)
  }
}

async function readFile(f: File): Promise<ChatAttachment | null> {
  if (f.size > MAX_FILE_SIZE) {
    ElMessage.warning(`文件过大（>20MB），已跳过：${f.name}`)
    return null
  }
  const mime = f.type || 'application/octet-stream'
  const base = { id: genId(), name: f.name, mime, size: f.size }
  if (mime.startsWith('image/')) {
    return { ...base, kind: 'image', dataUrl: await processImage(f) }
  }
  if (isTextLike(f)) {
    return { ...base, kind: 'text', text: await readAsText(f) }
  }
  return { ...base, kind: 'file', file: f }
}

function isTextLike(f: File): boolean {
  const mime = f.type || ''
  if (mime.startsWith('text/')) return true
  if (['application/json', 'application/xml', 'application/javascript'].includes(mime)) return true
  return /\.(md|txt|log|py|ts|js|json|yml|yaml|css|html|lua|cpp|c|h|hpp|csv|ini|toml)$/i.test(f.name)
}

async function submit() {
  if (submitting.value || props.streaming) return
  let value = text.value.trim()
  if (!value && attachments.value.length === 0) return
  submitting.value = true
  try {
    // 超长文本自动转文件，避免一条消息把上下文撑爆
    if (value.length > LONG_TEXT_THRESHOLD) {
      attachments.value.push({
        id: genId(),
        name: `片段-${Date.now()}.txt`,
        mime: 'text/plain',
        kind: 'text',
        size: new Blob([value]).size,
        text: value,
      })
      value = ''
    }
    // file 类附件：发送前上传到后端；失败保留展示态（仅名称，不阻塞发送）
    const ready = await uploadFileAttachments(attachments.value)
    emit('send', {
      text: value,
      provider: providerName.value || undefined,
      model: modelName.value || undefined,
      reasoningEffort: reasoningEffort.value,
      system: systemText.value.trim() || undefined,
      attachments: ready,
    })
    text.value = ''
    attachments.value = []
  } finally {
    submitting.value = false
  }
}

async function uploadFileAttachments(list: ChatAttachment[]): Promise<ChatAttachment[]> {
  const out: ChatAttachment[] = []
  for (const a of list) {
    if (a.kind !== 'file') {
      out.push(a)
      continue
    }
    const { file, ...rest } = a
    if (file) {
      try {
        const up = await uploadFile(file)
        out.push({ ...rest, url: up.url, fileId: up.file_id })
        continue
      } catch {
        ElMessage.warning(`附件上传失败（已保留文件名）：${a.name}`)
      }
    }
    out.push(rest)
  }
  return out
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

// —— 工具 ——
function genId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return Math.random().toString(36).slice(2)
  }
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function readAsDataURL(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result))
    r.onerror = reject
    r.readAsDataURL(f)
  })
}

function readAsText(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result))
    r.onerror = reject
    r.readAsText(f)
  })
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = src
  })
}

// 大图缩放为 jpeg，控制 base64 体积（截图/照片常见超大）
async function processImage(f: File): Promise<string> {
  const dataUrl = await readAsDataURL(f)
  const target = 1600
  try {
    const img = await loadImage(dataUrl)
    const maxSide = Math.max(img.width, img.height)
    if (maxSide > target) {
      const scale = target / maxSide
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(img.width * scale)
      canvas.height = Math.round(img.height * scale)
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        return canvas.toDataURL('image/jpeg', 0.85)
      }
    }
  } catch {
    /* 缩放失败退回原图 */
  }
  return dataUrl
}
</script>

<style scoped>
.chat-input {
  border-top: 1px solid var(--pd-border);
  padding: 12px 16px;
  background: var(--pd-surface);
}
.input-wrap { display: flex; }
.input-wrap :deep(.el-textarea__inner) {
  background: var(--pd-surface-2);
  border: 1px solid var(--pd-border);
  box-shadow: none;
  border-radius: var(--pd-radius);
}
.input-wrap :deep(.el-textarea__inner:focus) {
  border-color: var(--pd-primary);
  box-shadow: 0 0 0 3px var(--pd-primary-ring);
}
.input-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.toolbar-spacer { flex: 1; }
.provider-err { font-size: 12px; color: var(--pd-danger); }
.attach-btn { color: var(--pd-muted); }
.attach-btn:hover { color: var(--pd-primary); }
.model-trigger { font-weight: 500; color: var(--pd-text-2); }
.model-trigger:hover { color: var(--pd-text); }
.model-label {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.effort-tag { margin-left: 6px; font-size: 12px; font-weight: 600; color: var(--pd-primary); }
.model-arrow { margin-left: 2px; color: var(--pd-faint); }
.model-pop { display: flex; flex-direction: column; gap: 16px; }
.pop-row { display: flex; align-items: center; gap: 10px; }
.pop-row-col { display: flex; flex-direction: column; gap: 6px; }
.pop-label { flex: none; width: 52px; font-size: 12px; color: var(--pd-muted); }
.pop-select { flex: 1; }
.pop-refresh { color: var(--pd-muted); }
.effort-slider { flex: 1; margin: 0 8px 0 4px; }
.effort-slider :deep(.el-slider__marks-text) { font-size: 11px; color: var(--pd-muted); }
.effort-slider :deep(.el-slider__bar) { background: var(--pd-primary); }
.effort-slider :deep(.el-slider__button) { border-color: var(--pd-primary); }
.pop-note { font-size: 11px; color: var(--pd-faint); margin-top: -6px; }

.attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.att-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--pd-surface-2);
  border: 1px solid var(--pd-border);
  border-radius: var(--pd-radius-sm);
  padding: 5px 8px;
}
.att-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 5px;
}
.att-file {
  display: flex;
  flex-direction: column;
  max-width: 200px;
}
.att-name {
  font-size: 12px;
  color: var(--pd-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}
.att-meta { font-family: var(--pd-mono); font-size: 10px; color: var(--pd-faint); }
.att-remove {
  border: none;
  background: transparent;
  color: var(--pd-faint);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
}
.att-remove:hover { color: var(--pd-danger); background: var(--pd-surface); }
</style>
