import {
  ArrowRightOutlined,
  EyeOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  TagsOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type {
  ContentItem,
  ContentListResponse,
  ContentStatus,
  ContentStatusResponse,
  ContentGenerateResponse,
  ContentSummaryResponse,
} from '../api/types'
import {
  CONTENT_CHANNEL_OPTIONS,
  CONTENT_STATUS_COLORS,
  CONTENT_TYPE_OPTIONS,
} from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

/** 浏览量：★ 只显示后端 EvidenceValue；未统计 → 「未统计」而不是 0 */
function Views({ evidence }: { evidence: ContentItem['view_count'] }) {
  if (evidence.state !== 'VALID' || evidence.value === null) {
    return (
      <Tooltip title={evidence.reason ?? '未统计'}>
        <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
          未统计
        </Text>
      </Tooltip>
    )
  }
  return <Text strong>{Number(evidence.value).toLocaleString()}</Text>
}

export default function ContentsPage() {
  const [summary, setSummary] = useState<ContentSummaryResponse | null>(null)
  // ★ TASK-038 AI 生成正文：生成结果只回填表单，确认后才入库
  const [generating, setGenerating] = useState(false)
  const [genResult, setGenResult] = useState<ContentGenerateResponse | null>(null)
  const [genIsAi, setGenIsAi] = useState(false)
  const [list, setList] = useState<ContentListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [statusFilter, setStatusFilter] = useState('ALL')

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const [statusTarget, setStatusTarget] = useState<ContentItem | null>(null)
  const [statusValue, setStatusValue] = useState<ContentStatus | null>(null)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (statusFilter !== 'ALL') params.set('status', statusFilter)
      const [s, l] = await Promise.all([
        apiGet<ContentSummaryResponse>('/api/contents/summary'),
        apiGet<ContentListResponse>(`/api/contents?${params.toString()}`),
      ])
      setSummary(s)
      setList(l)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await apiPost<ContentItem>('/api/contents', {
        title: v.title,
        content_type: v.content_type,
        status: v.status ?? 'DRAFT',
        channel: v.channel,
        published_on: v.published_on ? v.published_on.format('YYYY-MM-DD') : null,
        view_count: v.view_count ?? null,
        author: v.author ?? null,
        url: v.url ?? null,
        summary: v.summary ?? null,
        // ★ TASK-038：AI 生成的正文一起提交；带上 ai 标记让界面能提示"未复核"
        body: v.body ?? null,
        body_ai_generated: v.body ? genIsAi : false,
      })
      message.success('内容已创建')
      setCreateOpen(false)
      form.resetFields()
      setGenResult(null)
      setGenIsAi(false)
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  /**
   * 调 AI 生成正文（TASK-038）。
   *
   * ★ 生成结果**只填进表单、不直接入库** —— 先让人看过再决定是否保存，
   *   避免"AI 写的东西悄悄变成正式内容"。
   */
  const runGenerate = async () => {
    const topic = (form.getFieldValue('gen_topic') as string | undefined)?.trim()
      || (form.getFieldValue('title') as string | undefined)?.trim()
    if (!topic) {
      message.warning('先填「写作主题」或「标题」，AI 需要知道写什么')
      return
    }
    setGenerating(true)
    try {
      const res = await apiPost<ContentGenerateResponse>('/api/contents/generate', {
        topic,
        content_type: form.getFieldValue('content_type') ?? 'ARTICLE',
        channel: form.getFieldValue('channel') ?? 'WEBSITE',
        target_length: form.getFieldValue('target_length') ?? 'MEDIUM',
        use_knowledge: form.getFieldValue('use_knowledge') ?? true,
      })
      // 回填：标题（若空）、摘要、正文
      if (!form.getFieldValue('title')) form.setFieldValue('title', res.title)
      form.setFieldValue('summary', res.summary)
      form.setFieldValue('body', res.body)
      setGenResult(res)
      setGenIsAi(true)
      message.success(`已生成约 ${res.char_count} 字，请核对后再创建`)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setGenerating(false)
    }
  }

  const submitStatus = async () => {
    if (!statusTarget || !statusValue) return
    setSaving(true)
    try {
      const res = await apiPost<ContentStatusResponse>(`/api/contents/${statusTarget.id}/status`, {
        status: statusValue,
      })
      message[res.changed ? 'success' : 'info'](res.message)
      setStatusTarget(null)
      setStatusValue(null)
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '状态流转失败')
    } finally {
      setSaving(false)
    }
  }

  const cards = [
    { title: '内容总数', icon: <FileTextOutlined />, color: '#2563EB', evidence: summary?.total },
    {
      title: '已发布',
      icon: <SendOutlined />,
      color: '#16A34A',
      evidence: summary?.published_total,
    },
    { title: '草稿', icon: <FileTextOutlined />, color: '#94A3B8', evidence: summary?.draft_total },
    {
      title: '浏览量合计',
      icon: <EyeOutlined />,
      color: '#7C3AED',
      evidence: summary?.total_views,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            营销与内容
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            内容与客户没有从属关系；浏览量是匿名聚合计数，不能用来推算「有多少客户看过」
          </Text>
        </Col>
        <Col>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建内容
          </Button>
        </Col>
      </Row>

      <Row gutter={16}>
        {cards.map((c) => (
          <Col key={c.title} xs={24} sm={12} xl={6}>
            <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }}>
              <Space size={10} style={{ marginBottom: 8 }}>
                <span
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: '50%',
                    background: `${c.color}14`,
                    color: c.color,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {c.icon}
                </span>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  {c.title}
                </Text>
              </Space>
              <div style={{ fontSize: 24, fontWeight: 600 }}>
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 分布 + Top 内容 */}
      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card
            variant="outlined"
            title={
              <Space size={8}>
                <TagsOutlined />
                <span>类型与渠道分布</span>
              </Space>
            }
            style={{ height: '100%' }}
            styles={{ body: { padding: 16 } }}
          >
            {(summary?.by_type.length ?? 0) === 0 && (summary?.by_channel.length ?? 0) === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <Space orientation="vertical" size={14} style={{ width: '100%' }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    按类型
                  </Text>
                  <div style={{ marginTop: 6 }}>
                    <Space size={8} wrap>
                      {(summary?.by_type ?? []).map((t) => (
                        <Tag key={t.key}>
                          {t.label} {t.count}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    按渠道
                  </Text>
                  <div style={{ marginTop: 6 }}>
                    <Space size={8} wrap>
                      {(summary?.by_channel ?? []).map((t) => (
                        <Tag key={t.key} color="blue">
                          {t.label} {t.count}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </div>
              </Space>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            variant="outlined"
            title="浏览量 Top 5"
            extra={
              <Tooltip title="浏览量是匿名聚合计数；未统计的内容不参与排名">
                <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                  仅统计已填浏览量的内容
                </Text>
              </Tooltip>
            }
            style={{ height: '100%' }}
            styles={{ body: { padding: (summary?.top_contents.length ?? 0) ? 8 : 24 } }}
          >
            {(summary?.top_contents.length ?? 0) === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">还没有内容统计过浏览量</Text>}
              />
            ) : (
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                {(summary?.top_contents ?? []).map((c, i) => (
                  <Row key={c.id} justify="space-between" align="middle" style={{ padding: '4px 8px' }}>
                    <Space size={8}>
                      <Text type="secondary" style={{ fontSize: 12, width: 16 }}>
                        {i + 1}
                      </Text>
                      <Text style={{ fontSize: 13 }} ellipsis>
                        {c.title}
                      </Text>
                    </Space>
                    <Views evidence={c.view_count} />
                  </Row>
                ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      {/* 内容列表 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>内容清单</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 条` : '暂无'}
            </Text>
          </Space>
        }
        extra={
          <Space>
            <Segmented
              size="small"
              options={[
                { label: '全部', value: 'ALL' },
                { label: '草稿', value: 'DRAFT' },
                { label: '待审核', value: 'REVIEW' },
                { label: '已发布', value: 'PUBLISHED' },
                { label: '已归档', value: 'ARCHIVED' },
              ]}
              value={statusFilter}
              onChange={(v) => setStatusFilter(String(v))}
            />
            <Button icon={<ReloadOutlined />} size="small" onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
          </Space>
        }
        styles={{ body: { padding: (list?.items.length ?? 0) ? 0 : 24 } }}
      >
        {(list?.items.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '当前筛选下没有内容'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  内容流转：草稿 → 待审核 → 已发布；审核不通过可退回草稿
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<ContentItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            columns={[
              { title: '标题', dataIndex: 'title', ellipsis: true },
              {
                title: '类型',
                dataIndex: 'type_label',
                width: 100,
                render: (v: string) => <Tag>{v}</Tag>,
              },
              {
                title: '渠道',
                dataIndex: 'channel_label',
                width: 100,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: '状态',
                dataIndex: 'status_label',
                width: 100,
                render: (v: string, r) => <Tag color={CONTENT_STATUS_COLORS[r.status]}>{v}</Tag>,
              },
              {
                title: '发布日期',
                dataIndex: 'published_on',
                width: 120,
                render: (v: string | null) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v ?? '—'}
                  </Text>
                ),
              },
              {
                title: '浏览量',
                width: 100,
                render: (_: unknown, r) => <Views evidence={r.view_count} />,
              },
              { title: '作者', dataIndex: 'author', width: 90, render: (v: string | null) => v ?? '—' },
              {
                title: '操作',
                key: 'action',
                width: 96,
                render: (_: unknown, r) => (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => {
                      setStatusTarget(r)
                      setStatusValue(null)
                    }}
                  >
                    流转 <ArrowRightOutlined style={{ fontSize: 10 }} />
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 新建内容 */}
      <Modal
        title="新建内容"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        width={620}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '填个标题' }]}>
            <Input placeholder="如：AI 商业助理如何降低获客成本" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="content_type" label="类型" initialValue="ARTICLE">
                <Select options={CONTENT_TYPE_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="channel" label="渠道" initialValue="WEBSITE">
                <Select options={CONTENT_CHANNEL_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="status" label="状态" initialValue="DRAFT">
                <Select
                  options={[
                    { value: 'DRAFT', label: '草稿' },
                    { value: 'REVIEW', label: '待审核' },
                    { value: 'PUBLISHED', label: '已发布' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="view_count"
                label="浏览量（不填=未统计，与 0 不同）"
                extra="浏览量是匿名聚合计数，不会由「客户访问」推算"
              >
                <InputNumber style={{ width: '100%' }} min={0} placeholder="留空表示还没统计" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="published_on" label="发布日期（已发布建议填）">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="author" label="作者/负责人">
            <Input placeholder="如：张三" />
          </Form.Item>
          <Form.Item name="url" label="内容链接">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} />
          </Form.Item>

          {/* ── AI 生成正文（TASK-038）── */}
          <Card
            size="small"
            variant="outlined"
            style={{ marginBottom: 16, background: '#F7F9FC' }}
            title={
              <Space size={6}>
                <RobotOutlined style={{ color: '#2563EB' }} />
                <span>AI 生成正文</span>
                <Text type="secondary" style={{ fontWeight: 400, fontSize: 11 }}>
                  可参考知识库，也可纯用通用场景写作
                </Text>
              </Space>
            }
          >
            <Form.Item
              name="gen_topic"
              label="写作主题"
              extra="留空则用上面的「标题」作为主题"
              style={{ marginBottom: 12 }}
            >
              <Input placeholder="如：算力一体机如何降低企业 AI 部署门槛" />
            </Form.Item>

            <Row gutter={12}>
              <Col span={10}>
                <Form.Item name="target_length" label="篇幅" initialValue="MEDIUM" style={{ marginBottom: 12 }}>
                  <Select
                    options={[
                      { value: 'SHORT', label: '短（约 300 字）' },
                      { value: 'MEDIUM', label: '中（约 600 字）' },
                      { value: 'LONG', label: '长（约 1000 字）' },
                    ]}
                  />
                </Form.Item>
              </Col>
              <Col span={14}>
                <Form.Item name="use_knowledge" label="写作依据" initialValue={true} style={{ marginBottom: 12 }}>
                  <Select
                    options={[
                      { value: true, label: '先检索知识库，作为事实依据' },
                      { value: false, label: '不用知识库，按通用场景写' },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>

            <Space>
              <Button
                type="primary"
                icon={<RobotOutlined />}
                loading={generating}
                onClick={() => void runGenerate()}
              >
                {generating ? '生成中…' : 'AI 生成'}
              </Button>
              <Text type="secondary" style={{ fontSize: 11 }}>
                ★ 生成结果只填进表单，你核对后再点「创建」才入库
              </Text>
            </Space>

            {genResult && (
              <Alert
                type={genResult.used_knowledge ? 'success' : 'warning'}
                showIcon
                style={{ marginTop: 12 }}
                message={`已生成约 ${genResult.char_count} 字（目标 ${genResult.target_range}）`}
                description={
                  <Space orientation="vertical" size={4}>
                    <Text style={{ fontSize: 12 }}>{genResult.note}</Text>
                    {genResult.sources.length > 0 && (
                      <Space size={4} wrap>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          引用资料：
                        </Text>
                        {genResult.sources.map((sv) => (
                          <Tooltip key={sv.evidence_ref} title={sv.evidence_ref}>
                            <Tag color="purple" style={{ marginInlineEnd: 0 }}>
                              {sv.document_title} 第{sv.seq}段
                            </Tag>
                          </Tooltip>
                        ))}
                      </Space>
                    )}
                  </Space>
                }
              />
            )}
          </Card>

          <Form.Item
            name="body"
            label="正文"
            extra={
              genIsAi
                ? '★ 当前正文由 AI 生成，尚未人工复核；创建后会标记出来，改完可直接保存'
                : '可手写，也可用上面的 AI 生成。字数不限 —— 篇幅只是写作目标'
            }
          >
            <Input.TextArea rows={10} placeholder="正文内容…" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 流转状态 */}
      <Modal
        title={statusTarget ? `流转状态：${statusTarget.title}` : '流转状态'}
        open={!!statusTarget}
        onCancel={() => setStatusTarget(null)}
        onOk={() => void submitStatus()}
        confirmLoading={saving}
        okText="确认"
      >
        {statusTarget && (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={`当前状态：${statusTarget.status_label}`}
              description="草稿 → 待审核 → 已发布；审核不通过可退回草稿；已发布可下线后重新发布；归档后只能先回草稿。"
            />
            <Select
              style={{ width: '100%' }}
              placeholder="选择目标状态"
              value={statusValue}
              onChange={(v) => setStatusValue(v as ContentStatus)}
              options={[
                { value: 'DRAFT', label: '草稿' },
                { value: 'REVIEW', label: '待审核' },
                { value: 'PUBLISHED', label: '已发布' },
                { value: 'UNPUBLISHED', label: '已下线' },
                { value: 'ARCHIVED', label: '已归档' },
              ]}
            />
          </Space>
        )}
      </Modal>

      <Text type="secondary" style={{ fontSize: 12 }}>
        {summary?.note ?? ''}
      </Text>
    </Space>
  )
}
