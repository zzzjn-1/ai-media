import { App } from 'antd'

// 统一从 antd App 上下文取 message 实例，
// 避免静态 message.* 丢失主题与 ConfigProvider 上下文（React 18 下会报警告）。
export function useMessage() {
  const { message } = App.useApp()
  return message
}
