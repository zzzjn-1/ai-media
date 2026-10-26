// 前端 Mock 数据层（无需后端即可浏览全部页面）
// - 当 VITE_USE_MOCK=true 时，client.ts 将请求路由到本模块
// - 模拟真实后端契约：auth / hotspots / materials / persona / topics / copy(generate SSE·optimize·adapt) / export
// - 登录接受任意账号密码（演示账号 demo_seed，口令见 README 的「快速开始」）

import { DOMAINS, MISC_DOMAIN } from './types'

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true' || import.meta.env.VITE_USE_MOCK === '1'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// ----------------- 平台映射 -----------------
const PLATFORMS: Record<string, string> = {
  weixin: '微信公众号',
  xhs: '小红书',
  douyin: '抖音',
}
const platformLabel = (c: string) => PLATFORMS[c] || c

// ----------------- 内存状态 -----------------
let tokenCounter = 1
let matId = 100
let topicId = 1
const curatedMaterials: any[] = [
  {
    id: 1,
    title: '大模型推理成本逐年下降，端侧部署成为新趋势',
    platforms: 'weixin,xhs',
    domains: '科技',
    styles: '专业',
    custom_tags: '端侧,降本',
    chunk_count: 4,
    created_at: '2026-09-20 14:22',
  },
  {
    id: 2,
    title: '我用 AI 重写了团队周报流程，每周省下 6 小时',
    platforms: 'xhs,douyin',
    domains: '生活',
    styles: '轻松',
    custom_tags: '自动化',
    chunk_count: 3,
    created_at: '2026-09-22 09:10',
  },
  {
    id:3,
    title: '从 0 到 1 搭建个人知识库的踩坑清单',
    platforms: 'weixin',
    domains: '生活',
    styles: '专业',
    custom_tags: '知识管理',
    chunk_count: 5,
    created_at: '2026-09-25 20:45',
  },
  {
    id: 4,
    title: '短视频脚本的黄金 3 秒：开头决定完播率',
    platforms: 'douyin,xhs',
    domains: '生活',
    styles: '轻松',
    custom_tags: '脚本',
    chunk_count: 2,
    created_at: '2026-10-01 11:30',
  },
  {
    id: 5,
    title: 'AI 写作会不会取代自媒体人？一线创作者访谈',
    platforms: 'weixin,douyin',
    domains: '科技',
    styles: '犀利',
    custom_tags: '行业观察',
    chunk_count: 4,
    created_at: '2026-10-05 16:08',
  },
  {
    id: 6,
    title: '一份可复用的副业变现清单（含热点评估维度）',
    platforms: 'weixin,xhs,douyin',
    domains: '财经',
    styles: '专业',
    custom_tags: '变现',
    chunk_count: 3,
    created_at: '2026-10-08 10:00',
  },
  {
    id: 7,
    title: '油皮夏季控油底妆实测：5 款持妆粉底上脸对比',
    platforms: 'xhs,douyin',
    domains: '美妆',
    styles: '轻松',
    custom_tags: '测评,底妆',
    chunk_count: 3,
    created_at: '2026-10-09 13:20',
  },
  {
    id: 8,
    title: '零基础转行数据分析：90 天学习路线与避坑指南',
    platforms: 'weixin,xhs',
    domains: '教育',
    styles: '专业',
    custom_tags: '转行,学习路线',
    chunk_count: 4,
    created_at: '2026-10-11 09:40',
  },
]

// 自动补全：保证 DOMAINS 中每个领域至少有一条素材，
// 未来在 types.ts 的 DOMAINS 新增领域时无需手动补 Mock 数据，从根上避免筛选空白。
const matCovered = new Set(curatedMaterials.map((m) => m.domains))
const materials: any[] = [
  ...curatedMaterials,
  ...DOMAINS.filter((d) => !matCovered.has(d)).map((d, i) => ({
    id: 900 + i,
    title: `${d}领域示例素材（自动补全）`,
    platforms: 'weixin,xhs,douyin',
    domains: d,
    styles: '专业',
    custom_tags: '示例',
    chunk_count: 2,
    created_at: '2026-10-12 00:00',
  })),
]

let persona: any = {
  display_name: 'AI 效率观察',
  positioning: '为内容创作者提供可用的 AI 效率工具与方法论',
  tone: '专业、克制、有温度',
  domains: DOMAINS.join(','),
}

// ----------------- 热点 Mock（P2-5 保真） -----------------
// 字段必须与真实 GET /hotspots 一致，否则前端只有接后端才能跑通，
// 而 Mock 恰恰是用来"没后端也能演示"的——两者一旦漂移，Mock 就失去了意义。
// 时间按"相对现在"生成：固定日期会让演示过几天就变成「300 天前首次出现」。
// 注意必须输出**本地时间**字符串——后端返回的就是本地时间，用 toISOString() 会差 8 小时，
// 表现为"首次 35 分钟前"被显示成"首次 8 小时前"。
const NOW = Date.now()
const pad = (n: number) => String(n).padStart(2, '0')
const localTime = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
  `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
const minsAgo = (m: number) => localTime(new Date(NOW - m * 60000))
const hoursAgo = (h: number) => minsAgo(h * 60)

const FRESHNESS = {
  updated_at: minsAgo(8),
  age_minutes: 8,
  degraded: false,
  sources_ok: 6,
  sources_total: 6,
}

// 演示标注由页面统一展示（Hotspots.tsx 读 USE_MOCK），数据本身不伪装成真实采集结果
const curatedHotspots: any[] = [
  {
    id: 1, signal_id: 101, domain: '科技', platform: 'weibo', source: '微博热搜',
    title: '端侧大模型本地运行成为新热点',
    summary: '多家厂商发布可在手机本地运行的小参数模型，隐私与离线场景受关注。',
    url: 'https://s.weibo.com/weibo?q=%E7%AB%AF%E4%BE%A7%E5%A4%A7%E6%A8%A1%E5%9E%8B',
    heat: 9821, norm_heat: 92, source_count: 3, best_rank: 2, trend: '上升',
    is_new: 1, first_seen_at: minsAgo(35), collected_at: minsAgo(8),
    suggestion: '断网跑同一句提问，掐表比本地和云端的延迟差',
  },
  {
    id: 2, signal_id: 102, domain: '美妆', platform: 'xhs', source: '小红书热榜',
    title: '早 C 晚 A 护肤翻车，敏感肌避雷清单',
    summary: '成分党热度下，新手盲目叠加酸类与维 A 导致屏障受损的案例增多，避雷清单类内容高收藏。',
    url: '',
    heat: 8210, norm_heat: 81, source_count: 3, best_rank: 1, trend: '上升',
    is_new: 1, first_seen_at: minsAgo(50), collected_at: minsAgo(8),
    suggestion: '把自己翻车那两周的护肤步骤按天列出来，标出踩雷节点',
  },
  {
    id: 3, signal_id: 103, domain: '生活', platform: 'baidu', source: '百度热搜',
    title: 'AI 自动化工作流进入平民化阶段',
    summary: '低代码 + 智能体让非技术用户也能搭建自动化流程，办公提效成主卖点。',
    url: '',
    heat: 7654, norm_heat: 84, source_count: 2, best_rank: 4, trend: '上升',
    is_new: 1, first_seen_at: hoursAgo(3), collected_at: minsAgo(8),
    suggestion: '挑一件每周都干的重复活，现场搭一遍给人看',
  },
  {
    id: 4, signal_id: 104, domain: '教育', platform: 'zhihu', source: '知乎热榜',
    title: 'AI 编程课真假难辨，成人自学路径图走红',
    summary: '成人再就业需求下，零基础自学编程的路径规划与资源筛选内容搜索走高。',
    url: '',
    heat: 7012, norm_heat: 74, source_count: 2, best_rank: 5, trend: '上升',
    is_new: 1, first_seen_at: hoursAgo(5), collected_at: minsAgo(8),
    suggestion: '花半小时扒一门课的课程大纲，看它教不教改报错',
  },
  {
    id: 5, signal_id: 105, domain: '财经', platform: 'toutiao', source: '今日头条热榜',
    title: '居民储蓄意愿上升，理财内容需求回暖',
    summary: '市场波动下，普通人的资产配置与记账类内容搜索量走高。',
    url: '',
    heat: 6890, norm_heat: 69, source_count: 1, best_rank: 3, trend: '平稳',
    is_new: 1, first_seen_at: minsAgo(90), collected_at: minsAgo(8),
    suggestion: '拿自己的记账本给人看：钱到底停在哪三类支出',
  },
  {
    id: 6, signal_id: 106, domain: '财经', platform: 'weibo', source: '微博热搜',
    title: '美联储加息预期扰动权益资产',
    summary: '降息交易降温，权益类资产波动加大，普通投资者关注仓位与回撤。',
    url: '',
    heat: 6550, norm_heat: 66, source_count: 2, best_rank: 9, trend: '上升',
    is_new: 0, first_seen_at: hoursAgo(12), collected_at: minsAgo(8),
    suggestion: '把持仓分成加息一次、两次、不加息三档算回撤',
  },
  {
    id: 7, signal_id: 107, domain: '科技', platform: 'bilibili', source: 'B站热搜',
    title: '开源模型商用授权争议',
    summary: '新发布权重在商用条款上收紧，引发创作者合规顾虑。',
    url: '',
    heat: 4310, norm_heat: 63, source_count: 2, best_rank: 11, trend: '下降',
    is_new: 0, first_seen_at: hoursAgo(20), collected_at: minsAgo(8),
    suggestion: '把三类常见授权条款翻成人话，标出踩线动作',
  },
  {
    id: 8, signal_id: 108, domain: '生活', platform: 'zhihu', source: '知乎热榜',
    title: '短视频开头 3 秒公式再被验证',
    summary: '完播率焦虑下，创作者集中讨论钩子设计与情绪前置。',
    url: '',
    heat: 6890, norm_heat: 78, source_count: 1, best_rank: 6, trend: '平稳',
    is_new: 0, first_seen_at: hoursAgo(10), collected_at: minsAgo(8),
    suggestion: '拿三条爆款逐帧拆前 3 秒，标出钩子类型',
  },
  {
    id: 9, signal_id: 109, domain: '生活', platform: 'xhs', source: '小红书热榜',
    title: '知识管理从收藏夹回归输出',
    summary: '「第二大脑」热度回落，强调以输出驱动输入的实践派受追捧。',
    url: '',
    heat: 5432, norm_heat: 72, source_count: 2, best_rank: 8, trend: '上升',
    is_new: 0, first_seen_at: hoursAgo(26), collected_at: minsAgo(8),
    suggestion: '晒一次"收藏 300 条只用了 3 条"的复盘账',
  },
  {
    id: 10, signal_id: 110, domain: '生活', platform: 'weibo', source: '微博热搜',
    title: '多平台一键分发工具走红',
    summary: '同一篇内容按平台规范自动改写并排期，成为中小创作者刚需。',
    url: '',
    heat: 3876, norm_heat: 58, source_count: 1, best_rank: 13, trend: '上升',
    is_new: 0, first_seen_at: hoursAgo(30), collected_at: minsAgo(8),
    suggestion: '同一篇稿子发三个平台，对比后台数据的差异',
  },
  {
    // 「综合」样例：喂给「其他」入口，保证二期新增的入口不是死链
    id: 11, signal_id: 111, domain: '综合', platform: 'toutiao', source: '今日头条热榜',
    title: '多地发布暴雨红色预警',
    summary: '强降雨影响出行与农业生产，多地启动应急响应。',
    url: '',
    heat: 9300, norm_heat: 88, source_count: 1, best_rank: 2, trend: '上升',
    is_new: 1, first_seen_at: minsAgo(15), collected_at: minsAgo(8),
    suggestion: '从「通勤改线怎么算时间」这个具体处境切入',
  },
]

// 自动补全：保证 DOMAINS 中每个领域至少有一条热点，避免筛选空白（同上）。
const hCovered = new Set(curatedHotspots.map((h) => h.domain))
const hotspots: any[] = [
  ...curatedHotspots,
  ...DOMAINS.filter((d) => !hCovered.has(d)).map((d, i) => ({
    id: 900 + i,
    signal_id: 900 + i,
    domain: d,
    platform: 'weibo',
    source: '微博热搜',
    heat: 3000,
    norm_heat: 50,
    source_count: 1,
    best_rank: 12,
    title: `${d}领域热点（演示数据）`,
    summary: `这是为「${d}」领域自动生成的示例热点，用于保证筛选器在所有官方领域下均有数据。`,
    url: '',
    trend: '平稳',
    is_new: 1,
    first_seen_at: minsAgo(10),
    collected_at: minsAgo(8),
    suggestion: `从「${d}」里最容易被误判的那个常识切入`,
  })),
]

// ----------------- 通用响应 -----------------
function ok(data: any) {
  return { ok: true, ...data }
}

// 相对时间：与页面展示口径一致（"35 分钟前"而不是一串时间戳）
function relFrom(s?: string): string {
  if (!s) return '—'
  const m = Math.round((Date.now() - new Date(s.replace(' ', 'T')).getTime()) / 60000)
  if (!Number.isFinite(m)) return '—'
  if (m < 60) return `${Math.max(1, m)} 分钟前`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h} 小时前` : `${Math.floor(h / 24)} 天前`
}

// 全榜视图与后端一致：不带 relevance/reason（避免用户以为全榜也按账号排过序）
function stripRank(h: any) {
  const rest = { ...h }
  delete rest.relevance
  delete rest.reason
  return rest
}

// 采纳/曝光状态（Mock 内存态 + localStorage 兜底）
// 为什么要落 localStorage：真实后端把采纳写进 hot_feed_event，刷新页面照样记得「已用过」；
// Mock 若只放内存，用户一刷新就"失忆"，演示时会被当成 bug。曝光计数不落盘——它是纯统计，无需跨刷新。
const ADOPT_KEY = 'mock_adopted_hotspots'
function loadAdopted(): Set<number> {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(ADOPT_KEY) : null
    return new Set<number>(raw ? (JSON.parse(raw) as number[]) : [])
  } catch {
    return new Set<number>()
  }
}
const adoptedIds = loadAdopted()
function saveAdopted() {
  try {
    localStorage.setItem(ADOPT_KEY, JSON.stringify([...adoptedIds]))
  } catch {
    /* 隐私模式禁用存储时忽略 */
  }
}
let mockExposed = 0

// ----------------- 路由分发 -----------------
export const mockApi = {
  async get(path: string): Promise<any> {
    await sleep(280)
    if (path.startsWith('/hotspots/status')) {
      // 保持契约一致：ctr 在没有曝光时是 null（不是 0）——空值当 0 会让人误判"没人点"
      return ok({
        ok: true,
        freshness: FRESHNESS,
        sources: [],
        window_days: 7,
        total_runs: 0,
        success_rate: null,
        p95_elapsed_ms: null,
        adoption: {
          window_days: 7,
          impressions: mockExposed,
          adopts: adoptedIds.size,
          generates: 0,
          ctr: mockExposed ? Number((adoptedIds.size / mockExposed).toFixed(4)) : null,
          adopt_to_generate: adoptedIds.size ? 0 : null,
          active_users: 1,
          top_adopted: [],
          note: '演示数据',
        },
      })
    }
    if (path.startsWith('/hotspots')) {
      const u = new URLSearchParams(path.split('?')[1] || '')
      const view = u.get('view') || 'feed'
      const d = u.get('domain')
      const p = u.get('platform')
      const newOnly = u.get('new_only') === 'true'
      // 与后端同口径：feed 默认剔除「综合」，all 默认包含；显式传入时以传入为准
      const includeMisc = u.has('include_misc') ? u.get('include_misc') === 'true' : view === 'all'
      const limit = Math.max(1, Math.min(Number(u.get('limit') || 20), 100))

      let list = hotspots.filter((h) => includeMisc || h.domain !== MISC_DOMAIN)
      if (d) list = list.filter((h) => h.domain === d)
      if (p) list = list.filter((h) => h.platform === p)
      if (newOnly) list = list.filter((h) => h.is_new === 1)

      if (view === 'feed') {
        // 推荐视图：与后端 5 因子同形（领域命中已在筛选里体现，这里近似打分）
        list = list
          .map((h) => ({
            ...h,
            relevance: Math.round(
              (h.norm_heat || 0) * 0.6 + (h.is_new ? 16 : 0) + Math.min(3, h.source_count || 1) * 6,
            ),
            reason: `${
              h.domain === MISC_DOMAIN ? '跨领域，需自行判断' : `命中${h.domain}`
            } · ${h.source_count || 1} 个平台在讨论 · 首次 ${relFrom(h.first_seen_at)}`,
          }))
          .sort((a, b) => b.relevance - a.relevance)
      } else {
        list = [...list].sort((a, b) => (b.norm_heat || 0) - (a.norm_heat || 0))
      }

      const sliced = list.slice(0, limit)
      return ok({
        count: sliced.length,
        view,
        personalized: view === 'feed',
        include_misc: includeMisc,
        freshness: FRESHNESS,
        // adopted：与真实后端同字段（来自 hot_feed_event 的「我用这条出过选题」）
        hotspots: (view === 'feed' ? sliced : sliced.map(stripRank)).map((h) => ({
          ...h,
          adopted: adoptedIds.has(h.id),
        })),
      })
    }
    if (path.startsWith('/materials')) {
      const u = new URLSearchParams(path.split('?')[1] || '')
      const q = (u.get('q') || '').toLowerCase()
      const list = q
        ? materials.filter(
            (m) =>
              m.title.toLowerCase().includes(q) ||
              (m.custom_tags || '').toLowerCase().includes(q),
          )
        : materials
      if (/\/chunks$/.test(path)) {
        const id = Number(path.split('/')[2])
        const m = materials.find((x) => x.id === id)
        const n = m?.chunk_count || 3
        const chunks = Array.from({ length: n }).map((_, i) => ({
          index: i,
          char_count: 180 + i * 24,
          content: `[分块 ${i + 1}] 这是素材《${m?.title || '示例素材'}》的第 ${i + 1} 段模拟内容。系统按句边界自动切分，单块不超过 300 字，并加密存储以支持语义检索与合规审计。`,
        }))
        return ok({ chunks })
      }
      return ok({ results: list })
    }
    if (path.startsWith('/persona')) {
      return ok({ persona })
    }
    return ok({})
  },

  async post(path: string, body: any): Promise<any> {
    await sleep(360)
    if (path.startsWith('/auth/login') || path.startsWith('/auth/register')) {
      tokenCounter += 1
      return ok({
        access_token: `mock-token-${tokenCounter}`,
        token_type: 'bearer',
        username: body?.username || 'demo_seed',
      })
    }
    // 推荐流事件：expose 记曝光条数、adopt 记"已用过"（Mock 内存态，刷新即重置）
    if (path.startsWith('/hotspots/events')) {
      const action = body?.action
      if (action === 'expose') {
        mockExposed += Math.max(0, Math.min(Number(body?.count) || 0, 100))
        return ok({ action, recorded: 1 })
      }
      if (action === 'adopt') {
        const id = Number(body?.hotspot_id)
        if (!id) return { ok: false, msg: 'adopt 需要 hotspot_id' }
        const dup = adoptedIds.has(id)
        adoptedIds.add(id)
        saveAdopted()
        return ok({ action, recorded: dup ? 0 : 1, deduped: dup })
      }
      return { ok: false, msg: 'action 只支持 expose / adopt' }
    }
    // 手动刷新：Mock 下不产生真实采集，只回一个与后端同形的结果
    if (path.startsWith('/hotspots/refresh')) {
      return ok({
        batch_id: 'mock-batch',
        reused: false,
        collected: 0,
        by_source: {},
        failed: [],
        skipped: [],
        degraded: false,
        elapsed_ms: 120,
      })
    }
    if (path.startsWith('/materials')) {
      const id = Number(path.split('/')[2])
      if (path.endsWith('/import')) {
        matId += 1
        const content = body?.content || ''
        const newMat = {
          id: matId,
          title: body?.title || '未命名素材',
          platforms: body?.platforms || '',
          domains: body?.domains || '',
          styles: body?.styles || '',
          custom_tags: body?.custom_tags || '',
          chunk_count: Math.max(1, Math.ceil(content.length / 200)),
          created_at: new Date().toISOString().slice(0, 16).replace('T', ' '),
        }
        materials.unshift(newMat)
        return ok({ id: newMat.id })
      }
      if (path.endsWith('/tag')) {
        const m = materials.find((x) => x.id === id)
        if (m) {
          if (body?.platforms) m.platforms = body.platforms
          if (body?.domains) m.domains = body.domains
          if (body?.styles) m.styles = body.styles
          if (body?.custom_tags) m.custom_tags = body.custom_tags
        }
        return ok({})
      }
      if (path.endsWith('/embed')) {
        const m = materials.find((x) => x.id === id)
        return ok({ rebuilt_vectors: m?.chunk_count || 0 })
      }
    }
    if (path.startsWith('/persona')) {
      persona = {
        display_name: body?.display_name ?? persona.display_name,
        positioning: body?.positioning ?? persona.positioning,
        tone: body?.tone ?? persona.tone,
        domains: body?.domains ?? persona.domains,
      }
      return ok({})
    }
    if (path.startsWith('/topics/generate')) {
      const count = body?.count || 3
      // 热点直传：与后端一致，优先用该热点的主领域与标题（否则 Mock 下闭环看起来通、领域是错的）
      const hot = body?.signal_id
        ? hotspots.find((h) => h.signal_id === body.signal_id || h.id === body.signal_id)
        : undefined
      const domain = hot?.domain || body?.domain
      const titles = [
        'AI 写作会不会取代自媒体人？一线创作者访谈',
        '我用 AI 重写了团队周报流程，每周省下 6 小时',
        '从 0 到 1 搭建个人知识库的踩坑清单',
        '短视频开头 3 秒公式：钩子设计实操',
        '端侧大模型本地运行，创作者能用到哪一步',
        '多平台一键分发：同一篇内容如何改写出彩',
        '本地优先软件回潮，效率工具该不该上云',
        '开源模型商用授权收紧，创作者合规清单',
      ]
      const reasons = [
        '近期「AI 取代创作者」讨论度高，适合观点型内容建立立场。',
        '自动化提效是确定性需求，案例类内容易引发收藏。',
        '知识管理话题长尾流量稳定，清单体适合新手。',
        '完播率焦虑普遍，方法论内容转化率高。',
        '端侧部署是技术热点，可下沉为非技术读者科普。',
      ]
      const audiences = ['关注效率的职场人', '内容创作者', 'AI 工具爱好者', '个人成长型用户']
      const angles = ['成本视角', '实操步骤', '避坑清单', '趋势判断', '对比评测']
      const seeded = hot
        ? [{
            id: topicId++,
            title: `从「${hot.title}」聊聊${hot.domain}账号的切入角度`,
            reason: `该热点 ${hot.source_count || 1} 个平台在讨论，与账号覆盖领域（${hot.domain}）匹配，适合快速跟进。`,
            target_audience: audiences[0],
            angle: hot.suggestion || angles[0],
            domain: hot.domain,
          }]
        : []
      const generic = Array.from({ length: Math.max(3, count) }).map((_, i) => ({
        id: topicId++,
        title: titles[i % titles.length],
        reason: reasons[i % reasons.length],
        target_audience: audiences[i % audiences.length],
        angle: angles[i % angles.length],
        domain: domain || DOMAINS[i % DOMAINS.length],
      }))
      const topics = [...seeded, ...generic].slice(0, Math.max(3, count))
      return ok({ domain, signal_id: body?.signal_id, topics })
    }
    if (path.startsWith('/topics/') && path.endsWith('/select')) {
      return ok({})
    }
    if (path.startsWith('/copy/') && path.endsWith('/optimize')) {
      const taskId = Number(path.split('/')[2])
      const n = taskId ? 3 : 3
      const versions = Array.from({ length: n }).map((_, i) => ({
        version_no: i + 1,
        body: `【风格优化版 ${i + 1}】围绕选题，本版本在口吻上更贴近「${body?.tone || '专业'}」，篇幅控制在「${body?.length || '中'}」，并融入账号人设表达。已由规则引擎完成一致性校验，替换建议已附。`,
        style_consistency: ['高', '中高', '高'][i % 3],
        replacement_suggestions: { 可能: '建议', 非常: '尤为' },
      }))
      return ok({ versions })
    }
    if (path.startsWith('/copy/') && path.endsWith('/adapt')) {
      const taskId = Number(path.split('/')[2])
      const platform = body?.platform || 'weixin'
      const n = taskId ? 3 : 3
      const layout: Record<string, string> = { weixin: '图文长文', xhs: '图文笔记', douyin: '口播脚本' }
      const fmt: Record<string, string> = { weixin: 'txt', xhs: 'txt', douyin: 'txt' }
      const note: Record<string, string> = {
        weixin: '发布备注：正文建议配 3 图，首段含关键词；原创声明可开启。',
        xhs: '发布备注：首图竖版 3:4，标题含 2 个 emoji 与 1 个话题；正文末引导收藏。',
        douyin: '发布备注：前 3 秒抛冲突钩子；口播脚本按句断行，配字幕更友好。',
      }
      const versions = Array.from({ length: n }).map((_, i) => ({
        version_no: i + 1,
        id: 1000 + i,
        platform,
        title: `【${platformLabel(platform)}】第 ${i + 1} 版标题`,
        body: `这是针对${platformLabel(platform)}适配后的第 ${i + 1} 版正文。\n\n已按平台排版规范（${layout[platform]}）调整结构，${
          platform === 'douyin' ? '改为口语化分镜脚本。' : '保留图文可读性。'
        }\n\n正文末附话题标签与互动引导，符合平台分发偏好。`,
        tags: ['AI创作', '效率', platformLabel(platform)],
        layout_style: layout[platform],
        export_format: fmt[platform],
        truncated: false,
        note: note[platform],
      }))
      return ok({ versions })
    }
    return ok({})
  },
}

// ----------------- SSE 流式生成（模拟） -----------------
export async function mockStreamGenerate(
  req: any,
  onEvent: (e: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const n = req?.n_versions || 3
  const plats: string[] = req?.target_platforms || ['weixin']
  const taskId = Math.floor(Math.random() * 9000) + 1000

  onEvent({ type: 'start', task_id: taskId })
  await sleep(500)
  if (signal?.aborted) return

  const snippets = materials.slice(0, 3).map((m) => m.title)
  onEvent({ type: 'retrieved', snippets })
  await sleep(400)
  if (signal?.aborted) return

  for (let i = 0; i < n; i++) {
    if (signal?.aborted) return
    await sleep(600)
    const adapted: Record<string, string> = {}
    plats.forEach((p) => {
      adapted[p] = `【${platformLabel(p)}】${req?.topic_title || '选题'}·第${i + 1}版短标题`
    })
    onEvent({
      type: 'version',
      version_no: i + 1,
      title: `版本 ${i + 1}：${req?.topic_title || 'AI 内容创作实战'}`,
      optimized_body: `这是第 ${i + 1} 个候选版本（共 ${n} 个）。\n\n围绕「${req?.topic_title || '选题'}」，本版本面向「${req?.audience || '关注效率的人群'}」，采用「${req?.tone || '专业'}」口吻，篇幅「${req?.length || '中'}」，创作意图为「${req?.intent || '干货科普'}」。\n\n正文逐段可编辑，并在下方给出平台适配标题与一致性评分，方便你横向对比择优。`,
      tags: ['AI', '效率', req?.intent || '干货'],
      adapted,
      style_consistency: ['高', '中高', '高'][i % 3],
      replacement_suggestions: { 非常: '尤为', 可能: '建议' },
    })
  }
  onEvent({
    type: 'done',
    task_id: taskId,
    generation_seconds: 1.8,
    target_platforms: plats,
  })
}

// ----------------- 导出（模拟文件下载） -----------------
export async function mockDownloadExport(req: { task_id: number; platform?: string; format: 'txt' | 'docx' }): Promise<void> {
  await sleep(400)
  const platformText = req.platform ? platformLabel(req.platform) : '全部平台'
  const content = `AI 自媒体内容创作助手 · 导出样例\n任务 ID：${req.task_id}\n平台：${platformText}\n格式：${req.format.toUpperCase()}\n\n（这是 Mock 导出内容，用于前端预览。接入真实后端后将输出与适配结果一致的成品。）\n`
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `copy_task_${req.task_id}_${req.platform || 'all'}.${req.format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
