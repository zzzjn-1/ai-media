import { useEffect, useState } from 'react'
import {
  Table, Input, Button, Modal, Form, Select, InputNumber,
  Drawer, Tag, Space, Popconfirm,
} from 'antd'
import { PlusOutlined, EyeOutlined, TagOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import type { Material, MaterialChunk } from '../api/types'
import { DOMAINS, PLATFORMS, platformLabel } from '../api/types'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'

// 存码（weixin/xhs/douyin），显示中文——与后端 platform_rules 的码保持一致。
// 早前这里把中文名当 value 入库，导致平台列一半是 `xhs`、一半是「小红书」。
const platformOptions = PLATFORMS.map((p) => ({ value: p.code, label: p.label }))
const domainOptions = DOMAINS.map((d) => ({ value: d, label: d }))

export default function Materials() {
  const message = useMessage()
  const [list, setList] = useState<Material[]>([])
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')

  const [importOpen, setImportOpen] = useState(false)
  const [tagOpen, setTagOpen] = useState(false)
  const [tagTarget, setTagTarget] = useState<Material | null>(null)
  const [importForm] = Form.useForm()
  const [tagForm] = Form.useForm()

  const [chunkOpen, setChunkOpen] = useState(false)
  const [chunkTitle, setChunkTitle] = useState('')
  const [chunks, setChunks] = useState<MaterialChunk[]>([])
  const [chunkLoading, setChunkLoading] = useState(false)

  async function fetchList() {
    setLoading(true)
    try {
      const r: any = await api.get(q ? `/materials?q=${encodeURIComponent(q)}&limit=100` : '/materials?limit=100')
      setList(r.results || [])
    } catch (e: any) {
      message.error(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchList()
  }, [])

  async function onSubmitImport() {
    const v = await importForm.validateFields()
    try {
      await api.post('/materials/import', {
        title: v.title,
        content: v.content,
        platforms: (v.platforms || []).join(','),
        domains: (v.domains || []).join(','),
        styles: v.styles || '',
        custom_tags: v.custom_tags || '',
      })
      message.success(`导入成功，分块 ${v.content ? '完成' : 0} 入库`)
      setImportOpen(false)
      importForm.resetFields()
      fetchList()
    } catch (e: any) {
      message.error(e?.message || '导入失败')
    }
  }

  async function onSubmitTag() {
    if (!tagTarget) return
    const v = await tagForm.validateFields()
    try {
      await api.post(`/materials/${tagTarget.id}/tag`, {
        platforms: v.platforms ? (v.platforms as string[]).join(',') : undefined,
        domains: v.domains ? (v.domains as string[]).join(',') : undefined,
        styles: v.styles || undefined,
        custom_tags: v.custom_tags || undefined,
      })
      message.success('打标成功')
      setTagOpen(false)
      fetchList()
    } catch (e: any) {
      message.error(e?.message || '打标失败')
    }
  }

  async function openChunks(m: Material) {
    setChunkTitle(m.title)
    setChunkOpen(true)
    setChunkLoading(true)
    try {
      const r: any = await api.get(`/materials/${m.id}/chunks`)
      setChunks(r.chunks || [])
    } catch (e: any) {
      message.error(e?.message || '加载分块失败')
    } finally {
      setChunkLoading(false)
    }
  }

  async function rebuildEmbed(m: Material) {
    try {
      const r: any = await api.post(`/materials/${m.id}/embed`)
      message.success(`已重建向量索引（${r.rebuilt_vectors} 向量）`)
    } catch (e: any) {
      message.error(e?.message || '重建失败')
    }
  }

  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title' },
    {
      title: '平台',
      dataIndex: 'platforms',
      key: 'platforms',
      render: (v: string) =>
        (v || '').split(',').filter(Boolean).map((t) => (
          <Tag key={t}>{platformLabel(t)}</Tag>
        )),
    },
    {
      title: '领域',
      dataIndex: 'domains',
      key: 'domains',
      render: (v: string) =>
        (v || '').split(',').filter(Boolean).map((t) => <Tag color="blue" key={t}>{t}</Tag>),
    },
    { title: '风格', dataIndex: 'styles', key: 'styles', render: (v: string) => v || '-' },
    { title: '分块数', dataIndex: 'chunk_count', key: 'chunk_count', width: 90, render: (v: number) => v ?? 0 },
    {
      title: '操作',
      key: 'op',
      width: 260,
      render: (_: any, m: Material) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openChunks(m)}>
            分块预览
          </Button>
          <Button
            size="small"
            icon={<TagOutlined />}
            onClick={() => {
              setTagTarget(m)
              tagForm.setFieldsValue({
                platforms: (m.platforms || '').split(',').filter(Boolean),
                domains: (m.domains || '').split(',').filter(Boolean),
                styles: m.styles || '',
                custom_tags: m.custom_tags || '',
              })
              setTagOpen(true)
            }}
          >
            打标
          </Button>
          <Popconfirm title="重建全量 FAISS 向量索引？" onConfirm={() => rebuildEmbed(m)}>
            <Button size="small" icon={<ReloadOutlined />}>重建向量</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="素材库管理"
        subtitle="导入素材、智能打标、语义检索与分块预览"
        extra={
          <Space>
            <Input.Search
              placeholder="搜索素材（关键词 / 语义）"
              allowClear
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onSearch={fetchList}
              style={{ width: 280 }}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
              导入素材
            </Button>
          </Space>
        }
      />

      <Table
        rowKey="id"
        loading={loading}
        dataSource={list as any}
        columns={columns as any}
        pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
      />

      {/* 导入素材 */}
      <Modal title="导入素材" open={importOpen} onCancel={() => setImportOpen(false)} onOk={onSubmitImport} okText="导入并分块" width={560}>
        <Form form={importForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="素材标题" />
          </Form.Item>
          <Form.Item name="content" label="正文" rules={[{ required: true, message: '请输入正文' }]}>
            <Input.TextArea rows={6} placeholder="粘贴素材正文，将按句边界自动分块（≤300 字/块）并加密入库" />
          </Form.Item>
          <Form.Item name="platforms" label="适用平台">
            <Select mode="tags" options={platformOptions} placeholder="可多选或自定义" />
          </Form.Item>
          <Form.Item name="domains" label="领域">
            <Select mode="tags" options={domainOptions} placeholder="可多选或自定义" />
          </Form.Item>
          <Form.Item name="styles" label="风格标签">
            <Input placeholder="逗号分隔，如：干货,复盘" />
          </Form.Item>
          <Form.Item name="custom_tags" label="自定义标签">
            <Input placeholder="逗号分隔" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 打标 */}
      <Modal title={`打标 — ${tagTarget?.title || ''}`} open={tagOpen} onCancel={() => setTagOpen(false)} onOk={onSubmitTag} okText="保存" width={560}>
        <Form form={tagForm} layout="vertical">
          <Form.Item name="platforms" label="适用平台">
            <Select mode="tags" options={platformOptions} placeholder="可多选或自定义" />
          </Form.Item>
          <Form.Item name="domains" label="领域">
            <Select mode="tags" options={domainOptions} placeholder="可多选或自定义" />
          </Form.Item>
          <Form.Item name="styles" label="风格标签">
            <Input placeholder="逗号分隔" />
          </Form.Item>
          <Form.Item name="custom_tags" label="自定义标签">
            <Input placeholder="逗号分隔" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 分块预览 */}
      <Drawer title={`分块预览 — ${chunkTitle}`} open={chunkOpen} onClose={() => setChunkOpen(false)} width={520}>
        {chunkLoading ? (
          <div className="text-muted">加载中…</div>
        ) : chunks.length === 0 ? (
          <div className="text-muted">暂无分块</div>
        ) : (
          <div className="space-y-3">
            {chunks.map((c) => (
              <div key={c.index} className="border border-line rounded-lg p-3">
                <div className="text-[12px] text-muted mb-1">
                  第 {c.index + 1} 块 · {c.char_count} 字
                </div>
                <div className="text-[14px] whitespace-pre-wrap">{c.content}</div>
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  )
}
