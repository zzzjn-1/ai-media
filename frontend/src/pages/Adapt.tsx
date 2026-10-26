import { useEffect, useRef, useState } from 'react'
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
  const { taskId, targetPlatforms, setTask } = useFlow()
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

  // React.StrictMode 在 dev 下会把 effect 跑两遍 → 每个平台被适配两次（白烧一次 LLM 调用）。
  // 用 ref 记住已适配过的 taskId，第二次进入直接跳过。
  // ⚠️ 必须配 cleanup 复位：StrictMode 的第一次 mount 会在请求飞行途中被卸载，
  // 那次结果会被 alive 守卫丢掉；若 ref 还在开头就置位，第二次 mount 直接被挡住，
  // 页面就永远停在骨架屏（2026-10-26 实测踩到）。
  const adaptedTaskRef = useRef<number | null>(null)
  const [restoring, setRestoring] = useState(false)

  useEffect(() => {
    if (!taskId) return
    if (adaptedTaskRef.current === taskId) return
    adaptedTaskRef.current = taskId

    let alive = true
    setRestoring(true)

    // 恢复优先：刷新后先去后端取"已经适配好的"版本。
    // 之前是无条件重跑一遍所有平台的适配——纯规则虽不烧 LLM，但页面每次刷新都要等，
    // 而且会重写库里的行。这里只对「后端还没有」的平台补适配。
    api
      .get<any>(`/copy/${taskId}`)
      .then((r) => {
        if (!alive) return
        const bp: Record<string, CopyVersion[]> = r.by_platform || {}
        if (Object.keys(bp).length) setData((prev) => ({ ...bp, ...prev }))
        targetPlatforms
          .filter((p) => !(bp[p] || []).length)
          .forEach((p) => loadPlatform(p))
        // 默认落在第一个有内容的平台，否则恢复后可能停在"该平台暂无适配结果"
        const withData = targetPlatforms.filter((p) => (bp[p] || []).length)
        setActive(withData[0] || targetPlatforms[0] || 'weixin')
      })
      .catch((e: any) => {
        if (!alive) return
        if (e?.status === 404) {
          // 任务被清理或换了账号：清掉本地指针，别让用户对着一个永远空白的页面猜
          setTask(null)
          message.warning('上次的创作任务已不存在，已清除本地记录')
          return
        }
        // 读接口失败（网络抖动等）时退回老行为：直接逐个平台适配
        targetPlatforms.forEach((p) => loadPlatform(p))
        setActive(targetPlatforms[0] || 'weixin')
      })
      .finally(() => {
        if (alive) setRestoring(false)
      })

    return () => {
      alive = false
      // 本轮的请求结果已作废（StrictMode 卸载 / 切换任务），把标记还回去让下一次能跑
      if (adaptedTaskRef.current === taskId) adaptedTaskRef.current = null
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

      {(loading || restoring) && !versions.length ? (
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
