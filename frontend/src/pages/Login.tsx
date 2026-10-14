import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Tabs, Card } from 'antd'
import { useAuth } from '../store/AuthContext'
import { useMessage } from '../hooks/useMessage'
import { LogoMark } from '../components/icons'

const features = [
  '素材库智能打标与语义检索',
  '热点 × 素材 × 人设 联动选题',
  '多版本文案并排对比与风格优化',
  '一键适配微信 / 小红书 / 抖音',
]

export default function Login() {
  const { login, register } = useAuth()
  const navigate = useNavigate()
  const message = useMessage()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [loading, setLoading] = useState(false)

  async function onFinish(values: { username: string; password: string; email?: string }) {
    setLoading(true)
    try {
      if (mode === 'login') await login(values.username, values.password)
      else await register(values.username, values.password, values.email)
      message.success('登录成功')
      navigate('/materials', { replace: true })
    } catch (e: any) {
      message.error(e?.message || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex bg-bg">
      {/* 品牌展示区（宽屏可见） */}
      <div className="hidden lg:flex w-[44%] max-w-[520px] flex-col justify-between bg-gradient-to-br from-brand to-brand-700 text-white p-10">
        <div className="flex items-center gap-2.5">
          <LogoMark />
          <span className="text-[18px] font-semibold">AI 自媒体内容创作助手</span>
        </div>
        <div>
          <div className="text-[30px] font-semibold leading-snug">
            从热点到多平台成稿
            <br />
            一站式创作工作台
          </div>
          <div className="text-white/80 text-[14px] mt-3 leading-relaxed">
            面向自媒体创作者的内容生产流水线：素材沉淀、选题策划、文案生成与平台适配。
          </div>
          <ul className="mt-7 space-y-3">
            {features.map((f) => (
              <li key={f} className="flex items-center gap-2.5 text-[14px] text-white/90">
                <span className="w-5 h-5 rounded-full bg-white/20 flex items-center justify-center text-[12px]">
                  ✓
                </span>
                {f}
              </li>
            ))}
          </ul>
        </div>
        <div className="text-[12px] text-white/60">M4 · 内容创作工作台</div>
      </div>

      {/* 表单区 */}
      <div className="flex-1 flex items-center justify-center px-4">
        <Card className="w-[380px] shadow-soft" styles={{ body: { padding: 28 } }}>
          <div className="text-center mb-5">
            <div className="flex justify-center mb-3 lg:hidden">
              <LogoMark />
            </div>
            <div className="text-brand font-semibold text-[20px]">欢迎使用创作助手</div>
            <div className="text-muted text-[13px] mt-1">登录以进入内容工作台</div>
          </div>
          <Tabs
            activeKey={mode}
            onChange={(k) => setMode(k as 'login' | 'register')}
            items={[
              { key: 'login', label: '登录' },
              { key: 'register', label: '注册' },
            ]}
          />
          <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input placeholder="用户名" autoComplete="username" />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password placeholder="密码" autoComplete="current-password" />
            </Form.Item>
            {mode === 'register' && (
              <Form.Item name="email" label="邮箱（可选）">
                <Input placeholder="you@example.com" />
              </Form.Item>
            )}
            <Button type="primary" htmlType="submit" block loading={loading} className="mt-1">
              {mode === 'login' ? '登录' : '注册并登录'}
            </Button>
          </Form>
          <div className="text-[12px] text-muted mt-4 text-center">
            演示账号：demo_seed ／ 口令 Demo@2026（Mock 模式任意密码均可登录）
          </div>
        </Card>
      </div>
    </div>
  )
}
