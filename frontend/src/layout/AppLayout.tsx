import { type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  DatabaseOutlined,
  FireOutlined,
  BulbOutlined,
  EditOutlined,
  ShareAltOutlined,
  UserOutlined,
  LogoutOutlined,
} from '@ant-design/icons'
import { useAuth } from '../store/AuthContext'
import { LogoMark } from '../components/icons'
import { USE_MOCK } from '../api/mock'

const NAV = [
  { to: '/materials', label: '素材库管理', icon: <DatabaseOutlined /> },
  { to: '/hotspots', label: '热点看板', icon: <FireOutlined /> },
  { to: '/topics', label: '选题列表', icon: <BulbOutlined /> },
  { to: '/generate', label: '文案生成对比', icon: <EditOutlined /> },
  { to: '/adapt', label: '平台适配预览', icon: <ShareAltOutlined /> },
  { to: '/persona', label: '账号人设', icon: <UserOutlined /> },
]

export default function AppLayout({ children }: { children: ReactNode }) {
  const { username, logout } = useAuth()
  const navigate = useNavigate()
  const initial = (username || '?').slice(0, 1).toUpperCase()

  return (
    <div className={`flex min-h-screen bg-bg${USE_MOCK ? ' mt-[28px]' : ''}`}>
      {USE_MOCK && (
        <div className="fixed top-0 left-0 right-0 z-50 h-[28px] bg-amber-100 text-amber-800 text-[12px] flex items-center justify-center gap-2 border-b border-amber-200">
          <span className="font-medium">演示模式</span>
          <span>当前为前端 Mock 数据（无需后端），接入真实服务请设置 VITE_USE_MOCK=false 并启动后端</span>
        </div>
      )}
      {/* 侧边栏 */}
      <aside className="w-[232px] shrink-0 bg-white border-r border-line-strong flex flex-col shadow-[2px_0_16px_rgba(16,24,40,0.04)]">
        {/* 品牌区 */}
        <div className="flex items-center gap-2.5 px-4 h-[60px] border-b border-line">
          <LogoMark />
          <div className="leading-tight">
            <div className="font-semibold text-ink text-[15px]">创作助手</div>
            <div className="text-[11px] text-muted">AI 内容工作台</div>
          </div>
        </div>

        {/* 导航 */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-[14px] font-medium transition-colors duration-200 ${
                  isActive
                    ? 'bg-brand text-white shadow-sm'
                    : 'text-ink/80 hover:bg-brand-soft hover:text-brand'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-1 rounded-r bg-white/80" />
                  )}
                  <span
                    className={`text-[17px] ${isActive ? '' : 'text-brand/70 group-hover:text-brand'}`}
                  >
                    {n.icon}
                  </span>
                  {n.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* 用户区 */}
        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2.5 px-2 py-1.5 mb-2">
            <div className="w-8 h-8 rounded-full bg-brand-soft text-brand font-semibold flex items-center justify-center text-[13px]">
              {initial}
            </div>
            <div className="min-w-0">
              <div className="text-[13px] text-ink font-medium truncate">{username}</div>
              <div className="text-[11px] text-muted">已登录</div>
            </div>
          </div>
          <button
            onClick={() => {
              logout()
              navigate('/login', { replace: true })
            }}
            className="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-lg text-[13px] text-danger/80 hover:bg-red-50 hover:text-danger transition-colors duration-200"
          >
            <LogoutOutlined /> 退出登录
          </button>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  )
}
