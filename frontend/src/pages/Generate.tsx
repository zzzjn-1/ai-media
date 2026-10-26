import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Select, Skeleton, Spin } from 'antd'
import { api, streamGenerate } from '../api/client'
import type { CopyVersion, SSEEvent } from '../api/types'
import { PLATFORMS, platformLabel } from '../api/types'
import { useFlow } from '../store/FlowContext'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'

const inputCls =
  'w-full border border-line rounded-lg px-3 py-2 text-[13px] bg-white transition-colors focus:border-brand focus:ring-2 focus:ring-brand/20 focus:outline-none'

const filterBtn = (active: boolean) =>
  `px-3 py-1.5 rounded-full text-[13px] border transition-colors duration-200 ${
    active
      ? 'bg-brand text-white border-brand shadow-sm'
      : 'bg-white text-ink/70 border-line hover:border-brand/40 hover:text-brand'
  }`

export default function Generate() {
  const navigate = useNavigate()
  // taskId 只从 FlowContext 取：它才是被持久化的那个指针。
  // ⚠️ 这里曾经另有一个同名局部 state（useState(null)），恢复 effect 读的是局部值（永远是
  // null），于是刷新后恢复一次都没真正发起过，页面看起来像「什么都没恢复」（2026-10-26 实测）。
  const { taskId, topicTitle, topicId, targetPlatforms, setTask, setTopic, setPlatforms } = useFlow()
  const message = useMessage()

  const [topic, setTopicTitle] = useState(topicTitle)
  // 选题来源：从选题列表带进来的 topic.id。用户一旦手改标题就解除关联，
  // 免得把这次文案挂到一条不相干的选题上（copy_tasks.topic_id 是外键）。
  const [linkTopicId, setLinkTopicId] = useState<number | null>(topicId)
  const [plats, setPlats] = useState<string[]>(targetPlatforms.length ? targetPlatforms : ['weixin'])
  const [audience, setAudience] = useState('关注效率的人群')
  const [intent, setIntent] = useState('干货科普')
  const [tone, setTone] = useState('专业')
  const [length, setLength] = useState('中')
  const [personaVoice, setPersonaVoice] = useState('')
  const [nVersions, setNVersions] = useState(3)

  const [versions, setVersions] = useState<CopyVersion[]>([])
  const [edits, setEdits] = useState<Record<number, string>>({})
  const [snippets, setSnippets] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)
  const [genSeconds, setGenSeconds] = useState<number | null>(null)
  const [optimizing, setOptimizing] = useState(false)
  const [error, setError] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [restoredAt, setRestoredAt] = useState('')

  // 刷新后恢复：FlowContext 已把 taskId 持久化，这里据此把上次的版本从后端读回来。
  //
  // ⚠️ 这里踩过一个坑：最初把「已恢复」标记在 effect 开头就置位，配合 alive 守卫，在
  // StrictMode（dev 下 mount→unmount→mount）里变成——第一次请求发出后被 cleanup 判为
  // alive=false、结果丢弃；第二次 mount 看到标记又直接 return。两边都不干活，页面永远空白。
  // 现在改成「成功/失败之后才置位」，第二次 mount 会正当地再请求一次（GET 幂等，很便宜）。
  const restoredRef = useRef(false)

  useEffect(() => {
    if (restoredRef.current) return
    if (!taskId) {
      restoredRef.current = true
      return
    }
    let alive = true
    setRestoring(true)
    api
      .get<any>(`/copy/${taskId}`)
      .then((r) => {
        if (!alive) return
        restoredRef.current = true
        const vs: CopyVersion[] = r.versions || []
        if (vs.length) setVersions(vs)
        if (r.task?.target_platforms?.length) {
          setPlats(r.task.target_platforms)
          setPlatforms(r.task.target_platforms)
        }
        // 输入框为空时才回填（用户可能已经在框里改过标题了）
        if (r.task?.topic_title && !topic.trim()) {
          setTopicTitle(r.task.topic_title)
          setTopic(r.task.topic_title, r.task.topic_id)
          setLinkTopicId(r.task.topic_id ?? null)
        }
        setRestoredAt(r.task?.created_at || '')
        if (vs.length && r.task?.status === 'running') {
          message.warning('上次生成似乎被中断了，只恢复了已产出的版本')
        }
      })
      .catch((e: any) => {
        if (!alive) return
        restoredRef.current = true
        // 只有"任务确实没了"才清指针；网络抖动不该把用户的上次进度抹掉
        if (e?.status === 404) {
          setTask(null)
          message.warning('上次的创作任务已不存在，已清除本地记录')
        }
      })
      .finally(() => {
        if (alive) setRestoring(false)
      })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function clearRestored() {
    setVersions([])
    setEdits({})
    setSnippets([])
    setGenSeconds(null)
    setRestoredAt('')
    setTask(null)
  }

  async function onGenerate() {
    if (!topic.trim()) {
      message.warning('请先填写选题标题（可在「选题列表」一键选用带入）')
      return
    }
    setGenerating(true)
    setError('')
    setVersions([])
    setEdits({})
    setSnippets([])
    setGenSeconds(null)
    setTask(null)

    const req = {
      topic_id: linkTopicId ?? undefined,
      topic_title: topic,
      target_platforms: plats,
      audience,
      intent,
      tone,
      length,
      persona_voice: personaVoice || undefined,
      n_versions: nVersions,
    }

    const onEvent = (e: SSEEvent) => {
      if (e.type === 'start') {
        setTask(e.task_id ?? null)
      } else if (e.type === 'retrieved') {
        setSnippets(e.snippets || [])
      } else if (e.type === 'version') {
        setVersions((prev) => [
          ...prev,
          {
            version_no: e.version_no ?? prev.length + 1,
            title: e.title || '',
            body: e.optimized_body || '',
            tags: e.tags || [],
            adapted: e.adapted || {},
            style_consistency: e.style_consistency,
            replacement_suggestions: e.replacement_suggestions,
          },
        ])
      } else if (e.type === 'done') {
        setGenerating(false)
        setGenSeconds(e.generation_seconds ?? null)
        setPlatforms(e.target_platforms || plats)
        setTask(e.task_id ?? null)
      } else if (e.type === 'error') {
        setGenerating(false)
        setError(e.msg || '生成失败')
      }
    }

    try {
      await streamGenerate(req, onEvent)
    } catch (e: any) {
      setGenerating(false)
      setError(e?.message || '生成失败')
    }
  }

  async function onOptimize() {
    if (!taskId) return
    setOptimizing(true)
    try {
      const r: any = await api.post(`/copy/${taskId}/optimize`, {
        tone: tone || undefined,
        length: length || undefined,
        persona_voice: personaVoice || undefined,
      })
      const updated: CopyVersion[] = r.versions || []
      setVersions((prev) =>
        prev.map((v) => {
          const u = updated.find((x: any) => x.version_no === v.version_no)
          return u
            ? {
                ...v,
                body: u.body,
                style_consistency: u.style_consistency,
                replacement_suggestions: u.replacement_suggestions,
              }
            : v
        }),
      )
      setEdits({})
      message.success('风格优化完成')
    } catch (e: any) {
      message.error(e?.message || '优化失败')
    } finally {
      setOptimizing(false)
    }
  }

  function bodyText(v: CopyVersion): string {
    return edits[v.version_no] !== undefined ? edits[v.version_no] : v.body
  }

  // 替换建议可能是字符串 / 数组 / {原词: 建议词} 三种形状（Mock 与真实后端不同），
  // 直接 JSON.stringify 会把引号和花括号显示给用户（实测截图里就是 ["「首发阶段」…"]）。
  function suggestionText(s: any): string {
    if (!s) return ''
    if (typeof s === 'string') return s
    if (Array.isArray(s)) return s.map((x) => String(x)).join('；')
    if (typeof s === 'object') {
      return Object.entries(s).map(([k, v]) => `${k} → ${v}`).join('；')
    }
    return String(s)
  }

  const toneOpts = ['专业', '轻松', '犀利', '温暖', '幽默']
  const lenOpts = ['短', '中', '长']

  return (
    <div>
      <PageHeader
        title="文案生成与对比编辑"
        subtitle="多版本并排对比 · 逐段编辑 · 风格参数面板"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 参数面板 */}
        <div className="bg-white border border-line rounded-card shadow-card p-4 h-fit">
          <h3 className="font-semibold text-ink mb-3 text-[15px]">创作参数</h3>

          <label className="block text-[12px] text-muted mb-1.5">选题标题</label>
          <input
            value={topic}
            onChange={(e) => {
              setTopicTitle(e.target.value)
              setTopic(e.target.value) // 不带 id：手改标题即与选题解除关联
              setLinkTopicId(null)
            }}
            className={`${inputCls} mb-3`}
            placeholder="如：AI 写作会不会取代自媒体人？"
          />

          <label className="block text-[12px] text-muted mb-1.5">目标平台（多选）</label>
          <div className="flex flex-wrap gap-2 mb-3">
            {PLATFORMS.map((p) => (
              <button
                key={p.code}
                onClick={() =>
                  setPlats((prev) =>
                    prev.includes(p.code) ? prev.filter((x) => x !== p.code) : [...prev, p.code],
                  )
                }
                className={filterBtn(plats.includes(p.code))}
              >
                {p.label}
              </button>
            ))}
          </div>

          <label className="block text-[12px] text-muted mb-1.5">语气</label>
          <Select
            value={tone}
            onChange={(v) => setTone(v)}
            options={toneOpts.map((t) => ({ value: t, label: t }))}
            className="w-full mb-3"
          />

          <label className="block text-[12px] text-muted mb-1.5">篇幅</label>
          <Select
            value={length}
            onChange={(v) => setLength(v)}
            options={lenOpts.map((t) => ({ value: t, label: t }))}
            className="w-full mb-3"
          />

          <label className="block text-[12px] text-muted mb-1.5">目标受众</label>
          <input
            value={audience}
            onChange={(e) => setAudience(e.target.value)}
            className={`${inputCls} mb-3`}
          />

          <label className="block text-[12px] text-muted mb-1.5">创作意图</label>
          <input
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            className={`${inputCls} mb-3`}
          />

          <label className="block text-[12px] text-muted mb-1.5">人设口吻（可选）</label>
          <input
            value={personaVoice}
            onChange={(e) => setPersonaVoice(e.target.value)}
            placeholder="如：理性克制的技术观察者"
            className={`${inputCls} mb-3`}
          />

          <label className="block text-[12px] text-muted mb-1.5">生成版本数</label>
          <Select
            value={nVersions}
            onChange={(v) => setNVersions(v)}
            options={[3, 4, 5].map((n) => ({ value: n, label: `${n} 个版本` }))}
            className="w-full mb-4"
          />

          <button
            onClick={onGenerate}
            disabled={generating}
            className="w-full bg-brand text-white rounded-lg py-2.5 text-[14px] font-medium disabled:opacity-50 hover:bg-brand-700 transition-colors duration-200"
          >
            {generating ? '生成中…（SSE 流式）' : `一键生成 ${nVersions} 版本`}
          </button>
          <button
            onClick={onOptimize}
            disabled={optimizing || !taskId}
            className="w-full mt-2 border border-brand text-brand rounded-lg py-2.5 text-[14px] font-medium disabled:opacity-40 hover:bg-brand-soft transition-colors duration-200"
          >
            {optimizing ? '优化中…' : '风格优化'}
          </button>
          <button
            onClick={() => navigate('/adapt')}
            disabled={!taskId}
            className="w-full mt-2 border border-line text-ink/70 rounded-lg py-2.5 text-[14px] font-medium disabled:opacity-40 hover:border-brand/40 hover:text-brand transition-colors duration-200"
          >
            进入平台适配 →
          </button>
        </div>

        {/* 多版本对比 */}
        <div className="lg:col-span-2">
          {error && (
            <div className="bg-red-50 border border-red-200 text-danger rounded-lg p-3 mb-3 text-[13px]">
              生成出错：{error}
            </div>
          )}

          {snippets.length > 0 && (
            <div className="bg-brand-soft text-brand rounded-lg p-3 mb-3 text-[13px]">
              已检索素材 {snippets.length} 条 · 示例：{snippets.join(' / ')}
            </div>
          )}

          {restoredAt && versions.length > 0 && (
            <div className="bg-brand-soft text-brand rounded-lg p-3 mb-3 text-[13px] flex flex-wrap items-center justify-between gap-2">
              <span>
                已恢复上次生成结果 · 任务 ID {taskId}
                {restoredAt ? ` · ${restoredAt}` : ''}
              </span>
              <button
                onClick={clearRestored}
                className="text-[12px] underline underline-offset-2 hover:opacity-70"
              >
                清空重来
              </button>
            </div>
          )}

          {restoring && versions.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 border border-dashed border-line-strong rounded-card bg-white/60">
              <Spin tip="正在恢复上次生成…" size="large">
                <div className="w-24 h-24" />
              </Spin>
            </div>
          )}

          {versions.length === 0 && !generating && !restoring && (
            <div className="text-muted text-center py-20 border border-dashed border-line-strong rounded-card bg-white/60">
              点击「一键生成」查看多版本结果（SSE 流式逐条产出）
            </div>
          )}

          {generating && versions.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 border border-dashed border-line-strong rounded-card bg-white/60">
              <Spin tip="正在生成…" size="large">
                <div className="w-32 h-32" />
              </Spin>
              <div className="text-[13px] text-muted mt-3">正在流式生成多版本文案，请稍候</div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5">
            {versions.map((v) => (
              <div
                key={v.version_no}
                className="bg-white border border-line rounded-card p-4 shadow-card hover:shadow-soft transition duration-200"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold text-brand">版本 {v.version_no}</span>
                  <span className="text-[12px] text-ok">一致性：{v.style_consistency || '—'}</span>
                </div>
                <div className="font-medium text-[14px] text-ink mb-2">{v.title}</div>

                <textarea
                  value={bodyText(v)}
                  onChange={(e) => setEdits((prev) => ({ ...prev, [v.version_no]: e.target.value }))}
                  rows={8}
                  className="w-full border border-line rounded-lg p-2 text-[13px] leading-relaxed bg-white transition-colors focus:border-brand focus:ring-2 focus:ring-brand/20 focus:outline-none"
                />

                <div className="flex flex-wrap gap-1.5 mt-2">
                  {(v.tags || []).map((t, i) => (
                    <span key={i} className="text-[12px] bg-bg text-muted px-2 py-0.5 rounded">
                      #{t}
                    </span>
                  ))}
                </div>

                {v.adapted && Object.keys(v.adapted).length > 0 && (
                  <div className="mt-2 text-[12px] text-muted leading-relaxed">
                    各平台标题：
                    {Object.entries(v.adapted).map(([p, t]) => (
                      <span key={p} className="ml-1">
                        {platformLabel(p)}「{t as string}」
                      </span>
                    ))}
                  </div>
                )}

                {suggestionText(v.replacement_suggestions) && (
                  <div className="mt-2 text-[12px] text-warn">
                    替换建议：{suggestionText(v.replacement_suggestions)}
                  </div>
                )}
              </div>
            ))}
          </div>

          {genSeconds !== null && (
            <div className="text-[13px] text-muted mt-3">
              生成完成 · 耗时 {genSeconds}s · 任务 ID {taskId}（可进入「平台适配预览」导出）
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
