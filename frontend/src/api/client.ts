// 统一 API 客户端：JWT 鉴权、JSON 请求、SSE 流式、文件导出下载
import type { ApiResult, SSEEvent } from './types'
import { USE_MOCK, mockApi, mockStreamGenerate, mockDownloadExport } from './mock'

const BASE = import.meta.env.VITE_API_BASE || '/api'

// ---------------- Token 管理 ----------------
export function getToken(): string | null {
  return localStorage.getItem('token')
}
export function setToken(t?: string) {
  if (t) localStorage.setItem('token', t)
  else localStorage.removeItem('token')
}
export function getUsername(): string {
  return localStorage.getItem('username') || ''
}

// ---------------- 通用请求 ----------------
async function request<T = ApiResult>(path: string, options: RequestInit = {}): Promise<T> {
  // Mock 模式：直接走前端 Mock 数据层（无需后端）
  if (USE_MOCK) {
    const method = (options.method || 'GET').toUpperCase()
    return (method === 'GET' ? mockApi.get(path) : mockApi.post(path, options.body ? JSON.parse(options.body as string) : undefined)) as Promise<T>
  }

  const headers = new Headers(options.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')

  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    const data = await res.json()
    if (!res.ok || data?.ok === false) {
      if (res.status === 401) {
        setToken()
        window.dispatchEvent(new Event('auth:unauthorized'))
      }
      throw new Error(data?.msg || `请求失败 (${res.status})`)
    }
    return data as T
  }
  if (!res.ok) throw new Error(`请求失败 (${res.status})`)
  return res as unknown as T
}

export const api = {
  get: <T = ApiResult>(p: string) => request<T>(p, { method: 'GET' }),
  post: <T = ApiResult>(p: string, body?: any) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
}

// ---------------- SSE 流式生成 ----------------
// POST /api/copy/generate → text/event-stream，逐条派发 data:{json}
export async function streamGenerate(
  req: any,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (USE_MOCK) {
    return mockStreamGenerate(req, onEvent, signal)
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}/copy/generate`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) {
    const ct = res.headers.get('content-type') || ''
    if (ct.includes('application/json')) {
      const data = await res.json()
      throw new Error(data?.msg || '生成请求失败')
    }
    throw new Error(`生成请求失败 (${res.status})`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = block.split('\n').find((l) => l.startsWith('data:'))
      if (line) {
        const payload = line.slice(5).trim()
        if (payload) {
          try {
            onEvent(JSON.parse(payload) as SSEEvent)
          } catch {
            /* 忽略无法解析的片段 */
          }
        }
      }
    }
  }
}

// ---------------- 导出文件下载 ----------------
// POST /api/export 返回文件流（txt/docx）；失败时为 JSON（{ok:false,msg}）
export async function downloadExport(req: {
  task_id: number
  platform?: string
  format: 'txt' | 'docx'
}): Promise<void> {
  if (USE_MOCK) {
    return mockDownloadExport(req)
  }

  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}/export`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
  })
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    const data = await res.json()
    throw new Error(data?.msg || '导出失败')
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const disp = res.headers.get('content-disposition') || ''
  const m = disp.match(/filename="?([^"]+)"?/)
  a.download = m ? m[1] : `copy_task_${req.task_id}.${req.format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
