import { useEffect, useState } from 'react'
import { Skeleton } from 'antd'
import { api } from '../api/client'
import type { Hotspot } from '../api/types'
import { DOMAINS } from '../api/types'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { FlameIcon, InboxIcon } from '../components/icons'

export default function Hotspots() {
  const [list, setList] = useState<Hotspot[]>([])
  const [loading, setLoading] = useState(true)
  const [domain, setDomain] = useState('')
  const message = useMessage()

  async function fetchList() {
    setLoading(true)
    try {
      const r: any = await api.get(
        `/hotspots?limit=50${domain ? `&domain=${encodeURIComponent(domain)}` : ''}`,
      )
      setList(r.hotspots || [])
    } catch (e: any) {
      message.error(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain])

  const filterBtn = (active: boolean) =>
    `px-3.5 py-1.5 rounded-full text-[13px] border transition-colors duration-200 ${
      active
        ? 'bg-brand text-white border-brand shadow-sm'
        : 'bg-white text-ink/70 border-line hover:border-brand/40 hover:text-brand'
    }`

  return (
    <div>
      <PageHeader title="热点看板" subtitle="定时采集去重 · 仅展示活跃热点" />

      <div className="flex flex-wrap gap-2 mb-5">
        <button onClick={() => setDomain('')} className={filterBtn(domain === '')}>
          全部
        </button>
        {DOMAINS.map((d) => (
          <button key={d} onClick={() => setDomain(d)} className={filterBtn(domain === d)}>
            {d}
          </button>
        ))}
      </div>

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
          title="暂无热点数据"
          hint="请先运行 data/clean_chunk.py 播种热点，或切换其他领域查看。"
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5">
          {list.map((h) => (
            <div
              key={h.id}
              className="group bg-white border border-line rounded-card p-4 shadow-card hover:shadow-soft hover:border-brand/30 hover:-translate-y-0.5 transition duration-200"
            >
              <div className="flex items-center justify-between mb-2.5">
                <span className="px-2 py-0.5 rounded-full bg-brand-soft text-brand text-[12px] font-medium">
                  {h.domain}
                </span>
                <span className="flex items-center gap-1 text-danger font-semibold text-[14px]">
                  <FlameIcon className="w-4 h-4" /> {h.heat}
                </span>
              </div>
              <div className="font-semibold text-[15px] text-ink mb-1.5 leading-snug">{h.title}</div>
              <div className="text-muted text-[13px] line-clamp-3 leading-relaxed">{h.summary}</div>
              <div className="text-[12px] text-muted mt-2.5 flex items-center justify-between">
                <span>趋势：{h.trend || '—'}</span>
                <span className="text-muted-soft">{h.collected_at?.slice(0, 10)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
