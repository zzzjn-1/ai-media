import { type ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: string
  extra?: ReactNode
}

// 统一的页面标题区：左侧标题+副标题，右侧操作槽（按钮/筛选等）。
export default function PageHeader({ title, subtitle, extra }: Props) {
  return (
    <div className="flex items-start justify-between gap-4 mb-5">
      <div className="min-w-0">
        <h1 className="text-[20px] font-semibold text-ink tracking-tight leading-tight">{title}</h1>
        {subtitle && <p className="text-[13px] text-muted mt-1.5">{subtitle}</p>}
      </div>
      {extra && <div className="shrink-0">{extra}</div>}
    </div>
  )
}
