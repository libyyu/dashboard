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
}

// 会话：一组消息，可持久化（后端 SessionStore）
export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  updatedAt: number
}

// 流式事件：与后端 app.core.agent.AgentEvent 对齐（SSE）
export type AgentEventType =
  | 'run_started'
  | 'thinking_delta'
  | 'message_delta'
  | 'tool_call'
  | 'tool_result'
  | 'message_done'
  | 'run_done'
  | 'error'

export interface AgentEvent {
  type: AgentEventType
  run_id?: string
  delta?: string
  message?: Partial<ChatMessage>
  tool_call?: ToolCall
  error?: string
}

// 发送请求体（预留，与契约端点的实现对齐）
export interface ChatSendRequest {
  session_id?: string
  messages: Array<Pick<ChatMessage, 'role' | 'content'>>
  stream?: boolean
}
