import { createContext, useContext, useState, type ReactNode } from 'react'

// 跨页面流转上下文：串联「热点→选题→生成→适配→导出」核心闭环
interface FlowState {
  taskId: number | null
  topicTitle: string
  targetPlatforms: string[] // 平台码 weixin/xhs/douyin
  setTask: (id: number | null) => void
  setTopic: (t: string) => void
  setPlatforms: (p: string[]) => void
}

const Ctx = createContext<FlowState | null>(null)

export function FlowProvider({ children }: { children: ReactNode }) {
  const [taskId, setTaskId] = useState<number | null>(null)
  const [topicTitle, setTopicTitle] = useState<string>('')
  const [targetPlatforms, setTargetPlatforms] = useState<string[]>(['weixin', 'xhs', 'douyin'])

  const value: FlowState = {
    taskId,
    topicTitle,
    targetPlatforms,
    setTask: setTaskId,
    setTopic: setTopicTitle,
    setPlatforms: setTargetPlatforms,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useFlow(): FlowState {
  const c = useContext(Ctx)
  if (!c) throw new Error('FlowProvider 未挂载')
  return c
}
