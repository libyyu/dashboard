// 对话域统一数据模型：任何需要对话能力的模块（AI 助手，以及后续接入对话的其它功能）
// 都基于这套类型，与具体页面解耦，配合 components/chat/ 组件库复用。
//
// 组件化拆分（components/chat/，Step 6 落地）：
//   ChatSession     会话容器（组合子组件，可复用入口）
//   ChatInput       输入组件（多行 / 发送 / 停止 / 插槽）
//   ChatMessageList 消息列表（滚动 / 自动滚底）
//   ChatMessageItem 单条消息（角色区分 user/assistant/system/tool）
//   ChatThinking    思考过程（reasoning/thinking 折叠展开）
//   ChatToolCall    工具调用（MCP/tool 参数与结果）
//   useChatStream   流式传输 composable（SSE 解析 / 增量拼接 / 取消 / 重连）

export type ChatRole = 'user' | 'assistant' | 'system' | 'tool'

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error' | 'cancelled'

export interface ToolCall {
  id: string
  name: string
  arguments: string
  result?: string
  status: 'running' | 'done' | 'error'
}

// 消息附件：图片 / 文本 / 其它文件（图片走多模态，文本拼进正文，其它仅展示名称）
export type ChatAttachmentKind = 'image' | 'text' | 'file'

export interface ChatAttachment {
  id: string
  name: string
  mime: string
  kind: ChatAttachmentKind
  size: number
  dataUrl?: string // image：base64 data URL（预览 + 多模态发送）
  text?: string // text：抽取的文本内容
  // file 类附件：上传后的后端 id 与可访问地址（发送时回填）
  fileId?: string
  url?: string
  // 客户端暂存的原始 File 句柄（仅上传用，不入库、不序列化）
  file?: File
}

// 上传接口返回：后端落盘后的文件引用
export interface UploadedFile {
  file_id: string
  name: string
  mime: string
  size: number
  url: string
}

// token 用量（run_done.usage 透传，用于消息脚注展示）
export interface ChatUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

// 多模态消息内容（OpenAI 兼容 content 数组格式）
export type ChatContentPart =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string } }

// 单条消息：content 为正文，thinking 为思考过程，toolCalls 为工具调用链
export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  thinking?: string
  toolCalls?: ToolCall[]
  status: MessageStatus
  createdAt: number
  error?: string
  // 契约错误码（error 事件 payload.code）：PROVIDER_AUTH_REQUIRED 等用于前端引导
  errorCode?: string
  // 本条 assistant 回复使用的 provider / model（会话回放与跨供应商区分）
  provider?: string
  model?: string
  // token 用量（assistant 回复结束时回填）
  usage?: ChatUsage
  // 本轮生成耗时（毫秒，assistant 回复结束时回填）
  durationMs?: number
  // user 消息携带的附件（图片/文件），发送时重建为多模态/文本内容
  attachments?: ChatAttachment[]
}

// 会话：一组消息，可持久化（后端 SessionStore）
export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  updatedAt: number
}

// 流式事件：与后端 app.core.agent.AgentEvent 对齐（SSE，帧带 id:{seq}）。
// payload 按 type 区分：run_started{model,provider} / thinking_delta{text} /
// message_delta{text} / tool_call{id,name,arguments} / run_done{finish_reason,usage,model} /
// error{message,code} / cancelled{}
export type AgentEventType =
  | 'run_started'
  | 'thinking_delta'
  | 'message_delta'
  | 'tool_call'
  | 'tool_result'
  | 'run_done'
  | 'error'
  | 'cancelled'

export interface AgentEvent {
  type: AgentEventType
  seq: number
  run_id: string
  session_id: string
  trace_id: string
  request_id: string
  payload: Record<string, unknown>
}

// 发送请求体：对齐 POST /api/agent/runs/stream 的 body
export interface ChatSendRequest {
  session_id: string
  provider?: string
  model?: string
  // 思考强度（低/中/高），透传给 provider 上游；缺省由上游自定
  reasoning_effort?: 'low' | 'medium' | 'high'
  title?: string
  // 系统提示词（自定义 Agent 角色/约束，透传给 provider；缺省由后端自定）
  system?: string
  messages: ChatRequestMessage[]
}

// 请求里的单条消息：content 为正文（多模态时是 parts 数组），
// attachments 仅为后端持久化/回放携带，透传模型前由后端剥离。
export interface ChatRequestMessage {
  role: string
  content: string | ChatContentPart[]
  attachments?: ChatAttachment[]
}

// /api/model/providers 返回条目（响应不含 api_key）
export interface ModelProviderInfo {
  name: string
  display_name?: string
  base_url: string
  source: 'registry' | 'sidecar'
  api_type: string
  auth: 'api-key' | 'login' | 'none'
  protocols?: string[]
}

export interface ModelProviderList {
  providers: ModelProviderInfo[]
  default: string | null
}

// —— 会话持久化 ——
export interface ChatSessionMeta {
  session_id: string
  title: string
  last_seq: number
  created_at: string
  updated_at: string
}

export interface AgentSessionList {
  sessions: ChatSessionMeta[]
}

// 后端会话消息回放条目（user/assistant 完整对话，刷新后恢复）
export interface StoredChatMessage {
  // 消息在会话内的单调序号（后端真分页游标），仅回放/分页使用
  msg_seq?: number
  role: ChatRole
  content: string
  thinking?: string | null
  tool_calls?: Array<{ id: string; name: string; arguments?: string; result?: string }> | null
  provider?: string | null
  model?: string | null
  created_at?: string | null
  attachments?: StoredChatAttachment[] | null
}

// 持久化附件元信息（去除客户端瞬态的 File 句柄）
export type StoredChatAttachment = Omit<ChatAttachment, 'file'>

// GET /api/agent/sessions/{id}/messages 分页响应：
// messages 为最近一页（升序），has_more 表示是否还有更早消息，
// next_before_seq 为下一页游标（作为 before_seq 加载更早消息；<0 表示无更多）
export interface AgentSessionMessages {
  session_id: string
  messages: StoredChatMessage[]
  has_more: boolean
  next_before_seq: number
}
