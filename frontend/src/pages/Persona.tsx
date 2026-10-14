import { useEffect, useState } from 'react'
import { Form, Input, Button, Card, Select } from 'antd'
import { api } from '../api/client'
import { DOMAINS } from '../api/types'
import { useMessage } from '../hooks/useMessage'
import PageHeader from '../components/PageHeader'

export default function Persona() {
  const message = useMessage()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    api
      .get('/persona')
      .then((r: any) => {
        form.setFieldsValue({
          display_name: r.persona?.display_name || '',
          positioning: r.persona?.positioning || '',
          tone: r.persona?.tone || '',
          domains: (r.persona?.domains || '').split(',').filter(Boolean),
        })
      })
      .catch((e) => message.error(e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [form])

  async function onSave() {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await api.post('/persona', {
        display_name: v.display_name || undefined,
        positioning: v.positioning || undefined,
        tone: v.tone || undefined,
        domains: (v.domains || []).join(','),
      })
      message.success('人设已保存')
    } catch (e: any) {
      message.error(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-[640px]">
      <PageHeader title="账号人设" subtitle="作为选题与文案生成的默认上下文（F6），可在生成 / 选题时覆盖" />
      <Card>
        <Form form={form} layout="vertical" disabled={loading}>
          <Form.Item name="display_name" label="展示名称">
            <Input placeholder="如：AI 效率观察" />
          </Form.Item>
          <Form.Item name="positioning" label="账号定位">
            <Input.TextArea rows={2} placeholder="一句话说明账号价值主张" />
          </Form.Item>
          <Form.Item name="tone" label="默认口吻">
            <Input placeholder="如：专业、轻松、犀利" />
          </Form.Item>
          <Form.Item name="domains" label="深耕领域">
            <Select
              mode="tags"
              options={DOMAINS.map((d) => ({ value: d, label: d }))}
              placeholder="可多选或自定义"
            />
          </Form.Item>
          <Button type="primary" loading={saving} onClick={onSave}>
            保存人设
          </Button>
        </Form>
      </Card>
      <div className="text-[12px] text-muted mt-3">
        人设将作为选题与文案生成的默认上下文（F6），可在生成/选题时覆盖。
      </div>
    </div>
  )
}
