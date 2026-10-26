import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useAuth } from './AuthContext'

// 跨页面流转上下文：串联「热点→选题→生成→适配→导出」核心闭环
interface FlowState {
  taskId: number | null
  topicTitle: string
  /** 选题来源 id：带上它，copy_tasks.topic_id 才能真正落地（否则这张外键永远是空） */
  topicId: number | null
  targetPlatforms: string[] // 平台码 weixin/xhs/douyin
  setTask: (id: number | null) => void
  setTopic: (t: string, id?: number | null) => void
  setPlatforms: (p: string[]) => void
}

const Ctx = createContext<FlowState | null>(null)

const STORAGE_KEY = 'flow:v1'
const DEFAULT_PLATFORMS = ['weixin', 'xhs', 'douyin']

interface Persisted {
  taskId: number | null
  topicTitle: string
  topicId: number | null
  targetPlatforms: string[]
  /** 归属账号：换账号登录时必须丢弃，否则 A 的任务会被 B 拿去打开（后端会 404，但用户会莫名看到"上次任务已失效"） */
  owner: string
}

function empty(owner: string): Persisted {
  return {
    taskId: null,
    topicTitle: '',
    topicId: null,
    targetPlatforms: DEFAULT_PLATFORMS,
    owner,
  }
}

/** 只认「同一个账号 + 形状合法」的存档；坏 JSON / 换了账号一律当没有 */
function restore(owner: string): Persisted {
  if (!owner) return empty('')
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return empty(owner)
    const p = JSON.parse(raw) as Partial<Persisted>
    if (!p || p.owner !== owner) return empty(owner)
    const plats = Array.isArray(p.targetPlatforms)
      ? p.targetPlatforms.filter((x): x is string => typeof x === 'string')
      : []
    return {
      taskId: typeof p.taskId === 'number' ? p.taskId : null,
      topicTitle: typeof p.topicTitle === 'string' ? p.topicTitle : '',
      topicId: typeof p.topicId === 'number' ? p.topicId : null,
      targetPlatforms: plats.length ? plats : DEFAULT_PLATFORMS,
      owner,
    }
  } catch {
    return empty(owner)
  }
}

export function FlowProvider({ children }: { children: ReactNode }) {
  // 存档按账号隔离：FlowProvider 在 AuthProvider 之内，可以直接拿到当前用户名
  const { username } = useAuth()
  const owner = username || ''

  // 惰性初始化：首帧就是恢复后的状态，避免"先渲染空态再闪一下"的观感。
  // 只解析一次存档（boot），四个字段从同一份快照取，避免 4 次 JSON.parse 还可能出现字段不一致。
  const [boot] = useState(() => restore(owner))
  const [taskId, setTaskId] = useState<number | null>(boot.taskId)
  const [topicTitle, setTopicTitle] = useState<string>(boot.topicTitle)
  const [topicId, setTopicId] = useState<number | null>(boot.topicId)
  const [targetPlatforms, setTargetPlatforms] = useState<string[]>(boot.targetPlatforms)

  // 切账号（含登出后登入另一个人）时整块重置，不允许旧账号的指针残留
  const ownerRef = useRef(owner)
  useEffect(() => {
    if (ownerRef.current === owner) return
    ownerRef.current = owner
    const s = restore(owner)
    setTaskId(s.taskId)
    setTopicTitle(s.topicTitle)
    setTopicId(s.topicId)
    setTargetPlatforms(s.targetPlatforms)
  }, [owner])

  // 写回：未登录不落盘（登出即清），登录后按 owner 存
  useEffect(() => {
    if (!owner) {
      localStorage.removeItem(STORAGE_KEY)
      return
    }
    const payload: Persisted = { taskId, topicTitle, topicId, targetPlatforms, owner }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      /* 隐私模式下 localStorage 可能抛错，流转退化成"仅本次会话可用"，不该白屏 */
    }
  }, [owner, taskId, topicTitle, topicId, targetPlatforms])

  // 不带 id 调用 = 用户手输了标题，与任何选题解除关联（否则会把文案挂到错的选题上）
  function setTopic(t: string, id?: number | null) {
    setTopicTitle(t)
    setTopicId(id ?? null)
  }

  const value: FlowState = {
    taskId,
    topicTitle,
    topicId,
    targetPlatforms,
    setTask: setTaskId,
    setTopic,
    setPlatforms: setTargetPlatforms,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useFlow(): FlowState {
  const c = useContext(Ctx)
  if (!c) throw new Error('FlowProvider 未挂载')
  return c
}
