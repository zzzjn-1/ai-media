// 后端 API 数据类型（对齐 M3 交付：AI自媒体内容创作助手 M3 后端）
// 字段命名与 api/routers/* 实际返回保持一致。

export interface ApiResult<T = any> {
  ok: boolean
  msg?: string
  [k: string]: any
}

export interface LoginResp {
  ok: boolean
  access_token?: string
  token_type?: string
  user_id?: number
  msg?: string
}

export interface Hotspot {
  id: number
  title: string
  summary: string
  source: string
  url: string
  domain: string
  heat: number
  trend: string
  collected_at: string
}

export interface Material {
  id: number
  title: string
  domains?: string
  platforms?: string
  styles?: string
  custom_tags?: string
  chunk_count?: number
  created_at?: string
}

export interface MaterialChunk {
  index: number
  content: string
  char_count: number
}

export interface Topic {
  id: number
  title: string
  angle?: string
  reason?: string
  target_audience?: string
}

export interface Persona {
  display_name?: string
  positioning?: string
  tone?: string
  domains?: string
}

export interface CopyVersion {
  version_no: number
  id?: number
  platform?: string
  title: string
  body: string
  tags?: string[]
  adapted?: Record<string, string>
  layout_style?: string
  export_format?: string
  truncated?: boolean
  note?: string
  style_consistency?: string
  replacement_suggestions?: any
}

export interface SSEEvent {
  type: 'start' | 'retrieved' | 'version' | 'done' | 'error'
  task_id?: number
  chunk_count?: number
  snippets?: string[]
  version_no?: number
  optimized_body?: string
  adapted?: Record<string, string>
  style_consistency?: string
  replacement_suggestions?: any
  versions_count?: number
  generation_seconds?: number
  target_platforms?: string[]
  msg?: string
  [k: string]: any
}

// 平台码（对齐 db/platform_rules：weixin / xhs / douyin）
export const PLATFORMS: { code: string; label: string }[] = [
  { code: 'weixin', label: '微信公众号' },
  { code: 'xhs', label: '小红书' },
  { code: 'douyin', label: '抖音' },
]

// 领域词表（官方 5 个领域，与热点/素材/人设展示及筛选保持一致）
export const DOMAINS = ['科技', '生活', '财经', '美妆', '教育']

export function platformLabel(code?: string): string {
  return PLATFORMS.find((p) => p.code === code)?.label || code || ''
}
