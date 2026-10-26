import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Skeleton } from 'antd'
import { api, getToken } from '../api/client'
import type { Freshness, Hotspot } from '../api/types'
import { DOMAINS, HOT_SOURCES, MISC_DOMAIN, hotSourceLabel } from '../api/types'
import { USE_MOCK } from '../api/mock'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { InboxIcon, SparkIcon } from '../components/icons'

type View = 'feed' | 'all' | 'platform'

const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: 'feed', label: '为你推荐', hint: '按领域命中 / 新鲜度 / 多平台覆盖排序，只留你可能写得动的' },
  { key: 'all', label: '全部', hint: '今日全部在榜话题（含未归类到垂类的），按热度排' },
  { key: 'platform', label: '按来源', hint: '看看某个平台今天在热议什么' },
]

/** 相对时间：热点的价值随分钟衰减，"3 小时前"比 "2026-10-22 10:52" 有用得多 */
function relTime(s?: string | null): string {
  if (!s) return ''
  const t = new Date(s.replace(' ', 'T')).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.floor(Math.max(0, Date.now() - t) / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

export default function Hotspots() {
  const navigate = useNavigate()
  const message = useMessage()
  const [view, setView] = useState<View>('feed')
  const [domain, setDomain] = useState('')
  const [platform, setPlatform] = useState(HOT_SOURCES[0].code)
  const [newOnly, setNewOnly] = useState(false)
  const [list, setList] = useState<Hotspot[]>([])
  const [freshness, setFreshness] = useState<Freshness | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  // 建议角度是后端每请求最多补 N 条的（控成本）。这里主动补两轮，
  // 让首屏的「建议角度生成中」真的会填上，而不是永远停在那句话上。
  const fillRounds = useRef(0)
  // 曝光去重：同一屏内容只上报一次（补建议角度会再 fetch 一轮，见下）
  const exposedSig = useRef('')

  async function fetchList(first = false) {
    if (first) setLoading(true)
    try {
      const qs = new URLSearchParams()
      // 后端只有 feed / all 两个视图；「按来源」用 all + platform 过滤表达，
      // 避免为一个筛选再开一个视图口径（口径越多，两边越容易走偏）
      qs.set('view', view === 'feed' ? 'feed' : 'all')
      qs.set('limit', '30')
      if (domain) qs.set('domain', domain)
      if (view === 'platform') qs.set('platform', platform)
      if (newOnly) qs.set('new_only', 'true')
      // 推荐流不含「综合」；另外两个视图要看全量，否则用户找不到"其他"
      if (view !== 'feed') qs.set('include_misc', 'true')

      const r: any = await api.get(`/hotspots?${qs.toString()}`)
      const rows: Hotspot[] = r.hotspots || []
      setList(rows)
      setFreshness(r.freshness || null)

      // 曝光上报（采纳率的分母）：只报"真的画出来了的这一屏"。
      // 坑：下面补建议角度会再 fetch 一次，若每次都报，分母会被自己灌一倍，
      // CTR 随即腰斩——所以用内容签名去重，同一屏只算一次曝光。
      const sig = `${view}|${domain}|${platform}|${newOnly}|${rows.length}|${rows[0]?.id ?? 0}|${rows.length ? rows[rows.length - 1].id : 0}`
      if (rows.length && sig !== exposedSig.current && getToken()) {
        exposedSig.current = sig
        // 埋点失败绝不能影响主流程，也不该弹提示
        void api
          .post('/hotspots/events', { action: 'expose', surface: view, count: rows.length })
          .catch(() => {})
      }

      const missing = rows.filter((h) => !h.suggestion).length
      if (missing > 0 && fillRounds.current < 2) {
        fillRounds.current += 1
        window.setTimeout(() => void fetchList(false), 700)
      }
    } catch (e: any) {
      message.error(e?.message || '加载失败')
    } finally {
      if (first) setLoading(false)
    }
  }

  /** 「用它做选题」：先记采纳（分子），再跳转。埋点失败不阻断创作路径。 */
  function adopt(h: Hotspot, index: number) {
    if (getToken()) {
      void api
        .post('/hotspots/events', {
          action: 'adopt',
          hotspot_id: h.id,
          surface: view,
          position: index + 1,
        })
        .catch(() => {})
    }
    // 本地先打标，避免返回看板时还得等一次请求才知道"已用过"
    setList((prev) =>
      prev.map((x) => (x.id === h.id ? { ...x, adopted: true } : x)),
    )
    navigate('/topics', {
      state: {
        signalId: h.signal_id ?? h.id,
        title: h.title,
        domain: h.domain,
        suggestion: h.suggestion,
      },
    })
  }

  useEffect(() => {
    fillRounds.current = 0
    fetchList(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, domain, platform, newOnly])

  async function refresh() {
    setRefreshing(true)
    try {
      const r: any = await api.post('/hotspots/refresh', { force: false })
      const n = r?.collected ?? 0
      message.success(r?.reused ? '本批次已采集过，直接复用' : `已刷新，采集 ${n} 条`)
      await fetchList(false)
    } catch (e: any) {
      // 频控（429）时后端会把剩余秒数写进 msg，这里原样透出，别自己编"稍后再试"
      message.warning(e?.message || '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }

  const chip = (active: boolean, extra = '') =>
    `px-3.5 py-1.5 rounded-full text-[13px] border transition-colors duration-200 ${extra} ${
      active
        ? 'bg-brand text-white border-brand shadow-sm'
        : 'bg-white text-ink/70 border-line hover:border-brand/40 hover:text-brand'
    }`

  const age = freshness?.age_minutes
  // 分钟数只在 1 小时内可读；再往上必须换算，否则会出现「5718 分钟前更新」这种没人读得懂的文案
  const updatedText =
    age == null
      ? '暂无采集记录'
      : age < 1
      ? '刚刚更新'
      : age < 60
      ? `${age} 分钟前更新`
      : age < 60 * 24
      ? `${Math.floor(age / 60)} 小时前更新`
      : `${Math.floor(age / 1440)} 天前更新`
  const sourcesText = freshness
    ? `${freshness.sources_ok}/${freshness.sources_total} 个来源可用`
    : ''
  // 本屏里已经用过的条数（不发请求，直接用列表自带的 adopted）
  const adoptedCount = list.filter((h) => h.adopted).length

  const currentHint = VIEWS.find((v) => v.key === view)?.hint || ''

  return (
    <div>
      <PageHeader
        title="热点看板"
        subtitle={
          USE_MOCK
            ? '演示数据 · 自动采集 · 跨平台合并 · 按你的领域推荐'
            : '自动采集 · 跨平台合并 · 按你的领域推荐'
        }
        extra={
          <div className="flex items-center gap-3">
            <div className="text-right leading-tight">
              <div className="text-[12px] text-muted">
                {updatedText}
                {sourcesText && <span className="text-muted-soft"> · {sourcesText}</span>}
              </div>
              <div className="text-[11px] text-muted-soft">
                {adoptedCount > 0 ? `本页已用过 ${adoptedCount} 条` : '自动更新，也可手动刷新'}
              </div>
            </div>
            {/* 低权重刷新：这是"加速"而不是主操作，不应该出现在首屏主按钮位 */}
            <button
              onClick={refresh}
              disabled={refreshing}
              title="立即刷新热点"
              aria-label="立即刷新热点"
              className="w-9 h-9 rounded-full border border-line bg-white text-muted hover:text-brand hover:border-brand/40 transition disabled:opacity-50 flex items-center justify-center"
            >
              <svg
                viewBox="0 0 24 24"
                className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`}
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                <path d="M21 3v6h-6" />
              </svg>
            </button>
          </div>
        }
      />

      {/* 演示模式提示已由布局层统一给出（顶部黄条），此处不再重复一条 */}
      {freshness?.degraded && (
        <div className="mb-4 px-4 py-3 rounded-card bg-[#fdf6e7] border border-[#f2e2c0] text-[13px] text-warn">
          实时采集暂不可用
          {freshness.updated_at ? `，以下是 ${freshness.updated_at.slice(0, 16)} 的数据` : ''}
          。数据可能已过期，发布前请核对。
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            onClick={() => {
              setView(v.key)
              if (v.key === 'feed' && domain === MISC_DOMAIN) setDomain('')
            }}
            className={chip(view === v.key)}
          >
            {v.label}
          </button>
        ))}
        <span className="text-[12px] text-muted-soft ml-1">{currentHint}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-5">
        <button onClick={() => setDomain('')} className={chip(domain === '')}>
          全部领域
        </button>
        {DOMAINS.map((d) => (
          <button key={d} onClick={() => setDomain(d)} className={chip(domain === d)}>
            {d}
          </button>
        ))}
        {/* 「其他」= 未命中 5 个垂类的话题。推荐流按设计不返回它，
            所以只在全量视图提供——不给入口等于这批数据在界面上不存在 */}
        {view !== 'feed' && (
          <button
            onClick={() => setDomain(MISC_DOMAIN)}
            className={chip(domain === MISC_DOMAIN)}
          >
            其他
          </button>
        )}
        {view !== 'feed' && (
          <button
            onClick={() => setNewOnly(!newOnly)}
            className={chip(newOnly, 'ml-auto')}
          >
            只看新上榜
          </button>
        )}
        {view === 'platform' && (
          <div className="flex flex-wrap items-center gap-2 w-full mt-1">
            {HOT_SOURCES.map((s) => (
              <button
                key={s.code}
                onClick={() => setPlatform(s.code)}
                className={chip(platform === s.code, 'text-[12.5px] px-3 py-1')}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 命中太少时不偷偷补「综合」：真实热榜的「综合」几乎全是时政/体育/娱乐，
          补进来等于把刚过滤掉的东西换个位置塞回去。说清楚 + 给入口，由用户决定 */}
      {!loading && view === 'feed' && !domain && list.length > 0 && list.length < 6 && (
        <div className="mb-4 px-4 py-3 rounded-card bg-bg border border-line flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <span>
            今天命中你领域的热点只有 {list.length} 条，其余多为时政 / 体育 / 娱乐（推荐流默认不推）。
          </span>
          <button onClick={() => setView('all')} className="text-brand hover:underline">
            去「全部」看看全网热榜 →
          </button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-white border border-line rounded-card p-4 shadow-card">
              <Skeleton active paragraph={{ rows: 3 }} />
            </div>
          ))}
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          icon={<InboxIcon className="w-10 h-10" />}
          title="这个筛选下暂时没有热点"
          hint={
            view === 'platform'
              ? `「${hotSourceLabel(platform)}」当前没有可用数据，换个来源看看，或点右上角刷新。`
              : '换个领域看看，或点右上角刷新拉取最新热榜。系统会自动重试，无需一直盯着。'
          }
          action={
            <button onClick={refresh} disabled={refreshing} className={chip(true)}>
              {refreshing ? '刷新中…' : '立即刷新'}
            </button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5">
          {list.map((h, i) => {
            const heat = Math.round(h.norm_heat ?? 0)
            return (
              <div
                key={h.id}
                className="group bg-white border border-line rounded-card p-4 shadow-card hover:shadow-soft hover:border-brand/30 hover:-translate-y-0.5 transition duration-200 flex flex-col"
              >
                <div className="flex items-center justify-between gap-2 mb-2.5">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span
                      className={`px-2 py-0.5 rounded-full text-[12px] font-medium shrink-0 ${
                        h.domain === MISC_DOMAIN
                          ? 'bg-bg text-muted'
                          : 'bg-brand-soft text-brand'
                      }`}
                      title={
                        h.domain === MISC_DOMAIN
                          ? '未命中 5 个垂类，需要你自己判断是否可写'
                          : `主领域：${h.domain}`
                      }
                    >
                      {h.domain === MISC_DOMAIN ? '其他' : h.domain}
                    </span>
                    {!!h.is_new && (
                      <span className="px-1.5 py-0.5 rounded bg-danger/10 text-danger text-[11px] font-medium shrink-0">
                        新
                      </span>
                    )}
                    {/* 「已用过」= 这条你已经出过选题。懒人博主最怕的不是没热点，
                        是同一个热点写了两遍——这个标记比任何推荐分数都实用 */}
                    {!!h.adopted && (
                      <span
                        className="px-1.5 py-0.5 rounded bg-bg text-muted text-[11px] font-medium shrink-0"
                        title="你已经用这条出过选题，再点会出新的角度"
                      >
                        已用过
                      </span>
                    )}
                  </div>
                  {/* 展示口径用归一化热度 + 榜内名次；原始 heat 跨源不可比，不再露给用户 */}
                  <div className="flex items-center gap-1.5 shrink-0" title={`归一化热度 ${heat} / 100`}>
                    <div className="h-1.5 w-14 rounded-full bg-line overflow-hidden">
                      <div
                        className="h-full bg-brand rounded-full"
                        style={{ width: `${Math.max(4, Math.min(100, heat))}%` }}
                      />
                    </div>
                    <span className="text-[12px] text-muted tabular-nums">{heat}</span>
                  </div>
                </div>

                <div className="font-semibold text-[15px] text-ink mb-1.5 leading-snug line-clamp-2">
                  {h.title}
                </div>
                <div className="text-muted text-[13px] line-clamp-2 leading-relaxed">
                  {h.summary}
                </div>

                {h.suggestion ? (
                  <div className="mt-2.5 px-2.5 py-2 rounded-lg bg-brand-soft/60 text-[12.5px] text-brand leading-relaxed">
                    <span className="font-medium">建议角度</span>
                    <span className="text-ink/80"> · {h.suggestion}</span>
                  </div>
                ) : (
                  <div className="mt-2.5 px-2.5 py-2 rounded-lg bg-bg text-[12.5px] text-muted-soft">
                    建议角度生成中，先看原文
                  </div>
                )}

                <div className="text-[12px] text-muted-soft mt-2.5 flex items-center flex-wrap gap-x-1.5">
                  <span>{hotSourceLabel(h.platform)}</span>
                  {h.best_rank ? <span>· 榜内第 {h.best_rank} 名</span> : null}
                  {/* 推荐视图的 reason 已经写了平台数与首次时间，这里不再重复一遍 */}
                  {!h.reason && (
                    <>
                      <span>· {h.source_count || 1} 个平台在讨论</span>
                      {h.first_seen_at && <span>· 首次 {relTime(h.first_seen_at)}</span>}
                    </>
                  )}
                </div>

                {h.reason && (
                  <div className="text-[12px] text-brand/80 mt-1.5 leading-relaxed">
                    {h.reason}
                  </div>
                )}

                <div className="mt-auto pt-3 flex items-center gap-2">
                  <button
                    onClick={() => adopt(h, i)}
                    className="px-3 py-1.5 rounded-lg bg-brand text-white text-[13px] hover:bg-brand-700 transition inline-flex items-center gap-1"
                    title={h.adopted ? '这条你已经写过，会生成新的角度与选题' : undefined}
                  >
                    <SparkIcon className="w-3.5 h-3.5" />
                    {h.adopted ? '再写一版' : '用它做选题'}
                  </button>
                  {h.url ? (
                    <a
                      href={h.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 rounded-lg border border-line text-[13px] text-muted hover:text-brand hover:border-brand/40 transition"
                    >
                      看原文 ↗
                    </a>
                  ) : (
                    <span className="px-3 py-1.5 rounded-lg border border-line text-[13px] text-muted-soft">
                      无原文链接
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
