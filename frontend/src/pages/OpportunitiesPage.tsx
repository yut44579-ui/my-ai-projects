import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RiseOutlined,
  ThunderboltOutlined,
  WalletOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Badge,
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
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type {
  CustomerItem,
  OpportunityBoardResponse,
  OpportunityItem,
  OpportunityListResponse,
  OpportunityStage,
  OpportunityStageResponse,
} from '../api/types'
import { PRIORITY_COLORS, PRIORITY_LABELS, STAGE_COLORS } from '../api/types'
import { AiLabel } from '../components/AiDraftButton'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

/** 金额展示（★ 只显示后端给的 EvidenceValue，前端不做任何换算/加总） */
function Amount({ evidence, currency }: { evidence: OpportunityItem['amount']; currency: string }) {
  if (evidence.state !== 'VALID' || evidence.value === null) {
    return (
      <Tooltip title={evidence.reason ?? '暂无数据'}>
        <Text type="secondary">—</Text>
      </Tooltip>
    )
  }
  return (
    <Text strong>
      {currency} {Number(evidence.value).toLocaleString()}
    </Text>
  )
}

export default function OpportunitiesPage() {
  const navigate = useNavigate()
  const [board, setBoard] = useState<OpportunityBoardResponse | null>(null)
  const [list, setList] = useState<OpportunityListResponse | null>(null)
  const [view, setView] = useState<string>('board')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [customers, setCustomers] = useState<CustomerItem[]>([])
  const [form] = Form.useForm()

  const [stageTarget, setStageTarget] = useState<OpportunityItem | null>(null)
  const [stageValue, setStageValue] = useState<OpportunityStage | null>(null)
  const [wonAmount, setWonAmount] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [b, l] = await Promise.all([
        apiGet<OpportunityBoardResponse>('/api/opportunities/board'),
        apiGet<OpportunityListResponse>('/api/opportunities?page=1&page_size=50'),
      ])
      setBoard(b)
      setList(l)
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

  const openCreate = async () => {
    setCreateOpen(true)
    try {
      const res = await apiGet<{ items: CustomerItem[] }>('/api/customers?page=1&page_size=100')
      setCustomers(res.items)
    } catch {
      /* 客户列表拉不到不影响弹窗，Select 会显示为空 */
    }
  }

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await apiPost<OpportunityItem>('/api/opportunities', {
        customer_id: v.customer_id,
        title: v.title,
        amount: v.amount ?? null,
        currency: v.currency ?? 'CNY',
        probability: v.probability ?? null,
        expected_close_date: v.expected_close_date ? v.expected_close_date.format('YYYY-MM-DD') : null,
        owner: v.owner ?? null,
        note: v.note ?? null,
      })
      message.success('商机已创建')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const submitStage = async () => {
    if (!stageTarget || !stageValue) return
    setSaving(true)
    try {
      const res = await apiPost<OpportunityStageResponse>(
        `/api/opportunities/${stageTarget.id}/stage`,
        { stage: stageValue, won_amount: wonAmount ?? null },
      )
      message[res.changed ? 'success' : 'info'](res.message)
      setStageTarget(null)
      setStageValue(null)
      setWonAmount(null)
      setReloadKey((k) => k + 1)
    } catch (err) {
      // ★ 非法流转（如向后回退）由后端拒绝，把后端的原话显示出来
      message.error((err as { message?: string }).message ?? '阶段流转失败')
    } finally {
      setSaving(false)
    }
  }

  const summaryCards = [
    { title: '商机总数', icon: <ThunderboltOutlined />, color: '#2563EB', evidence: board?.total },
    { title: '进行中', icon: <RiseOutlined />, color: '#F59E0B', evidence: board?.open_total },
    { title: '赢单', icon: <CheckCircleOutlined />, color: '#16A34A', evidence: board?.won_total },
    {
      title: '管道金额',
      icon: <WalletOutlined />,
      color: '#7C3AED',
      evidence: board?.pipeline_amount,
      money: true,
    },
    {
      title: '赢单金额',
      icon: <WalletOutlined />,
      color: '#16A34A',
      evidence: board?.won_amount_total,
      money: true,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            商机管理
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            一个客户可以有多笔商机（不同产品线各自推进）；金额未估算时显示「—」而不是 0
          </Text>
        </Col>
        <Col>
          <Space>
            <Segmented
              size="small"
              options={[
                { label: '看板', value: 'board' },
                { label: '列表', value: 'list' },
              ]}
              value={view}
              onChange={(v) => setView(String(v))}
            />
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => void openCreate()}>
              新建商机
            </Button>
          </Space>
        </Col>
      </Row>

      <Row gutter={16}>
        {summaryCards.map((c) => (
          <Col key={c.title} xs={24} sm={12} xl={{ flex: '0 0 20%' }} style={{ maxWidth: '20%' }}>
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

      {view === 'board' ? (
        <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}>
          {(board?.columns ?? []).map((col) => (
            <div key={col.stage} style={{ flex: '0 0 260px', minWidth: 260 }}>
              <Card
                variant="outlined"
                size="small"
                title={
                  <Space size={8}>
                    <Badge color={STAGE_COLORS[col.stage]} />
                    <span style={{ fontSize: 13 }}>{col.label}</span>
                    <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                      {col.count}
                    </Text>
                  </Space>
                }
                extra={
                  <Tooltip title="该阶段金额合计；没有已估金额时为「—」">
                    <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                      <EvidenceNumber evidence={col.amount_total} />
                    </Text>
                  </Tooltip>
                }
                styles={{ body: { padding: col.items.length ? 8 : 20, minHeight: 120 } }}
              >
                {col.items.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    暂无
                  </Text>
                ) : (
                  <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                    {col.items.map((op) => (
                      <Card
                        key={op.id}
                        size="small"
                        variant="outlined"
                        hoverable
                        styles={{ body: { padding: 10 } }}
                      >
                        <Text style={{ fontSize: 13, fontWeight: 500 }} ellipsis>
                          {op.title}
                        </Text>
                        <div style={{ marginTop: 4 }}>
                          <Button
                            type="link"
                            size="small"
                            style={{ padding: 0, fontSize: 12, height: 'auto' }}
                            onClick={() => navigate(`/customers/${op.customer_id}`)}
                          >
                            {op.customer_name}
                          </Button>
                        </div>
                        <div style={{ marginTop: 4 }}>
                          <Amount evidence={op.amount} currency={op.currency} />
                        </div>
                        <Space size={4} style={{ marginTop: 6 }} wrap>
                          <Tag color={PRIORITY_COLORS[op.priority]} style={{ marginInlineEnd: 0 }}>
                            {PRIORITY_LABELS[op.priority]}
                          </Tag>
                          {op.probability !== null && (
                            <Tooltip title="赢单概率由人工填写，不是模型预测">
                              <Tag style={{ marginInlineEnd: 0 }}>{op.probability}%</Tag>
                            </Tooltip>
                          )}
                        </Space>
                        <div style={{ marginTop: 8 }}>
                          <Button
                            size="small"
                            type="link"
                            style={{ padding: 0, fontSize: 12 }}
                            onClick={() => {
                              setStageTarget(op)
                              setStageValue(null)
                              setWonAmount(null)
                            }}
                          >
                            推进阶段 <ArrowRightOutlined style={{ fontSize: 10 }} />
                          </Button>
                        </div>
                      </Card>
                    ))}
                  </Space>
                )}
              </Card>
            </div>
          ))}
        </div>
      ) : (
        <Card variant="outlined" styles={{ body: { padding: (list?.items.length ?? 0) ? 0 : 24 } }}>
          {(list?.items.length ?? 0) === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Text>{loading ? '加载中…' : '还没有商机'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    商机必须挂在一个真实客户上；一个客户可以有多笔
                  </Text>
                </Space>
              }
            />
          ) : (
            <Table<OpportunityItem>
              rowKey="id"
              size="middle"
              loading={loading}
              pagination={false}
              dataSource={list?.items ?? []}
              columns={[
                { title: '商机', dataIndex: 'title' },
                {
                  title: '客户',
                  dataIndex: 'customer_name',
                  width: 150,
                  render: (v: string, r) => (
                    <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(`/customers/${r.customer_id}`)}>
                      {v}
                    </Button>
                  ),
                },
                {
                  title: '阶段',
                  dataIndex: 'stage_label',
                  width: 110,
                  render: (v: string, r) => <Tag color={STAGE_COLORS[r.stage]}>{v}</Tag>,
                },
                {
                  title: '金额',
                  width: 150,
                  render: (_: unknown, r) => <Amount evidence={r.amount} currency={r.currency} />,
                },
                {
                  title: '概率',
                  dataIndex: 'probability',
                  width: 80,
                  render: (v: number | null) =>
                    v === null ? <Text type="secondary">—</Text> : <Text>{v}%</Text>,
                },
                {
                  title: '预计成交',
                  dataIndex: 'expected_close_date',
                  width: 120,
                  render: (v: string | null) => (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {v ?? '—'}
                    </Text>
                  ),
                },
                { title: '负责人', dataIndex: 'owner', width: 90, render: (v: string | null) => v ?? '—' },
                {
                  title: '操作',
                  key: 'action',
                  width: 96,
                  render: (_: unknown, r) => (
                    <Button
                      size="small"
                      type="link"
                      onClick={() => {
                        setStageTarget(r)
                        setStageValue(null)
                        setWonAmount(null)
                      }}
                    >
                      推进
                    </Button>
                  ),
                },
              ]}
            />
          )}
        </Card>
      )}

      {/* 新建商机 */}
      <Modal
        title="新建商机"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="customer_id" label="所属客户" rules={[{ required: true, message: '必须选一个客户' }]}>
            <Select
              showSearch
              placeholder="搜索并选择客户"
              optionFilterProp="label"
              options={customers.map((c) => ({
                value: c.id,
                label: `${c.name}${c.company_name ? ' · ' + c.company_name : ''}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="title" label="商机名称" rules={[{ required: true, message: '填个名字' }]}>
            <Input placeholder="如：H800 采购意向" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={14}>
              <Form.Item name="amount" label="预计金额（不填=未估算，不是 0）">
                <InputNumber style={{ width: '100%' }} min={0} placeholder="留空表示还没估出来" />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item name="currency" label="币种" initialValue="CNY">
                <Select
                  options={[
                    { value: 'CNY', label: 'CNY 人民币' },
                    { value: 'USD', label: 'USD 美元' },
                    { value: 'EUR', label: 'EUR 欧元' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="probability" label="赢单概率（人工填写）">
                <InputNumber style={{ width: '100%' }} min={0} max={100} addonAfter="%" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="expected_close_date" label="预计成交日">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="owner" label="负责人">
            <Input placeholder="如：张三" />
          </Form.Item>
          <Form.Item
            name="note"
            label={
              <AiLabel
                text="备注"
                purpose="OPPORTUNITY_NOTE"
                context={{
                  title: form.getFieldValue('title'),
                  // ★ 刻意**不把金额/概率传给后端**：那两个是事实数字，
                  //   给了模型它就可能写进正文，而正文里的数字必须有人负责
                }}
                onGenerated={(t) => form.setFieldValue('note', t)}
                guard={() => {
                  if (!form.getFieldValue('title')) {
                    message.warning('先把「商机标题」填上，AI 需要知道在谈什么')
                    return false
                  }
                  return true
                }}
              />
            }
          >
            <Input.TextArea rows={2} placeholder="可手写，也可点上面的 AI 生成" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 推进阶段 */}
      <Modal
        title={stageTarget ? `推进阶段：${stageTarget.title}` : '推进阶段'}
        open={!!stageTarget}
        onCancel={() => setStageTarget(null)}
        onOk={() => void submitStage()}
        confirmLoading={saving}
        okText="确认推进"
      >
        {stageTarget && (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={`当前阶段：${stageTarget.stage_label}`}
              description="只能向前推进，或关闭为赢单/丢单。向后回退会被拒绝——真要修正请先关闭再重新打开，这样能保留它曾经到过哪里。"
            />
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                目标阶段
              </Text>
              <Select
                style={{ width: '100%', marginTop: 6 }}
                placeholder="选择目标阶段"
                value={stageValue}
                onChange={(v) => setStageValue(v as OpportunityStage)}
                options={[
                  { value: 'CONTACTED', label: '已联系' },
                  { value: 'QUALIFIED', label: '已确认需求' },
                  { value: 'VALIDATING', label: '方案验证中' },
                  { value: 'QUOTED', label: '已报价' },
                  { value: 'NEGOTIATING', label: '谈判中' },
                  { value: 'WON', label: '赢单' },
                  { value: 'CLOSED_LOST', label: '丢单' },
                ]}
              />
            </div>
            {stageValue === 'WON' && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  实际成交金额（留空则沿用预计金额）
                </Text>
                <InputNumber
                  style={{ width: '100%', marginTop: 6 }}
                  min={0}
                  value={wonAmount}
                  onChange={(v) => setWonAmount(v as number | null)}
                />
              </div>
            )}
            {stageValue === 'CLOSED_LOST' && (
              <Alert type="warning" showIcon message="标记为丢单后，这笔商机不再计入管道金额" />
            )}
          </Space>
        )}
      </Modal>

      <Text type="secondary" style={{ fontSize: 12 }}>
        {board?.note ?? ''}
      </Text>
    </Space>
  )
}
