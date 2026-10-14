import { type ReactNode } from 'react'

interface Props {
  icon?: ReactNode
  title: string
  hint?: string
  action?: ReactNode
}

// 统一的空状态：图标 + 标题 + 说明 + 可选操作，避免裸「暂无数据」文字。
export default function EmptyState({ icon, title, hint, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6 border border-dashed border-line-strong rounded-card bg-white/60">
      {icon && <div className="text-brand/25 mb-3">{icon}</div>}
      <div className="text-[15px] font-medium text-ink">{title}</div>
      {hint && (
        <div className="text-[13px] text-muted mt-1.5 max-w-[440px] leading-relaxed">{hint}</div>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
