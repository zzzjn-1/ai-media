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
  /** 聚合层 id（hot_signals.id）；「用它做选题」用它直传，保证跨源合并后仍唯一 */
  signal_id?: number | null
  title: string
  summary: string
  /** 展示用来源名，如「微博热搜」 */
  source: string
  /** ★热榜来源码（weibo/zhihu/...），不是发布平台码 */
  platform?: string | null
  url: string
  domain: string
  /** 原始热度：跨源不可比，仅作参考，不用于展示与排序 */
  heat: number
  /** 归一化热度 0-100：跨源可比，是展示与排序口径 */
  norm_heat?: number
  /** 覆盖来源数（多平台同时出现 = 真趋势） */
  source_count?: number
  /** 该话题在最优来源榜内的名次 */
  best_rank?: number | null
  trend: string
  first_seen_at?: string | null
  /** 1 = 新上榜（HOTSPOT_NEW_WINDOW_HOURS 窗口内） */
  is_new?: number
  /** 切入建议角度（后端惰性生成，可能为空） */
  suggestion?: string | null
  /** 仅推荐视图返回：相关性分 0-100 与可解释理由 */
  relevance?: number
  reason?: string
  /** 当前账号是否已用这条出过选题（来自 hot_feed_event，未登录恒为 false） */
  adopted?: boolean
  collected_at: string
}

/** 采纳漏斗（GET /hotspots/status 的 adoption 字段） */
export interface AdoptionSummary {
  window_days: number
  impressions: number
  adopts: number
  generates: number
  /** null = 没有曝光数据，不是 0%；别把空值当"没人点" */
  ctr: number | null
  adopt_to_generate: number | null
  active_users: number
  top_adopted: { hotspot_id: number; title: string; domain: string; adopts: number }[]
  note: string
}

/** 数据新鲜度（后端 freshness 字段） */
export interface Freshness {
  updated_at: string | null
  age_minutes: number | null
  /** true = 实时源不可用，当前展示的是库内旧数据（前端须标注时间） */
  degraded: boolean
  sources_ok: number
  sources_total: number
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
  status?: number // 0=待选用 1=已选用
  created_at?: string // 后端已转本地时间
  hotspot_id?: number | null
  hotspot_domain?: string | null
  hotspot_signal_id?: number | null
  copy_task_id?: number | null
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

// 平台码（对齐 db/platform_rules：weixin / xhs / douyin）——★发布平台，不是热点来源
export const PLATFORMS: { code: string; label: string }[] = [
  { code: 'weixin', label: '微信公众号' },
  { code: 'xhs', label: '小红书' },
  { code: 'douyin', label: '抖音' },
]

// 热榜来源码（对齐 api/core/hot_sources.py 的 SOURCES）——★来源，不是发布平台。
// 两边都有 `douyin` 但语义不同，禁止合并成一个常量（曾因此把"来源"筛成了"发布平台"）。
export const HOT_SOURCES: { code: string; label: string }[] = [
  { code: 'weibo', label: '微博' },
  { code: 'zhihu', label: '知乎' },
  { code: 'baidu', label: '百度' },
  { code: 'douyin', label: '抖音' },
  { code: 'bilibili', label: '哔哩哔哩' },
  { code: 'toutiao', label: '今日头条' },
]

export function hotSourceLabel(code?: string | null): string {
  return HOT_SOURCES.find((s) => s.code === code)?.label || code || '热榜'
}

// 领域词表（官方 5 个领域，与热点/素材/人设展示及筛选保持一致）
export const DOMAINS = ['科技', '生活', '财经', '美妆', '教育']

// 未命中 5 个垂类时的领域值。推荐视图默认剔除，全榜视图通过它提供「其他」入口
export const MISC_DOMAIN = '综合'

export function platformLabel(code?: string): string {
  return PLATFORMS.find((p) => p.code === code)?.label || code || ''
}
