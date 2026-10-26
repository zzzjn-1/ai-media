import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Select, Skeleton, Button } from 'antd'
import { api } from '../api/client'
import type { Topic } from '../api/types'
import { DOMAINS } from '../api/types'
import { useFlow } from '../store/FlowContext'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { SparkIcon } from '../components/icons'

// 从热点看板「用它做选题」跳转过来时携带的上下文
interface HotspotSeed {
  signalId?: number
  title?: string
  domain?: string
  suggestion?: string
}

export default function Topics() {
  const navigate = useNavigate()
  const location = useLocation()
  const { setTopic } = useFlow()
  const message = useMessage()
  const [topics, setTopics] = useState<Topic[]>([])
  const [domain, setDomain] = useState<string | undefined>(undefined)
  const [count, setCount] = useState(3)
  const [loading, setLoading] = useState(false)
  const [seed, setSeed] = useState<HotspotSeed | null>(null)
  // 严格模式（dev 下 React.StrictMode）会把 effect 跑两遍。这里是**昂贵的**副作用
  // （一次真实 LLM 出题，十几秒 + 真金白银），跑两遍还会让「出题数」翻倍，
  // 把采纳漏斗的第三级直接污染。用 location.key 做幂等键：同一次跳转只出一批题。
  const seededKey = useRef('')

  async function generate(opts?: { signalId?: number; domain?: string }) {
    setLoading(true)
    try {
      const d = opts?.domain ?? domain
      const r: any = await api.post('/topics/generate', {
        domain: d || undefined,
        count,
        // 热点直传：后端会以该热点为主输入，并用它的主领域覆盖这里的 domain
        signal_id: opts?.signalId,
      })
      setTopics(r.topics || [])
    } catch (e: any) {
      message.error(e?.message || '生成失败')
    } finally {
      setLoading(false)
    }
  }

  // 带热点上下文进来：自动按该热点出一批选题，用户不用再点一次「生成」
  useEffect(() => {
    const st = (location.state || {}) as HotspotSeed
    if (!st.signalId) return
    if (seededKey.current === location.key) return
    seededKey.current = location.key
    setSeed(st)
    const d = st.domain && DOMAINS.includes(st.domain) ? st.domain : undefined
    setDomain(d)
    generate({ signalId: st.signalId, domain: d })
    // location.key 变化才重新触发，避免 count 改动把直传上下文重置
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key])

  async function selectTopic(t: Topic) {
    try {
      await api.post(`/topics/${t.id}/select`)
    } catch {
      /* 选用失败不阻断跳转 */
    }
    setTopic(t.title)
    navigate('/generate')
  }

  return (
    <div>
      <PageHeader title="选题列表" subtitle="结合热点趋势 + 素材库 + 账号定位生成" />

      {seed && (
        <div className="mb-4 px-4 py-3 rounded-card bg-brand-soft/60 border border-brand/20 flex items-start gap-3">
          <span className="text-[12px] text-brand font-medium shrink-0 mt-0.5">来自热点</span>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] text-ink leading-snug">{seed.title}</div>
            {seed.suggestion && (
              <div className="text-[12px] text-muted mt-1 leading-relaxed">
                建议角度 · {seed.suggestion}
              </div>
            )}
          </div>
          <button
            onClick={() => setSeed(null)}
            className="text-[12px] text-muted hover:text-ink shrink-0"
            aria-label="清除热点上下文"
          >
            清除
          </button>
        </div>
      )}

      <div className="bg-white border border-line rounded-card shadow-card p-4 mb-5 flex flex-wrap items-end gap-4">
        <div className="w-[180px]">
          <label className="block text-[12px] text-muted mb-1.5">领域（可选）</label>
          <Select
            allowClear
            placeholder="全部"
            value={domain}
            onChange={(v) => setDomain(v)}
            options={DOMAINS.map((d) => ({ value: d, label: d }))}
            className="w-full"
          />
        </div>
        <div className="w-[140px]">
          <label className="block text-[12px] text-muted mb-1.5">数量</label>
          <Select
            value={count}
            onChange={(v) => setCount(v)}
            options={[3, 5, 8].map((n) => ({ value: n, label: `${n} 条` }))}
            className="w-full"
          />
        </div>
        <Button type="primary" onClick={() => generate()} loading={loading} className="h-[38px]">
          {loading ? '生成中…' : '生成选题'}
        </Button>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white border border-line rounded-card p-4 shadow-card">
              <Skeleton active paragraph={{ rows: 3 }} />
            </div>
          ))}
        </div>
      ) : topics.length === 0 ? (
        <EmptyState
          icon={<SparkIcon className="w-10 h-10" />}
          title="还没有选题"
          hint="设置领域与数量后点击「生成选题」，系统将结合热点与素材为你产出候选选题。"
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
          {topics.map((t) => (
            <div
              key={t.id}
              className="group bg-white border border-line rounded-card p-4 shadow-card hover:shadow-soft hover:border-brand/30 transition duration-200"
            >
              <div className="font-semibold text-[15px] text-ink mb-2 leading-snug">{t.title}</div>
              <div className="text-[13px] text-muted mb-1.5 leading-relaxed">
                <span className="text-ink font-medium">推荐理由：</span>
                {t.reason || '—'}
              </div>
              <div className="text-[13px] text-muted mb-1.5 leading-relaxed">
                <span className="text-ink font-medium">目标受众：</span>
                {t.target_audience || '—'}
              </div>
              {t.angle && (
                <div className="text-[13px] text-muted mb-2.5 leading-relaxed">
                  <span className="text-ink font-medium">切入角度：</span>
                  {t.angle}
                </div>
              )}
              <Button type="primary" size="small" onClick={() => selectTopic(t)} className="mt-1">
                一键选用 →
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
