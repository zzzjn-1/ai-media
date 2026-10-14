import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Select, Skeleton, Button } from 'antd'
import { api } from '../api/client'
import type { Topic } from '../api/types'
import { DOMAINS } from '../api/types'
import { useFlow } from '../store/FlowContext'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { SparkIcon } from '../components/icons'

export default function Topics() {
  const navigate = useNavigate()
  const { setTopic } = useFlow()
  const message = useMessage()
  const [topics, setTopics] = useState<Topic[]>([])
  const [domain, setDomain] = useState<string | undefined>(undefined)
  const [count, setCount] = useState(3)
  const [loading, setLoading] = useState(false)

  async function generate() {
    setLoading(true)
    try {
      const r: any = await api.post('/topics/generate', {
        domain: domain || undefined,
        count,
      })
      setTopics(r.topics || [])
    } catch (e: any) {
      message.error(e?.message || '生成失败')
    } finally {
      setLoading(false)
    }
  }

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
        <Button type="primary" onClick={generate} loading={loading} className="h-[38px]">
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
