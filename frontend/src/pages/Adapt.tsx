import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Skeleton, Button } from 'antd'
import { api, downloadExport } from '../api/client'
import type { CopyVersion } from '../api/types'
import { platformLabel } from '../api/types'
import { useFlow } from '../store/FlowContext'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { DocIcon } from '../components/icons'

const filterBtn = (active: boolean) =>
  `px-3.5 py-1.5 rounded-full text-[13px] border transition-colors duration-200 ${
    active
      ? 'bg-brand text-white border-brand shadow-sm'
      : 'bg-white text-ink/70 border-line hover:border-brand/40 hover:text-brand'
  }`

export default function Adapt() {
  const navigate = useNavigate()
  const { taskId, targetPlatforms } = useFlow()
  const message = useMessage()
  const [active, setActive] = useState<string>(targetPlatforms[0] || 'weixin')
  const [data, setData] = useState<Record<string, CopyVersion[]>>({})
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState<string>('')

  async function loadPlatform(code: string) {
    if (!taskId) return
    setLoading(true)
    try {
      const r: any = await api.post(`/copy/${taskId}/adapt`, { platform: code })
      setData((prev) => ({ ...prev, [code]: r.versions || [] }))
    } catch (e: any) {
      message.error(`适配 ${platformLabel(code)} 失败：${e?.message || ''}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (taskId) {
      targetPlatforms.forEach((c) => loadPlatform(c))
      setActive(targetPlatforms[0] || 'weixin')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  if (!taskId) {
    return (
      <div>
        <PageHeader title="平台适配预览" />
        <EmptyState
          icon={<DocIcon className="w-10 h-10" />}
          title="尚无创作任务"
          hint="请先在「文案生成对比」生成版本，任务会自动带入本页。"
          action={
            <Button type="primary" onClick={() => navigate('/generate')}>
              去生成 →
            </Button>
          }
        />
      </div>
    )
  }

  const versions = data[active] || []

  async function onExport(format: 'txt' | 'docx', platform?: string) {
    setExporting(`${format}-${platform || 'all'}`)
    try {
      await downloadExport({ task_id: taskId!, platform, format })
    } catch (e: any) {
      message.error(e?.message || '导出失败')
    } finally {
      setExporting('')
    }
  }

  return (
    <div>
      <PageHeader
        title="平台适配预览"
        subtitle={`任务 ID ${taskId} · 同一创作按各平台规范分别适配`}
      />

      <div className="flex flex-wrap gap-2 mb-5">
        {targetPlatforms.map((c) => (
          <button
            key={c}
            onClick={() => {
              setActive(c)
              if (!data[c]) loadPlatform(c)
            }}
            className={filterBtn(active === c)}
          >
            {platformLabel(c)}
          </button>
        ))}
      </div>

      {loading && !versions.length ? (
        <div className="space-y-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="bg-white border border-line rounded-card p-4 shadow-card">
              <Skeleton active paragraph={{ rows: 4 }} />
            </div>
          ))}
        </div>
      ) : versions.length === 0 ? (
        <EmptyState
          icon={<DocIcon className="w-10 h-10" />}
          title="该平台暂无适配结果"
          hint="可切换其他平台查看，或返回「文案生成对比」重新生成。"
        />
      ) : (
        <div className="space-y-4">
          {/* 导出工具条 */}
          <div className="bg-white border border-line rounded-card shadow-card p-3 flex flex-wrap items-center gap-2">
            <span className="text-[13px] text-muted mr-1">
              导出当前平台（{platformLabel(active)}）：
            </span>
            <Button
              type="primary"
              onClick={() => onExport('txt', active)}
              loading={exporting === `txt-${active}`}
            >
              导出 txt
            </Button>
            <Button onClick={() => onExport('docx', active)} loading={exporting === `docx-${active}`}>
              导出 docx
            </Button>
            <span className="text-[13px] text-muted mx-2">|</span>
            <Button onClick={() => onExport('txt')} loading={exporting === 'txt-all'}>
              导出全部平台 txt
            </Button>
          </div>

          {versions.map((v) => (
            <div
              key={v.version_no}
              className="bg-white border border-line rounded-card shadow-card p-4"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-brand">版本 {v.version_no}</span>
                <span className="text-[12px] text-ok">
                  格式：{v.export_format?.toUpperCase() || 'TXT'}
                </span>
              </div>
              <div className="font-medium text-[15px] text-ink mb-2">{v.title}</div>
              <div className="text-[14px] whitespace-pre-wrap leading-relaxed text-ink/90">
                {v.body}
              </div>

              <div className="flex flex-wrap gap-1.5 mt-2">
                {(v.tags || []).map((t, i) => (
                  <span key={i} className="text-[12px] bg-bg text-muted px-2 py-0.5 rounded">
                    {t}
                  </span>
                ))}
              </div>

              <div className="flex flex-wrap gap-2 mt-2 text-[12px]">
                <span className="px-2 py-0.5 rounded bg-brand-soft text-brand">
                  排版：{v.layout_style || '默认'}
                </span>
                {v.truncated && (
                  <span className="px-2 py-0.5 rounded bg-amber-50 text-warn">已按规范截断</span>
                )}
              </div>

              {v.note && (
                <div className="mt-3 bg-bg border border-line rounded-lg p-3 text-[12px] text-muted whitespace-pre-wrap leading-relaxed">
                  {v.note}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
