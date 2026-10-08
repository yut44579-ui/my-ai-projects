import {
  AlertOutlined,
  CheckOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ScanOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  message,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type { RiskEventItem, RiskListResponse, RiskScanResponse, RiskSummaryResponse } from '../api/types'
import { RISK_LEVEL_LABELS, RISK_STATUS_LABELS, RISK_TYPE_LABELS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text } = Typography

/** 等级 → Tag 配色。★ 资金/不可逆类才是红，其余橙 */
const LEVEL_COLORS: Record<string, string> = {
  HIGH: 'red',
  ATTENTION: 'orange',
  LOW: 'default',
}

const STATUS_COLORS: Record<string, string> = {
  OPEN: 'volcano',
  RESOLVED: 'green',
  DISMISSED: 'default',
}

type SummaryCard = {
  title: string
  icon: React.ReactNode
  color: string
  evidence?: RiskSummaryResponse['total']
}

export default function RiskCenterPage() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<RiskSummaryResponse | null>(null)
  const [list, setList] = useState<RiskListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('OPEN')
  const [levelFilter, setLevelFilter] = useState<string>('ALL')
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (statusFilter !== 'ALL') params.set('status', statusFilter)
      if (levelFilter !== 'ALL') params.set('level', levelFilter)
      const [s, l] = await Promise.all([
        apiGet<RiskSummaryResponse>('/api/risks/summary'),
        apiGet<RiskListResponse>(`/api/risks?${params.toString()}`),
      ])
      setSummary(s)
      setList(l)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, levelFilter])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const runScan = async () => {
    setScanning(true)
    try {
      const res = await apiPost<RiskScanResponse>('/api/risks/scan', {})
      message.success(res.note)
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '扫描失败')
    } finally {
      setScanning(false)
    }
  }

  const resolve = async (id: number, status: 'RESOLVED' | 'DISMISSED') => {
    try {
      await apiPost<RiskEventItem>(`/api/risks/${id}/resolve`, {
        status,
        note: status === 'RESOLVED' ? '已人工核实' : '误报，忽略',
      })
      message.success(status === 'RESOLVED' ? '已标记为已处理' : '已忽略')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '处置失败')
    }
  }

  const cards: SummaryCard[] = [
    { title: '风险总数', icon: <AlertOutlined />, color: '#6B7280', evidence: summary?.total },
    { title: '待处理', icon: <ScanOutlined />, color: '#DC2626', evidence: summary?.open_total },
    { title: '高风险待处理', icon: <StopOutlined />, color: '#DC2626', evidence: summary?.high_open },
    {
      title: '需关注待处理',
      icon: <SafetyCertificateOutlined />,
      color: '#F59E0B',
      evidence: summary?.attention_open,
    },
    { title: '已处置', icon: <CheckOutlined />, color: '#16A34A', evidence: summary?.resolved_total },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      {/* 汇总 */}
      <Row gutter={[16, 16]}>
        {cards.map((c) => (
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
              <div style={{ fontSize: 26, fontWeight: 600 }}>
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 说明 + 操作 */}
      <Card variant="outlined" styles={{ body: { padding: 16 } }}>
        <Row justify="space-between" align="middle" gutter={16}>
          <Col flex="auto">
            <Descriptions column={1} size="small" colon={false}>
              <Descriptions.Item label="判定依据">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {summary?.note ?? '加载中…'}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="类型分布">
                {(summary?.by_type.length ?? 0) === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    暂无
                  </Text>
                ) : (
                  <Space size={8} wrap>
                    {summary?.by_type.map((t) => (
                      <Tag key={t.event_type}>
                        {t.label} {t.count}
                      </Tag>
                    ))}
                  </Space>
                )}
              </Descriptions.Item>
            </Descriptions>
          </Col>
          <Col flex="0 0 auto">
            <Space>
              <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
                刷新
              </Button>
              <Button type="primary" icon={<ScanOutlined />} loading={scanning} onClick={() => void runScan()}>
                扫描消息生成风险
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* 列表 + 筛选 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>风险明细</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 条` : '暂无'}
            </Text>
          </Space>
        }
        extra={
          <Space size={10}>
            <Segmented
              size="small"
              options={[
                { label: '待处理', value: 'OPEN' },
                { label: '已处理', value: 'RESOLVED' },
                { label: '已忽略', value: 'DISMISSED' },
                { label: '全部', value: 'ALL' },
              ]}
              value={statusFilter}
              onChange={(v) => setStatusFilter(String(v))}
            />
            <Segmented
              size="small"
              options={[
                { label: '全部等级', value: 'ALL' },
                { label: '高风险', value: 'HIGH' },
                { label: '需关注', value: 'ATTENTION' },
              ]}
              value={levelFilter}
              onChange={(v) => setLevelFilter(String(v))}
            />
          </Space>
        }
        styles={{ body: { padding: 0 } }}
      >
        {(list?.items.length ?? 0) === 0 ? (
          <div style={{ padding: 40 }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Text>{loading ? '加载中…' : '当前筛选下没有风险'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    风险只来自两条真实依据：消息命中策略闸门、AI 回复失败
                  </Text>
                </Space>
              }
            />
          </div>
        ) : (
          <Table<RiskEventItem>
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={list?.items ?? []}
            pagination={false}
            columns={[
              {
                title: '等级',
                dataIndex: 'level',
                width: 96,
                render: (v: string) => <Tag color={LEVEL_COLORS[v] ?? 'default'}>{RISK_LEVEL_LABELS[v] ?? v}</Tag>,
              },
              {
                title: '类型',
                dataIndex: 'event_type',
                width: 140,
                render: (v: string) => RISK_TYPE_LABELS[v] ?? v,
              },
              {
                title: '客户',
                dataIndex: 'customer_name',
                width: 140,
                render: (_: string, r) => (
                  <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(`/customers/${r.customer_id}`)}>
                    {r.customer_name}
                  </Button>
                ),
              },
              {
                title: '原因',
                dataIndex: 'reason',
                ellipsis: true,
                render: (v: string, r) => (
                  <Tooltip title={r.trigger_text ?? ''}>
                    <span>{v ?? '—'}</span>
                  </Tooltip>
                ),
              },
              {
                title: '证据',
                dataIndex: 'evidence_ref',
                width: 180,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v}
                  </Text>
                ),
              },
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 150,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v.replace('T', ' ').slice(0, 16)}
                  </Text>
                ),
              },
              {
                title: '状态',
                dataIndex: 'status',
                width: 90,
                render: (v: string) => <Tag color={STATUS_COLORS[v] ?? 'default'}>{RISK_STATUS_LABELS[v] ?? v}</Tag>,
              },
              {
                title: '操作',
                key: 'action',
                width: 150,
                render: (_: unknown, r) =>
                  r.status === 'OPEN' ? (
                    <Space size={4}>
                      <Button size="small" type="link" onClick={() => void resolve(r.id, 'RESOLVED')}>
                        已处理
                      </Button>
                      <Button size="small" type="link" onClick={() => void resolve(r.id, 'DISMISSED')}>
                        忽略
                      </Button>
                    </Space>
                  ) : (
                    <Tooltip title={r.resolved_note ?? ''}>
                      <Badge status="success" text={<Text type="secondary" style={{ fontSize: 12 }}>已处置</Text>} />
                    </Tooltip>
                  ),
              },
            ]}
          />
        )}
      </Card>
    </Space>
  )
}
