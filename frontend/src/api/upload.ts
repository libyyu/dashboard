// 聊天附件上传：把 file 类附件以 JSON base64 传到后端落盘，返回可引用地址。
// 走统一 http 客户端（含 VITE_API_MOCK 离线 mock 与统一错误形状）。

import { http } from './http'
import { ENDPOINTS } from './contract'
import type { UploadedFile } from '../types/chat'

export function uploadFile(file: File): Promise<UploadedFile> {
  return readAsBase64(file).then((data) =>
    http<UploadedFile>(ENDPOINTS.agentFiles, {
      method: 'POST',
      body: JSON.stringify({
        name: file.name,
        mime: file.type || 'application/octet-stream',
        data,
      }),
    }),
  )
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result)
      const idx = dataUrl.indexOf(',')
      resolve(idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl)
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
