<template>
  <!--
    Markdown 渲染组件：把 AI 回复正文渲染为富文本（代码高亮 + 代码块复制）。
    只渲染纯文本 Markdown（html:false 防 XSS）；复制按钮走事件委托，不依赖动态绑定。
  -->
  <div class="md" @click="onClick" v-html="html"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import markdownItKatex from 'markdown-it-katex'
import hljs from 'highlight.js'
import 'katex/dist/katex.min.css'

const props = defineProps<{ text: string }>()

// 代码块：高亮 + 顶部「复制」按钮（原始代码用 textContent 保留，复制时不失真）
function highlight(str: string, lang: string): string {
  const code =
    lang && hljs.getLanguage(lang)
      ? hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
      : md.utils.escapeHtml(str)
  return (
    '<div class="md-code">' +
    '<button type="button" class="md-code-copy">复制</button>' +
    `<pre><code class="hljs${lang ? ` language-${lang}` : ''}">${code}</code></pre>` +
    '</div>'
  )
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight,
}).use(markdownItKatex, { throwOnError: false })

// 链接统一新窗口打开（包装默认规则，避免覆盖其 href 渲染）
const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const html = computed(() => {
  const t = props.text ?? ''
  return t ? md.render(t) : ''
})

function onClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  const btn = target?.closest('.md-code-copy') as HTMLButtonElement | null
  if (!btn) return
  const code = btn.parentElement?.querySelector('code')?.textContent ?? ''
  void copyText(code, btn)
}

async function copyText(text: string, btn: HTMLButtonElement) {
  const restore = () => {
    btn.textContent = '复制'
  }
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 剪贴板不可用时的回退：临时文本域 + execCommand
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    } catch {
      /* 复制失败静默忽略 */
    }
  }
  btn.textContent = '已复制'
  window.setTimeout(restore, 1500)
}
</script>

<style>
/* v-html 内容不会被 scoped 命中，这里用非 scoped 块定义 Markdown 视觉 */
.md {
  word-break: break-word;
}
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 0 0 8px; }
.md p:last-child { margin-bottom: 0; }
.md h1, .md h2, .md h3, .md h4 {
  margin: 14px 0 8px;
  line-height: 1.35;
  font-weight: 650;
}
.md h1 { font-size: 17px; }
.md h2 { font-size: 15.5px; }
.md h3 { font-size: 14.5px; }
.md h4 { font-size: 14px; }
.md ul, .md ol { margin: 0 0 8px; padding-left: 22px; }
.md li { margin: 2px 0; }
.md blockquote {
  margin: 8px 0;
  padding: 2px 12px;
  border-left: 3px solid var(--pd-border-strong);
  color: var(--pd-muted);
}
.md a { color: var(--pd-primary); }
.md hr { border: none; border-top: 1px solid var(--pd-border); margin: 12px 0; }
.md table { border-collapse: collapse; margin: 8px 0; max-width: 100%; }
.md th, .md td { border: 1px solid var(--pd-border); padding: 5px 10px; font-size: 13px; }
.md th { background: var(--pd-surface-2); }

.md code {
  font-family: var(--pd-mono);
  font-size: 12.5px;
  background: var(--pd-surface-2);
  padding: 1px 5px;
  border-radius: 4px;
}
.md .md-code {
  position: relative;
  margin: 8px 0;
  border: 1px solid var(--pd-border);
  border-radius: 8px;
  overflow: hidden;
}
.md .md-code pre {
  margin: 0;
  padding: 12px 14px;
  overflow-x: auto;
  background: oklch(97% 0.006 262);
}
[data-theme="dark"] .md .md-code pre { background: oklch(20% 0.008 262); }
.md .md-code code {
  background: transparent;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.6;
}
.md .md-code-copy {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--pd-muted);
  background: var(--pd-surface);
  border: 1px solid var(--pd-border);
  border-radius: 5px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}
.md .md-code:hover .md-code-copy { opacity: 1; }
.md .md-code-copy:hover { color: var(--pd-primary); }

/* highlight.js token 配色（浅色） */
.md .hljs-comment, .md .hljs-quote { color: oklch(55% 0.02 155); }
.md .hljs-keyword, .md .hljs-selector-tag, .md .hljs-literal, .md .hljs-meta { color: oklch(55% 0.16 30); }
.md .hljs-string, .md .hljs-regexp, .md .hljs-addition { color: oklch(52% 0.11 155); }
.md .hljs-number, .md .hljs-symbol, .md .hljs-bullet { color: oklch(50% 0.11 262); }
.md .hljs-title, .md .hljs-title.function_, .md .hljs-title.class_ { color: oklch(55% 0.11 262); }
.md .hljs-attr, .md .hljs-property, .md .hljs-variable, .md .hljs-template-variable, .md .hljs-type { color: oklch(50% 0.09 262); }
.md .hljs-built_in, .md .hljs-builtin-name { color: oklch(55% 0.1 262); }
.md .hljs-section, .md .hljs-name { color: oklch(55% 0.11 262); }
.md .hljs-deletion { color: oklch(52% 0.17 28); }
.md .hljs-emphasis { font-style: italic; }
.md .hljs-strong { font-weight: 600; }

[data-theme="dark"] .md .hljs-comment, [data-theme="dark"] .md .hljs-quote { color: oklch(63% 0.03 155); }
[data-theme="dark"] .md .hljs-keyword, [data-theme="dark"] .md .hljs-selector-tag, [data-theme="dark"] .md .hljs-literal, [data-theme="dark"] .md .hljs-meta { color: oklch(70% 0.14 30); }
[data-theme="dark"] .md .hljs-string, [data-theme="dark"] .md .hljs-regexp, [data-theme="dark"] .md .hljs-addition { color: oklch(68% 0.1 155); }
[data-theme="dark"] .md .hljs-number, [data-theme="dark"] .md .hljs-symbol, [data-theme="dark"] .md .hljs-bullet { color: oklch(68% 0.09 262); }
[data-theme="dark"] .md .hljs-title, [data-theme="dark"] .md .hljs-title.function_, [data-theme="dark"] .md .hljs-title.class_ { color: oklch(72% 0.08 262); }
[data-theme="dark"] .md .hljs-attr, [data-theme="dark"] .md .hljs-property, [data-theme="dark"] .md .hljs-variable, [data-theme="dark"] .md .hljs-template-variable, [data-theme="dark"] .md .hljs-type { color: oklch(70% 0.07 262); }
[data-theme="dark"] .md .hljs-built_in, [data-theme="dark"] .md .hljs-builtin-name { color: oklch(70% 0.08 262); }
[data-theme="dark"] .md .hljs-deletion { color: oklch(68% 0.15 28); }
</style>
