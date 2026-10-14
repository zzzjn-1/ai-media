import { createContext, useContext, useState, type ReactNode } from 'react'
import { api, getToken, setToken } from '../api/client'
import type { LoginResp } from '../api/types'

interface AuthState {
  token: string | null
  username: string | null
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, email?: string) => Promise<void>
  logout: () => void
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setT] = useState<string | null>(getToken())
  const [username, setU] = useState<string | null>(localStorage.getItem('username'))

  async function login(u: string, p: string) {
    const r = await api.post<LoginResp>('/auth/login', { username: u, password: p })
    if (!r.ok || !r.access_token) throw new Error(r.msg || '登录失败')
    setToken(r.access_token)
    setT(r.access_token)
    setU(u)
    localStorage.setItem('username', u)
  }

  async function register(u: string, p: string, email?: string) {
    const r = await api.post<LoginResp>('/auth/register', { username: u, password: p, email })
    if (!r.ok || !r.access_token) throw new Error(r.msg || '注册失败')
    setToken(r.access_token)
    setT(r.access_token)
    setU(u)
    localStorage.setItem('username', u)
  }

  function logout() {
    setToken()
    setT(null)
    setU(null)
    localStorage.removeItem('username')
  }

  return (
    <Ctx.Provider value={{ token, username, login, register, logout }}>{children}</Ctx.Provider>
  )
}

export function useAuth(): AuthState {
  const c = useContext(Ctx)
  if (!c) throw new Error('AuthProvider 未挂载')
  return c
}
