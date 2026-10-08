import {
  BookOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  TagsOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '../api/client'
import type {
  KnowledgeAnalyzeResponse,
  KnowledgeDocumentDetail,
  KnowledgeDocumentItem,
  KnowledgeListResponse,
  KnowledgeSearchResponse,
  KnowledgeSummaryResponse,
} from '../api/types'
import { KNOWLEDGE_DOC_TYPE_OPTIONS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

export default function KnowledgePage() {
  const [list, setList] = useState<KnowledgeListResponse | null>(null)
  const [summary, setSummary] = useState<KnowledgeSummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const [detail, setDetail] = useState<KnowledgeDocumentDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  // 检索
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResponse | null>(null)

  // 分析
  const [question, setQuestion] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<KnowledgeAnalyzeResponse | null>(null)

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([
        apiGet<KnowledgeListResponse>('/api/knowledge/documents?page=1&page_size=50'),
        apiGet<KnowledgeSummaryResponse>('/api/knowledge/summary'),
      ])
      setList(l)
      setSummary(s)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await apiPost<KnowledgeDocumentDetail>('/api/knowledge/documents', {
        title: v.title,
        doc_type: v.doc_type,
        source_note: v.source_note ?? null,
        tags: v.tags ?? [],
        effective_to: v.effective_to ? v.effective_to.format('YYYY-MM-DD') : null,
        source_text: v.source_text,
      })
      message.success('资料已导入并完成切片')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '导入失败')
    } finally {
      setSaving(false)
    }
  }

  const openDetail = async (id: number) => {
    setDetailOpen(true)
    try {
      setDetail(await apiGet<KnowledgeDocumentDetail>(`/api/knowledge/documents/${id}`))
    } catch (err) {
      message.error((err as { message?: string }).message ?? '读取失败')
    }
  }

  const removeDoc = async (row: KnowledgeDocumentItem) => {
    try {
      await apiDelete<KnowledgeDocumentItem>(`/api/knowledge/documents/${row.id}`)
      message.success('已移除（软删除，历史引用仍可回溯）')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '移除失败')
    }
  }

  const runSearch = async () => {
    if (!q.trim()) return
    setSearching(true)
    try {
      setSearchResult(
        await apiGet<KnowledgeSearchResponse>(
          `/api/knowledge/search?q=${encodeURIComponent(q.trim())}&top_k=10`,
        ),
      )
    } catch (err) {
      message.error((err as { message?: string }).message ?? '检索失败')
    } finally {
      setSearching(false)
    }
  }

  const runAnalyze = async () => {
    if (!question.trim()) return
    setAnalyzing(true)
    try {
      setAnalysis(
        await apiPost<KnowledgeAnalyzeResponse>('/api/knowledge/analyze', {
          question: question.trim(),
          top_k: 6,
        }),
      )
    } catch (err) {
      message.error((err as { message?: string }).message ?? '分析失败')
    } finally {
      setAnalyzing(false)
    }
  }

  const cards = [
    { title: '资料总数', icon: <BookOutlined />, color: '#2563EB', evidence: summary?.total },
    {
      title: '知识片段',
      icon: <FileTextOutlined />,
      color: '#7C3AED',
      evidence: summary?.chunk_total,
    },
    {
      title: '已失效资料',
      icon: <TagsOutlined />,
      color: '#F59E0B',
      evidence: summary?.expired_total,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            知识库
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            导入产品/市场/竞品资料 → AI 结合资料与真实业务数据做分析（资料是背景知识，不是业务事实）
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              导入资料
            </Button>
          </Space>
        </Col>
      </Row>

      {summary && (
        <Alert
          type="info"
          showIcon
          message="本知识库用关键词检索，没有用向量库"
          description={summary.note}
        />
      )}

      <Row gutter={16}>
        {cards.map((c) => (
          <Col key={c.title} xs={24} sm={8}>
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

      {/* 检索 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <SearchOutlined />
            <span>检索资料</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 11 }}>
            命中原因是「切片包含这些词」，不是语义相似度
          </Text>
        }
      >
        <Space.Compact style={{ width: '100%', maxWidth: 560 }}>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onPressEnter={() => void runSearch()}
            placeholder="如：交付周期 / 算力 / 折扣"
          />
          <Button type="primary" loading={searching} onClick={() => void runSearch()}>
            检索
          </Button>
        </Space.Compact>

        {searchResult && (
          <div style={{ marginTop: 12 }}>
            <Space size={8} wrap style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                检索词：
              </Text>
              {searchResult.terms.map((t) => (
                <Tag key={t} style={{ marginInlineEnd: 0 }}>
                  {t}
                </Tag>
              ))}
              <Text type="secondary" style={{ fontSize: 12 }}>
                命中 {searchResult.total.state === 'VALID' ? searchResult.total.value : 0} 片
              </Text>
            </Space>
            {searchResult.hits.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">没有检索到相关资料</Text>}
              />
            ) : (
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                {searchResult.hits.map((h) => (
                  <Card key={h.chunk_id} size="small" variant="outlined" styles={{ body: { padding: 12 } }}>
                    <Space size={8} wrap style={{ marginBottom: 4 }}>
                      <Tag color="blue">{h.doc_type_label}</Tag>
                      <Text style={{ fontSize: 12 }}>{h.document_title}</Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        第 {h.seq} 段
                      </Text>
                      <Tooltip title={`命中词：${h.matched_terms.join('、')}`}>
                        <Tag color="purple" style={{ cursor: 'help' }}>
                          score {h.score}
                        </Tag>
                      </Tooltip>
                    </Space>
                    <div style={{ fontSize: 13, color: '#374151' }}>{h.content}</div>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {h.evidence_ref}
                    </Text>
                  </Card>
                ))}
              </Space>
            )}
          </div>
        )}
      </Card>

      {/* 分析 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <RobotOutlined />
            <span>结合资料与业务数据做分析</span>
          </Space>
        }
        extra={
          <Tooltip title="资料是背景知识，不是本公司业务事实；真实业务数据来自系统查库">
            <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
              两类内容分开呈现
            </Text>
          </Tooltip>
        }
      >
        <Space.Compact style={{ width: '100%', maxWidth: 720 }}>
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onPressEnter={() => void runAnalyze()}
            placeholder="如：我们的 H800 一体机适合什么客户？目前手上客户情况如何？"
          />
          <Button type="primary" loading={analyzing} onClick={() => void runAnalyze()}>
            分析
          </Button>
        </Space.Compact>

        {analysis && (
          <div style={{ marginTop: 14 }}>
            <Alert
              type={analysis.llm_error ? 'warning' : 'success'}
              showIcon
              message="分析结论"
              description={analysis.analysis}
            />
            {analysis.llm_error && (
              <Text type="danger" style={{ fontSize: 12 }}>
                AI 不可用：{analysis.llm_error}
              </Text>
            )}

            <Row gutter={16} style={{ marginTop: 12 }}>
              <Col span={12}>
                <Card
                  size="small"
                  variant="outlined"
                  title={
                    <Space size={6}>
                      <Tag color="purple">参考资料</Tag>
                      <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                        ★ 背景知识，不是本公司业务事实
                      </Text>
                    </Space>
                  }
                  styles={{ body: { padding: 12, maxHeight: 300, overflow: 'auto' } }}
                >
                  {analysis.knowledge_used.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      没有检索到相关资料
                    </Text>
                  ) : (
                    <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                      {analysis.knowledge_used.map((h) => (
                        <div key={h.chunk_id}>
                          <Text style={{ fontSize: 12.5 }}>{h.content.slice(0, 120)}</Text>
                          <div>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              {h.document_title} · 第 {h.seq} 段 · {h.evidence_ref}
                            </Text>
                          </div>
                        </div>
                      ))}
                    </Space>
                  )}
                </Card>
              </Col>
              <Col span={12}>
                <Card
                  size="small"
                  variant="outlined"
                  title={
                    <Space size={6}>
                      <Tag color="blue">真实业务数据</Tag>
                      <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                        系统查库所得
                      </Text>
                    </Space>
                  }
                  styles={{ body: { padding: 12, maxHeight: 300, overflow: 'auto' } }}
                >
                  {analysis.business_data.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      取不到业务数据
                    </Text>
                  ) : (
                    <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                      {analysis.business_data.map((b) => (
                        <Row key={b.key} justify="space-between">
                          <Text style={{ fontSize: 12.5 }}>{b.label}</Text>
                          <Text strong>
                            <EvidenceNumber evidence={b.evidence} />
                          </Text>
                        </Row>
                      ))}
                    </Space>
                  )}
                </Card>
              </Col>
            </Row>

            {(analysis.from_knowledge.length > 0 || analysis.from_business_data.length > 0) && (
              <Collapse
                size="small"
                style={{ marginTop: 12 }}
                items={[
                  {
                    key: 'src',
                    label: '结论分别来自哪里（模型自述）',
                    children: (
                      <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                        <Text style={{ fontSize: 12 }}>
                          <Tag color="purple">来自资料</Tag>
                          {analysis.from_knowledge.join('；') || '无'}
                        </Text>
                        <Text style={{ fontSize: 12 }}>
                          <Tag color="blue">来自业务数据</Tag>
                          {analysis.from_business_data.join('；') || '无'}
                        </Text>
                        {analysis.gaps.length > 0 && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            还缺：{analysis.gaps.join('；')}
                          </Text>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </div>
        )}
      </Card>

      {/* 资料清单 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>资料清单</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 份` : '暂无'}
            </Text>
          </Space>
        }
        styles={{ body: { padding: (list?.items.length ?? 0) ? 0 : 24 } }}
      >
        {(list?.items.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '知识库还是空的'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点「导入资料」粘贴产品/市场/竞品资料原文（系统不联网抓取）
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<KnowledgeDocumentItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            columns={[
              {
                title: '资料',
                dataIndex: 'title',
                render: (v: string, r) => (
                  <Space orientation="vertical" size={0}>
                    <Space size={6}>
                      <Text>{v}</Text>
                      {r.is_expired && <Tag color="orange">已失效</Tag>}
                    </Space>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {r.source_type_label}｜原文 {r.source_length} 字｜{r.chunk_count} 段
                    </Text>
                  </Space>
                ),
              },
              {
                title: '类型',
                dataIndex: 'doc_type_label',
                width: 100,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: '标签',
                dataIndex: 'tags',
                width: 180,
                render: (tags: string[]) =>
                  tags.length ? (
                    <Space size={4} wrap>
                      {tags.map((t) => (
                        <Tag key={t} style={{ marginInlineEnd: 0 }}>
                          {t}
                        </Tag>
                      ))}
                    </Space>
                  ) : (
                    <Text type="secondary">—</Text>
                  ),
              },
              {
                title: '有效期至',
                dataIndex: 'effective_to',
                width: 120,
                render: (v: string | null) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v ?? '长期有效'}
                  </Text>
                ),
              },
              {
                title: '导入时间',
                dataIndex: 'created_at',
                width: 150,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '操作',
                key: 'action',
                width: 130,
                render: (_: unknown, r) => (
                  <Space size={4}>
                    <Button size="small" type="link" onClick={() => void openDetail(r.id)}>
                      查看
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => void removeDoc(r)}
                    />
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 导入资料 */}
      <Modal
        title="导入资料"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="导入并切片"
        width={720}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Row gutter={12}>
            <Col span={10}>
              <Form.Item name="doc_type" label="资料类型" initialValue="PRODUCT">
                <Select options={KNOWLEDGE_DOC_TYPE_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={14}>
              <Form.Item name="title" label="标题" rules={[{ required: true, message: '填个标题' }]}>
                <Input placeholder="如：智算一体机 H800 产品资料" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={14}>
              <Form.Item name="tags" label="标签（便于检索时过滤）">
                <Select
                  mode="tags"
                  placeholder="如 H800 / 一体机 / 交付"
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item name="effective_to" label="失效日期（不填=长期有效）">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="source_note" label="出处（仅记录，系统不会访问它）">
            <Input placeholder="如：内部产品手册 v2 / https://..." />
          </Form.Item>
          <Form.Item
            name="source_text"
            label="资料原文"
            rules={[
              { required: true, message: '必须粘贴原文' },
              { min: 50, message: '原文至少 50 字，否则切不出有意义的片段' },
            ]}
            extra="★ 原文会完整留存；每个片段都能定位回原文的具体位置（可核对）"
          >
            <Input.TextArea rows={10} placeholder="把资料原文粘贴到这里" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 资料详情 */}
      <Drawer
        title={detail ? `资料：${detail.title}` : '资料'}
        width={820}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detail && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="类型">{detail.doc_type_label}</Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source_type_label}</Descriptions.Item>
              <Descriptions.Item label="出处" span={2}>
                {detail.source_note ?? '—'}
                <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                  （仅记录，系统不会访问）
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="原文长度">{detail.source_length} 字</Descriptions.Item>
              <Descriptions.Item label="切片数">{detail.chunk_count} 段</Descriptions.Item>
              <Descriptions.Item label="有效期至" span={2}>
                {detail.effective_to ?? '长期有效'}
                {detail.is_expired && <Tag color="orange" style={{ marginLeft: 8 }}>已失效</Tag>}
              </Descriptions.Item>
            </Descriptions>

            <Card
              size="small"
              variant="outlined"
              title={
                <Space size={6}>
                  <span>切片（每段都能定位回原文位置）</span>
                  <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                    content == 原文[起:止]
                  </Text>
                </Space>
              }
              styles={{ body: { padding: 12, maxHeight: 420, overflow: 'auto' } }}
            >
              <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                {detail.chunks.map((c) => (
                  <div key={c.id}>
                    <Space size={6}>
                      <Tag>第 {c.seq} 段</Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        原文[{c.char_start}:{c.char_end}]｜{c.evidence_ref}
                      </Text>
                    </Space>
                    <div style={{ fontSize: 12.5, color: '#374151', whiteSpace: 'pre-wrap' }}>
                      {c.content}
                    </div>
                  </div>
                ))}
              </Space>
            </Card>

            <Collapse
              size="small"
              items={[
                {
                  key: 'src',
                  label: `查看原文（${detail.source_length} 字）`,
                  children: (
                    <div style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, color: '#4B5563' }}>
                      {detail.source_text}
                    </div>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
