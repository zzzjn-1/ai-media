import { useEffect, lazy, Suspense } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from './store/AuthContext'
import AppLayout from './layout/AppLayout'
import Login from './pages/Login'

// T5.2 按路由拆包：六大页面各自独立 chunk，首屏只加载登录页 + 框架，
// 进入对应路由时才拉取页面（含其 AntD 组件），主包体积显著下降。
const Materials = lazy(() => import('./pages/Materials'))
const Hotspots = lazy(() => import('./pages/Hotspots'))
const Topics = lazy(() => import('./pages/Topics'))
const Generate = lazy(() => import('./pages/Generate'))
const Adapt = lazy(() => import('./pages/Adapt'))
const Persona = lazy(() => import('./pages/Persona'))

function RouteFallback() {
  return (
    <div style={{ padding: 48, textAlign: 'center', color: '#999' }}>
      页面加载中…
    </div>
  )
}

export default function App() {
  const { token, logout } = useAuth()
  const navigate = useNavigate()

  // 后端返回 401 → 退出并回登录页
  useEffect(() => {
    const onUnauth = () => {
      logout()
      navigate('/login', { replace: true })
    }
    window.addEventListener('auth:unauthorized', onUnauth)
    return () => window.removeEventListener('auth:unauthorized', onUnauth)
  }, [logout, navigate])

  if (!token) return <Login />

  return (
    <AppLayout>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Navigate to="/materials" replace />} />
          <Route path="/materials" element={<Materials />} />
          <Route path="/hotspots" element={<Hotspots />} />
          <Route path="/topics" element={<Topics />} />
          <Route path="/generate" element={<Generate />} />
          <Route path="/adapt" element={<Adapt />} />
          <Route path="/persona" element={<Persona />} />
          <Route path="*" element={<Navigate to="/materials" replace />} />
        </Routes>
      </Suspense>
    </AppLayout>
  )
}
