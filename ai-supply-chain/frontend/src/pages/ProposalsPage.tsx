import {
  CheckOutlined,
  CloseOutlined,
  FileDoneOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
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
import { apiDelete, apiGet, apiPost } from '../api/client'
import type {
  ProposalCandidatesResponse,
  ProposalDetail,
  ProposalGenerateResponse,
  ProposalItem,
  ProposalListResponse,
  ProposalSummaryResponse,
} from '../api/types'
import { PROPOSAL_KIND_OPTIONS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

const STATUS_COLORS: Record<string, string> = {
  NONE: 'default',
  GENERATED: 'gold',
  CONFIRMED: 'green',
  REJECTED: 'red',
}

/**
 * 方案页（需求 §八 输出中心 → 方案）。
 *
 * ★ 与知识库/获客一致：AI 只产出草稿（GENERATED），**人工确认**后才可用。
 * ★ 生成时会把输入冻结下来（引用了哪些资料、哪些业务数字），
 *   所以每份方案都能核对"这句话是从哪来的"。
 * ★ 本页**没有发送功能** —— 确认只是标记可用，实际发送由人自己去做（§十六）。
 */
export default function ProposalsPage() {
  const [list, setList] = useState<ProposalListResponse | null>(null)
  const [summary, setSummary] = useState<ProposalSummaryResponse | null>(null)
  const [candidates, setCandidates] = useState<ProposalCandidatesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [filter, setFilter] = useState('ALL')

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const [detail, setDetail] = useState<ProposalDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (filter !== 'ALL') params.set('status', filter)
      const [l, s, c] = await Promise.all([
        apiGet<ProposalListResponse>(`/api/proposals?${params.toString()}`),
        apiGet<ProposalSummaryResponse>('/api/proposals/summary'),
        apiGet<ProposalCandidatesResponse>('/api/proposals/candidates'),
      ])
      setList(l)
      setSummary(s)
      setCandidates(c)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const openDetail = async (id: number) => {
    setDetailOpen(true)
    try {
      setDetail(await apiGet<ProposalDetail>(`/api/proposals/${id}`))
    } catch (err) {
      message.error((err as { message?: string }).message ?? '读取失败')
    }
  }

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const created = await apiPost<ProposalDetail>('/api/proposals', {
        title: v.title,
        kind: v.kind,
        customer_id: v.customer_id ?? null,
        opportunity_id: v.opportunity_id ?? null,
        target_name: v.target_name ?? null,
        requirement: v.requirement ?? null,
      })
      message.success('方案已创建；请点「生成草稿」')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
      await openDetail(created.id)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const runGenerate = async () => {
    if (!detail) return
    setBusy(true)
    try {
      const res = await apiPost<ProposalGenerateResponse>(
        `/api/proposals/${detail.id}/generate`,
        { top_k: 5 },
      )
      setDetail(res.proposal)
      message.success(
        `已生成 ${res.section_count} 节，引用 ${res.knowledge_hit_count} 条知识库资料`,
      )
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setBusy(false)
    }
  }

  const runConfirm = async () => {
    if (!detail) return
    setBusy(true)
    try {
      setDetail(await apiPost<ProposalDetail>(`/api/proposals/${detail.id}/confirm`, {}))
      message.success('已确认可用；系统不会自动发送，请自行使用')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '确认失败')
    } finally {
      setBusy(false)
    }
  }

  const runReject = async () => {
    if (!detail) return
    setBusy(true)
    try {
      setDetail(
        await apiPost<ProposalDetail>(`/api/proposals/${detail.id}/reject`, {
          reason: '人工判定不可用',
        }),
      )
      message.info('已驳回')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '驳回失败')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (row: ProposalItem) => {
    try {
      await apiDelete<ProposalItem>(`/api/proposals/${row.id}`)
      message.success('已移除（软删除，记录保留）')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '移除失败')
    }
  }

  const cards = [
    { title: '方案总数', icon: <FileDoneOutlined />, color: '#2563EB', evidence: summary?.total },
    {
      title: '待人工确认',
      icon: <RobotOutlined />,
      color: '#F59E0B',
      evidence: summary?.pending_confirm_total,
    },
    {
      title: '已确认',
      icon: <CheckOutlined />,
      color: '#16A34A',
      evidence: summary?.confirmed_total,
    },
    {
      title: '已驳回',
      icon: <CloseOutlined />,
      color: '#DC2626',
      evidence: summary?.rejected_total,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            方案
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            选对象 → 填需求 → AI 依据知识库资料与真实业务数据生成草稿 → 你确认后使用
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建方案
            </Button>
          </Space>
        </Col>
      </Row>

      {summary && (
        <Alert
          type="info"
          showIcon
          message="方案基于真实资料生成，且必须人工确认"
          description={summary.note}
        />
      )}

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

      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>方案清单</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 份` : '暂无'}
            </Text>
          </Space>
        }
        extra={
          <Segmented
            size="small"
            options={[
              { label: '全部', value: 'ALL' },
              { label: '未生成', value: 'NONE' },
              { label: '待确认', value: 'GENERATED' },
              { label: '已确认', value: 'CONFIRMED' },
              { label: '已驳回', value: 'REJECTED' },
            ]}
            value={filter}
            onChange={(v) => setFilter(String(v))}
          />
        }
        styles={{ body: { padding: (list?.items.length ?? 0) ? 0 : 24 } }}
      >
        {(list?.items.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '还没有做过方案'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点「新建方案」选一个客户或商机，填上需求背景，再让 AI 生成草稿
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<ProposalItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            columns={[
              {
                title: '方案',
                dataIndex: 'title',
                render: (v: string, r) => (
                  <Space orientation="vertical" size={0}>
                    <Text>{v}</Text>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {r.kind_label}
                      {r.customer_name ? `｜${r.customer_name}` : ''}
                      {r.opportunity_title ? `｜${r.opportunity_title}` : ''}
                      {!r.customer_name && r.target_name ? `｜${r.target_name}` : ''}
                    </Text>
                  </Space>
                ),
              },
              {
                title: '状态',
                dataIndex: 'status_label',
                width: 120,
                render: (v: string, r) => <Tag color={STATUS_COLORS[r.status]}>{v}</Tag>,
              },
              {
                title: '节数',
                dataIndex: 'section_count',
                width: 70,
                render: (v: number) => (v ? v : <Text type="secondary">—</Text>),
              },
              {
                title: '确认人',
                dataIndex: 'confirmed_by',
                width: 90,
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '更新时间',
                dataIndex: 'updated_at',
                width: 160,
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
                    <Button size="small" type="link" danger onClick={() => void remove(r)}>
                      移除
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 新建方案 */}
      <Modal
        title="新建方案"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        width={680}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Row gutter={12}>
            <Col span={14}>
              <Form.Item name="title" label="方案标题" rules={[{ required: true, message: '填标题' }]}>
                <Input placeholder="如：智算一体机 H800 解决方案" />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item name="kind" label="方案类型" initialValue="SOLUTION">
                <Select options={PROPOSAL_KIND_OPTIONS} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="customer_id" label="关联客户">
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="选一个客户"
                  options={(candidates?.customers ?? []).map((c) => ({
                    value: c.id,
                    label: c.company_name ? `${c.name}（${c.company_name}）` : c.name,
                  }))}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="opportunity_id" label="关联商机">
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="选一个进行中的商机"
                  options={(candidates?.opportunities ?? []).map((o) => ({
                    value: o.id,
                    label: o.title,
                  }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="target_name"
            label="对象名称（没有客户档案时填这里）"
            extra="客户、商机、对象名称三者至少要有一个"
          >
            <Input placeholder="如：某智能制造公司" />
          </Form.Item>
          <Form.Item
            name="requirement"
            label="客户需求 / 背景"
            rules={[{ required: true, message: '填需求背景，否则生成的内容会空泛' }]}
            extra="★ AI 只会基于你写的内容 + 知识库资料生成，不许自行添加需求"
          >
            <Input.TextArea
              rows={5}
              placeholder="如：客户希望为大模型微调搭建本地算力，关注散热与供电条件，希望在现有预算内分期建设。"
            />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="创建后还要点「生成草稿」，并且人工确认后才可用"
            description="系统不会自动发送方案，也不替代报价审批。价格、折扣、交期承诺请走人工流程。"
          />
        </Form>
      </Modal>

      {/* 方案详情 */}
      <Drawer
        title={detail ? `方案：${detail.title}` : '方案'}
        width={900}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detail && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="类型">{detail.kind_label}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLORS[detail.status]}>{detail.status_label}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="客户">{detail.customer_name ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="商机">{detail.opportunity_title ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="需求背景" span={2}>
                {detail.requirement ?? '—'}
              </Descriptions.Item>
              {detail.confirmed_by && (
                <Descriptions.Item label="确认" span={2}>
                  {detail.confirmed_by}｜{stamp(detail.confirmed_at)}
                </Descriptions.Item>
              )}
              {detail.reject_reason && (
                <Descriptions.Item label="驳回原因" span={2}>
                  <Text type="danger">{detail.reject_reason}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>

            <Space wrap>
              <Button type="primary" loading={busy} onClick={() => void runGenerate()}>
                ① 生成草稿
              </Button>
              {detail.status === 'GENERATED' && (
                <>
                  <Button type="primary" ghost loading={busy} onClick={() => void runConfirm()}>
                    ② 确认可用
                  </Button>
                  <Button danger loading={busy} onClick={() => void runReject()}>
                    驳回
                  </Button>
                </>
              )}
            </Space>

            {detail.llm_error && (
              <Alert type="warning" showIcon message={`AI 不可用：${detail.llm_error}`} />
            )}

            {detail.content.length === 0 ? (
              <Alert
                type="info"
                showIcon
                message="还没有生成内容"
                description="点上面的「生成草稿」。生成会引用知识库里的产品资料与这个客户的真实业务数据。"
              />
            ) : (
              <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                {detail.content.map((sec, i) => (
                  <Card
                    key={i}
                    size="small"
                    variant="outlined"
                    title={sec.heading}
                    extra={
                      sec.basis ? (
                        <Tooltip title={sec.basis}>
                          <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                            依据
                          </Text>
                        </Tooltip>
                      ) : null
                    }
                  >
                    <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7 }}>
                      {sec.body}
                    </div>
                    {sec.basis && (
                      <div style={{ marginTop: 8 }}>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          依据：{sec.basis}
                        </Text>
                      </div>
                    )}
                  </Card>
                ))}
              </Space>
            )}

            {/* ★ 冻结输入：核对"这份方案当初依据什么" */}
            {detail.inputs && (
              <Collapse
                size="small"
                items={[
                  {
                    key: 'inputs',
                    label: `生成时的输入快照（${detail.inputs.knowledge_hits.length} 条资料 + ${
                      Object.keys(detail.inputs.business_data).length
                    } 项业务数据）`,
                    children: (
                      <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          冻结时间：{stamp(detail.inputs.frozen_at)}
                        </Text>
                        <div>
                          <Text strong style={{ fontSize: 12 }}>
                            引用的知识库资料
                          </Text>
                          <Space orientation="vertical" size={6} style={{ width: '100%', marginTop: 4 }}>
                            {detail.inputs.knowledge_hits.map((h) => (
                              <div key={h.evidence_ref}>
                                <Space size={6}>
                                  <Tag color="purple">{h.document_title}</Tag>
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    第 {h.seq} 段｜{h.evidence_ref}｜score {h.score}
                                  </Text>
                                </Space>
                                <div style={{ fontSize: 12, color: '#4B5563' }}>
                                  {h.content.slice(0, 120)}
                                </div>
                              </div>
                            ))}
                          </Space>
                        </div>
                        <div>
                          <Text strong style={{ fontSize: 12 }}>
                            使用的业务数据
                          </Text>
                          <div style={{ marginTop: 4 }}>
                            {Object.entries(detail.inputs.business_data).map(([k, v]) => (
                              <div key={k} style={{ fontSize: 12 }}>
                                <Text type="secondary">{k}：</Text>
                                <Text>{String(v)}</Text>
                              </div>
                            ))}
                          </div>
                        </div>
                        {detail.inputs.missing_info.length > 0 && (
                          <div>
                            <Text strong style={{ fontSize: 12 }}>
                              <WarningOutlined style={{ color: '#F59E0B' }} /> 生成时标记的缺口
                            </Text>
                            <Space orientation="vertical" size={2} style={{ width: '100%', marginTop: 4 }}>
                              {detail.inputs.missing_info.map((m, i) => (
                                <Text key={i} type="secondary" style={{ fontSize: 12 }}>
                                  · {m}
                                </Text>
                              ))}
                            </Space>
                          </div>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
