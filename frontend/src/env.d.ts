/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// markdown-it-katex 无官方类型定义：声明为 markdown-it 插件形态，配合 .use() 使用
declare module 'markdown-it-katex' {
  import type MarkdownIt from 'markdown-it'
  interface KatexOptions {
    throwOnError?: boolean
    errorColor?: string
    displayMode?: boolean
    [key: string]: unknown
  }
  function markdownItKatex(md: MarkdownIt, options?: KatexOptions): void
  export default markdownItKatex
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_API_TIMEOUT_MS?: string
  readonly VITE_API_MOCK?: string
  readonly VITE_API_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
