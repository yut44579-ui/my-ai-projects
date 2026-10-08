import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  PlusOutlined,
  ProjectOutlined,
  ReloadOutlined,
  TeamOutlined,
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
  Progress,
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
  ProjectBoardResponse,
  ProjectItem,
  ProjectListResponse,
  ProjectStatus,
  ProjectStatusResponse,
} from '../api/types'
import { PRIORITY_COLORS, PRIORITY_LABELS, PROJECT_STATUS_COLORS } from '../api/types'
import { AiLabel } from '../components/AiDraftButton'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

/**
 * 进度展示。
 * ★ null = 未评估 → 显示「未评估」而不是 0%：把没评估显示成 0% 是虚假精确。
 */
function ProgressCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) {
    return (
      <Tooltip title="还没有评估进度（与「0%」不同）">
        <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
          未评估
        </Text>
      </Tooltip>
    )
  }
  return <Progress percent={value} size="small" style={{ marginBottom: 0, minWidth: 90 }} />
}

export default function ProjectsPage() {
  const navigate = useNavigate()
  const [board, setBoard] = useState<ProjectBoardResponse | null>(null)
  const [list, setList] = useState<ProjectListResponse | null>(null)
  const [view, setView] = useState('board')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [customers, setCustomers] = useState<CustomerItem[]>([])
  const [form] = Form.useForm()

  const [statusTarget, setStatusTarget] = useState<ProjectItem | null>(null)
  const [statusValue, setStatusValue] = useState<ProjectStatus | null>(null)
  const [linkTarget, setLinkTarget] = useState<ProjectItem | null>(null)
  const [linkIds, setLinkIds] = useState<number[]>([])

  const load = useCallback(async () => {
    try {
      const [b, l] = await Promise.all([
        apiGet<ProjectBoardResponse>('/api/projects/board'),
        apiGet<ProjectListResponse>('/api/projects?page=1&page_size=50'),
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

  const loadCustomers = async () => {
    try {
      const res = await apiGet<{ items: CustomerItem[] }>('/api/customers?page=1&page_size=100')
      setCustomers(res.items)
    } catch {
      /* 拉不到不影响其它功能 */
    }
  }

  const openCreate = async () => {
    setCreateOpen(true)
    await loadCustomers()
  }

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await apiPost<ProjectItem>('/api/projects', {
        name: v.name,
        code: v.code ?? null,
        status: v.status ?? 'PLANNING',
        priority: v.priority ?? 'MEDIUM',
        progress: v.progress ?? null,
        start_date: v.start_date ? v.start_date.format('YYYY-MM-DD') : null,
        due_date: v.due_date ? v.due_date.format('YYYY-MM-DD') : null,
        owner: v.owner ?? null,
        description: v.description ?? null,
        customer_ids: v.customer_ids ?? [],
      })
      message.success('项目已创建')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const submitStatus = async () => {
    if (!statusTarget || !statusValue) return
    setSaving(true)
    try {
      const res = await apiPost<ProjectStatusResponse>(`/api/projects/${statusTarget.id}/status`, {
        status: statusValue,
      })
      message[res.changed ? 'success' : 'info'](res.message)
      setStatusTarget(null)
      setStatusValue(null)
      setReloadKey((k) => k + 1)
    } catch (err) {
      // ★ 非法流转（如向后回退）由后端拒绝，把后端原话显示出来
      message.error((err as { message?: string }).message ?? '状态流转失败')
    } finally {
      setSaving(false)
    }
  }

  const submitLink = async () => {
    if (!linkTarget || !linkIds.length) return
    setSaving(true)
    try {
      await apiPost<ProjectItem>(`/api/projects/${linkTarget.id}/customers`, { customer_ids: linkIds })
      message.success('已关联客户')
      setLinkTarget(null)
      setLinkIds([])
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '关联失败')
    } finally {
      setSaving(false)
    }
  }

  const cards = [
    { title: '项目总数', icon: <ProjectOutlined />, color: '#2563EB', evidence: board?.total },
    { title: '进行中', icon: <TeamOutlined />, color: '#F59E0B', evidence: board?.active_total },
    {
      title: '已逾期',
      icon: <ExclamationCircleOutlined />,
      color: '#DC2626',
      evidence: board?.overdue_total,
    },
    {
      title: '进行中平均进度',
      icon: <CheckCircleOutlined />,
      color: '#16A34A',
      evidence: board?.avg_progress,
      suffix: '%',
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            项目管理
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            一个项目可服务多个客户（N:N）；进度由人工填写，未评估显示「未评估」而不是 0%
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
              新建项目
            </Button>
          </Space>
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
                {c.evidence ? (
                  <>
                    <EvidenceNumber evidence={c.evidence} />
                    {c.suffix && c.evidence.state === 'VALID' && (
                      <Text style={{ fontSize: 14, fontWeight: 400 }}>{c.suffix}</Text>
                    )}
                  </>
                ) : (
                  <Text type="secondary">—</Text>
                )}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {view === 'board' ? (
        <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}>
          {(board?.columns ?? []).map((col) => (
            <div key={col.status} style={{ flex: '0 0 270px', minWidth: 270 }}>
              <Card
                variant="outlined"
                size="small"
                title={
                  <Space size={8}>
                    <Badge color={PROJECT_STATUS_COLORS[col.status]} />
                    <span style={{ fontSize: 13 }}>{col.label}</span>
                    <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                      {col.count}
                    </Text>
                  </Space>
                }
                styles={{ body: { padding: col.items.length ? 8 : 20, minHeight: 110 } }}
              >
                {col.items.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    暂无
                  </Text>
                ) : (
                  <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                    {col.items.map((p) => (
                      <Card key={p.id} size="small" variant="outlined" styles={{ body: { padding: 10 } }}>
                        <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
                          <Text style={{ fontSize: 13, fontWeight: 500 }} ellipsis>
                            {p.name}
                          </Text>
                          {p.is_overdue && (
                            <Tooltip title="计划完成日已过且未结束（日期比较，不是延期预测）">
                              <Tag color="red" style={{ marginInlineEnd: 0, fontSize: 11 }}>
                                逾期
                              </Tag>
                            </Tooltip>
                          )}
                        </Space>
                        {p.code && (
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {p.code}
                          </Text>
                        )}
                        <div style={{ marginTop: 6 }}>
                          <ProgressCell value={p.progress} />
                        </div>
                        <div style={{ marginTop: 4 }}>
                          <Tag color={PRIORITY_COLORS[p.priority]} style={{ marginInlineEnd: 4 }}>
                            {PRIORITY_LABELS[p.priority]}
                          </Tag>
                          {p.customer_count > 0 && (
                            <Tooltip title={p.customers.map((c) => c.customer_name).join('、')}>
                              <Tag style={{ marginInlineEnd: 0 }}>
                                <TeamOutlined /> {p.customer_count}
                              </Tag>
                            </Tooltip>
                          )}
                        </div>
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            <ClockCircleOutlined /> {p.due_date ?? '未定完成日'}
                          </Text>
                        </div>
                        <Space size={8} style={{ marginTop: 8 }}>
                          <Button
                            size="small"
                            type="link"
                            style={{ padding: 0, fontSize: 12 }}
                            onClick={() => {
                              setStatusTarget(p)
                              setStatusValue(null)
                            }}
                          >
                            流转状态 <ArrowRightOutlined style={{ fontSize: 10 }} />
                          </Button>
                          <Button
                            size="small"
                            type="link"
                            style={{ padding: 0, fontSize: 12 }}
                            onClick={() => {
                              setLinkTarget(p)
                              setLinkIds([])
                              void loadCustomers()
                            }}
                          >
                            关联客户
                          </Button>
                        </Space>
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
                  <Text>{loading ? '加载中…' : '还没有项目'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    项目与客户是 N:N 关系；一个项目可以服务多个客户
                  </Text>
                </Space>
              }
            />
          ) : (
            <Table<ProjectItem>
              rowKey="id"
              size="middle"
              loading={loading}
              pagination={false}
              dataSource={list?.items ?? []}
              columns={[
                {
                  title: '项目',
                  dataIndex: 'name',
                  render: (v: string, r) => (
                    <Space size={6}>
                      <Text>{v}</Text>
                      {r.is_overdue && <Tag color="red">逾期</Tag>}
                    </Space>
                  ),
                },
                { title: '编号', dataIndex: 'code', width: 130, render: (v: string | null) => v ?? '—' },
                {
                  title: '状态',
                  dataIndex: 'status_label',
                  width: 100,
                  render: (v: string, r) => <Tag color={PROJECT_STATUS_COLORS[r.status]}>{v}</Tag>,
                },
                {
                  title: '进度',
                  width: 160,
                  render: (_: unknown, r) => <ProgressCell value={r.progress} />,
                },
                {
                  title: '关联客户',
                  dataIndex: 'customer_count',
                  width: 96,
                  render: (v: number, r) =>
                    v === 0 ? (
                      <Text type="secondary">0</Text>
                    ) : (
                      <Tooltip title={r.customers.map((c) => c.customer_name).join('、')}>
                        <Button
                          type="link"
                          size="small"
                          style={{ padding: 0 }}
                          onClick={() => navigate(`/customers/${r.customers[0].customer_id}`)}
                        >
                          {v} 个
                        </Button>
                      </Tooltip>
                    ),
                },
                {
                  title: '计划完成',
                  dataIndex: 'due_date',
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
                  width: 88,
                  render: (_: unknown, r) => (
                    <Button
                      size="small"
                      type="link"
                      onClick={() => {
                        setStatusTarget(r)
                        setStatusValue(null)
                      }}
                    >
                      流转
                    </Button>
                  ),
                },
              ]}
            />
          )}
        </Card>
      )}

      {/* 新建项目 */}
      <Modal
        title="新建项目"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        width={620}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Row gutter={12}>
            <Col span={16}>
              <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '填个项目名' }]}>
                <Input placeholder="如：园区智能化改造" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="code" label="项目编号">
                <Input placeholder="如 PRJ-2026-001" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="status" label="状态" initialValue="PLANNING">
                <Select
                  options={[
                    { value: 'PLANNING', label: '筹备中' },
                    { value: 'IN_PROGRESS', label: '进行中' },
                    { value: 'ON_HOLD', label: '已暂停' },
                    { value: 'DELIVERED', label: '已交付' },
                    { value: 'CLOSED', label: '已结项' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="priority" label="优先级" initialValue="MEDIUM">
                <Select
                  options={[
                    { value: 'LOW', label: '低' },
                    { value: 'MEDIUM', label: '中' },
                    { value: 'HIGH', label: '高' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="progress" label="进度（不填=未评估）">
                <InputNumber style={{ width: '100%' }} min={0} max={100} addonAfter="%" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="start_date" label="开始日期">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="due_date" label="计划完成日">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="owner" label="负责人">
            <Input placeholder="如：张三" />
          </Form.Item>
          <Form.Item name="customer_ids" label="关联客户（可多选，N:N）">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              placeholder="选择参与该项目的客户"
              options={customers.map((c) => ({
                value: c.id,
                label: `${c.name}${c.company_name ? ' · ' + c.company_name : ''}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="description"
            label={
              <AiLabel
                text="项目说明"
                purpose="PROJECT_DESCRIPTION"
                context={{
                  // ★ 只把用户已经填好的信息给后端 —— 后端只用这些，缺的一律不提
                  name: form.getFieldValue('name'),
                  status: form.getFieldValue('status'),
                  priority: form.getFieldValue('priority'),
                }}
                onGenerated={(t) => form.setFieldValue('description', t)}
                guard={() => {
                  if (!form.getFieldValue('name')) {
                    message.warning('先把「项目名称」填上，AI 需要知道是哪个项目')
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

      {/* 流转状态 */}
      <Modal
        title={statusTarget ? `流转状态：${statusTarget.name}` : '流转状态'}
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
              description="只能向前推进，或取消。向后回退会被拒绝——真要修正请先取消再重新打开。"
            />
            <Select
              style={{ width: '100%' }}
              placeholder="选择目标状态"
              value={statusValue}
              onChange={(v) => setStatusValue(v as ProjectStatus)}
              options={[
                { value: 'IN_PROGRESS', label: '进行中' },
                { value: 'ON_HOLD', label: '已暂停' },
                { value: 'DELIVERED', label: '已交付' },
                { value: 'CLOSED', label: '已结项' },
                { value: 'CANCELLED', label: '已取消' },
              ]}
            />
          </Space>
        )}
      </Modal>

      {/* 关联客户 */}
      <Modal
        title={linkTarget ? `关联客户：${linkTarget.name}` : '关联客户'}
        open={!!linkTarget}
        onCancel={() => setLinkTarget(null)}
        onOk={() => void submitLink()}
        confirmLoading={saving}
        okText="关联"
      >
        {linkTarget && (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已关联 {linkTarget.customer_count} 个客户；重复选择已关联的客户不会产生重复记录（幂等）。
            </Text>
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              style={{ width: '100%' }}
              placeholder="选择要关联的客户"
              value={linkIds}
              onChange={(v) => setLinkIds(v as number[])}
              options={customers.map((c) => ({
                value: c.id,
                label: `${c.name}${c.company_name ? ' · ' + c.company_name : ''}`,
              }))}
            />
          </Space>
        )}
      </Modal>

      <Text type="secondary" style={{ fontSize: 12 }}>
        {board?.note ?? ''}
      </Text>
    </Space>
  )
}
