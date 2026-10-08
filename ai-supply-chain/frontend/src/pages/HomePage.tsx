import {
  ArrowRightOutlined,
  BarChartOutlined,
  CheckCircleFilled,
  CloudServerOutlined,
  CloseOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ImportOutlined,
  LoadingOutlined,
  PlusOutlined,
  RobotOutlined,
  StopOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Line, Pie } from '@ant-design/charts'
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  List,
  message,
  Radio,
  Row,
  Segmented,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type {
  CustomerStatsResponse,
  DashboardSummaryResponse,
  TaskItem,
} from '../api/types'
import { LIFECYCLE_LABELS, TASK_REASON_LABELS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'
import AiAssistant from '../components/AiAssistant'
import ImportModal from '../components/ImportModal'
import SourceTag from '../components/SourceTag'
import { displayName } from '../utils/currentUser'

const { Text, Title } = Typography

/** 环形图柔和配色（不用霓虹色 / 大面积渐变） */
const DONUT_COLORS = ['#2563EB', '#60A5FA', '#93C5FD', '#A7F3D0', '#FCD34D', '#FCA5A5', '#C4B5FD', '#CBD5E1']

/** 任务标签配色：风险类必须一眼看出是红的 */
const REASON_COLORS: Record<string, string> = {
  HUMAN_REQUIRED: 'red',
  HUMAN_ACTIVE: 'volcano',
  AI_FAILED: 'orange',
  PENDING_REVIEW: 'gold',
  NEW: 'blue',
}

const TASK_FILTERS = [
  { label: '全部', value: 'ALL' },
  { label: '待跟进', value: 'NEW' },
  { label: '需人工', value: 'HUMAN' },
  { label: '高风险', value: 'RISK' },
]

const ACTIVITY_FILTERS = ['全部', '客户访问', '沟通记录', '商机动态', '系统提醒']

/** 后端还没返回时的占位 EvidenceValue：显示「—」，绝不显示 0 */
const NO_EVIDENCE = {
  value: null,
  state: 'NO_DATA' as const,
  source_type: 'SYSTEM',
  evidence_ref: null,
  reason: '等待后端返回',
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffMin = Math.floor((Date.now() - then) / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const h = Math.floor(diffMin / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

export default function HomePage() {
  const navigate = useNavigate()
  const [data, setData] = useState<DashboardSummaryResponse | null>(null)
  const [stats, setStats] = useState<CustomerStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [taskFilter, setTaskFilter] = useState('ALL')
  const [activityFilter, setActivityFilter] = useState('全部')

  // ★ AI 助手的问答状态已随组件抽走（TASK-036）——
  //   这一页不再自己管 aiQuestion/aiResult/aiLoading/aiError/askAi，
  //   那些都归 components/AiAssistant.tsx。留在这里就是死代码。

  /**
   * 「生成报告」快捷入口：**当场生成一份今日汇报**，再跳到报告页看结果。
   *
   * ★ 为什么不是直接跳报告页：这个按钮的字面意思是"生成"。
   *   跳过去让用户自己再点一次「生成报告」，等于按钮没干活。
   *   （§二十六：不做假功能 —— 按钮文案说什么，就得真做什么。）
   */
  const [generating, setGenerating] = useState(false)
  const generateReportNow = useCallback(async () => {
    if (generating) return
    setGenerating(true)
    try {
      const today = new Date()
      const iso = (d: Date) => d.toISOString().slice(0, 10)
      await apiPost('/api/reports/generate', {
        report_type: 'DAILY',
        period_start: iso(today),
        period_end: iso(today),
      })
      message.success('今日汇报已生成')
      navigate('/reports')
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setGenerating(false)
    }
  }, [generating, navigate])

  const load = useCallback(async () => {    try {
      const [summary, statsData] = await Promise.all([
        apiGet<DashboardSummaryResponse>('/api/dashboard/summary'),
        apiGet<CustomerStatsResponse>('/api/customers/stats'),
      ])
      setData(summary)
      setStats(statsData)
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

  // ── 今日重点任务：按筛选标签过滤（标签条数来自后端 counts，真实） ──
  const filteredTasks = useMemo(() => {
    const items = data?.tasks.items ?? []
    if (taskFilter === 'ALL') return items
    if (taskFilter === 'HUMAN') {
      return items.filter((t) => t.reason === 'HUMAN_REQUIRED' || t.reason === 'HUMAN_ACTIVE')
    }
    if (taskFilter === 'RISK') return items.filter((t) => t.reason === 'HUMAN_REQUIRED')
    return items.filter((t) => t.reason === taskFilter)
  }, [data, taskFilter])

  const donutData = (data?.region_distribution.slices ?? []).map((s) => ({
    region: s.region,
    count: s.count,
  }))
  const lineData = (data?.growth_trend.points ?? []).map((p) => ({
    day: p.day.slice(5),
    count: p.count,
  }))

  const kpiCards = [
    {
      title: '客户总数',
      icon: <TeamOutlined />,
      color: '#2563EB',
      evidence: data?.kpis.customer_total,
      hint: '当前库内全部客户',
    },
    {
      title: '新增客户',
      icon: <PlusOutlined />,
      color: '#16A34A',
      evidence: data?.kpis.customer_new_this_week,
      hint: '本周一 00:00 起首次出现',
    },
    {
      title: '商机数量',
      icon: <BarChartOutlined />,
      color: '#F59E0B',
      evidence: data?.kpis.opportunity_count,
      // ★ 口径由后端给（读商机表 / 生命周期近似），前端不重新定义
      hint:
        data?.kpis.opportunity_source === 'OPPORTUNITY_TABLE'
          ? '进行中的商机（不含赢单/丢单）'
          : '商机表暂无记录，暂用客户生命周期近似',
    },
    {
      title: '成交订单',
      icon: <CheckCircleFilled />,
      color: '#7C3AED',
      evidence: data?.kpis.won_orders,
      hint:
        data?.kpis.opportunity_source === 'OPPORTUNITY_TABLE'
          ? '赢单（WON）的商机数'
          : '生命周期为「已成交」的客户',
    },
    {
      title: '风险客户',
      icon: <StopOutlined />,
      color: '#DC2626',
      evidence: data?.kpis.risk_customers,
      hint: '策略闸门判定需人工处理',
    },
  ]

  const pending = data?.pending

  return (
    <>
      {error && (
        <Alert
          type="error"
          showIcon
          message="工作台数据加载失败"
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* ══ 三栏：中央工作区（约 62%） + 右侧辅助区（约 24%） ══ */}
      <Row gutter={20} align="top">
        {/* ─────────── 中央工作区 ─────────── */}
        <Col xs={24} xl={17}>
          {/* 欢迎区 + 今日待处理 */}
          <Card variant="outlined" styles={{ body: { padding: '20px 24px' } }} style={{ marginBottom: 20 }}>
            <Row align="middle" justify="space-between" gutter={16}>
              <Col flex="auto">
                <Title level={3} style={{ margin: 0, fontWeight: 600 }}>
                  {data?.greeting.salutation ?? '你好'}，{displayName()}
                </Title>
                <Text type="secondary" style={{ fontSize: 14 }}>
                  {data?.greeting.subtitle ?? '这是你今天的商业项目助理工作台。'}
                </Text>
              </Col>
              <Col flex="0 0 auto">
                <div
                  style={{
                    background: '#F7F9FC',
                    border: '1px solid #EEF0F4',
                    borderRadius: 10,
                    padding: '12px 18px',
                    minWidth: 300,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      今日待处理
                    </Text>
                    <span style={{ fontSize: 22, fontWeight: 600 }}>
                      <EvidenceNumber evidence={pending?.total ?? NO_EVIDENCE} />
                    </span>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      项
                    </Text>
                  </div>
                  <Space size={16} wrap>
                    <Text style={{ fontSize: 12 }}>
                      <Badge color="#DC2626" /> 高风险{' '}
                      <EvidenceNumber evidence={pending?.human_required ?? NO_EVIDENCE} />
                    </Text>
                    <Text style={{ fontSize: 12 }}>
                      <Badge color="#F59E0B" /> 需人工处理{' '}
                      <EvidenceNumber evidence={pending?.human_active ?? NO_EVIDENCE} />
                    </Text>
                    <Text style={{ fontSize: 12 }}>
                      <Badge color="#8B5CF6" /> AI 回复失败{' '}
                      <EvidenceNumber evidence={pending?.ai_failed ?? NO_EVIDENCE} />
                    </Text>
                  </Space>
                </div>
              </Col>
            </Row>
          </Card>

          {/* KPI：5 张一排（xs 1 / sm 2 / md 3 / xl 5 —— AntD 栅格是 24 等分，用 flex 实现 5 等分） */}
          <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
            {kpiCards.map((card) => (
              // ★ TASK-043 移动端修正：原来这里有个**无条件**的
              //   `maxWidth: '20%'`。它在手机上照样生效，把 5 张卡各压成 20%
              //   （75px），再叠加 Row 的 -8px 负边距 → 右边界 383px，横向溢出。
              //   xl 的 `flex: '0 0 20%'` 本身就能实现 5 等分，那个 maxWidth 是多余的。
              <Col key={card.title} xs={24} sm={12} md={8} xl={{ flex: '0 0 20%' }}>
                <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }} style={{ height: '100%' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                    <div
                      style={{
                        width: 30,
                        height: 30,
                        borderRadius: '50%',
                        background: `${card.color}14`,
                        color: card.color,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 15,
                        flex: '0 0 auto',
                      }}
                    >
                      {card.icon}
                    </div>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      {card.title}
                    </Text>
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 600, lineHeight: '36px' }}>
                    {card.evidence ? (
                      <EvidenceNumber evidence={card.evidence} />
                    ) : (
                      <Text type="secondary">—</Text>
                    )}
                  </div>
                  <Tooltip title={card.evidence?.reason ?? card.hint}>
                    <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                      {card.evidence && card.evidence.state !== 'VALID' ? '暂无数据' : card.hint}
                    </Text>
                  </Tooltip>
                </Card>
              </Col>
            ))}
          </Row>

          {/* ── 今日重点任务（业务任务列表，不是聊天窗口） ── */}
          <Card
            variant="outlined"
            style={{ marginBottom: 20 }}
            styles={{ body: { padding: 0 } }}
            title={
              // ★ TASK-043 移动端：原来是 `justify-content: space-between` 的单行 flex，
              //   手机上「全部/待跟进/需人工/高风险 + 查看全部」宽 317px，
              //   撑到右边界 560px → 整页横向溢出。
              //   改成可换行：窄屏时标题与筛选条自动分两行。
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                  flexWrap: 'wrap',
                }}
              >
                <Space size={10}>
                  <Text strong style={{ fontSize: 15 }}>
                    今日重点任务
                  </Text>
                  <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                    <RobotOutlined /> AI 智能排序
                  </Tag>
                </Space>
                <Space size={8} wrap>
                  <Radio.Group
                    size="small"
                    value={taskFilter}
                    onChange={(e) => setTaskFilter(e.target.value)}
                    optionType="button"
                    buttonStyle="solid"
                  >
                    {TASK_FILTERS.map((f) => (
                      <Radio.Button key={f.value} value={f.value}>
                        {f.label}
                      </Radio.Button>
                    ))}
                  </Radio.Group>
                  <Button type="link" size="small" onClick={() => navigate('/customers')}>
                    查看全部 <ArrowRightOutlined />
                  </Button>
                </Space>
              </div>
            }
          >
            {filteredTasks.length === 0 ? (
              <div style={{ padding: 40 }}>
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <Space orientation="vertical" size={4}>
                      <Text>{loading ? '加载中…' : '暂无需要处理的客户'}</Text>
                      <Text type="secondary">导入客户数据后，需要跟进的客户会出现在这里</Text>
                    </Space>
                  }
                >
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
                    导入 Excel/CSV
                  </Button>
                </Empty>
              </div>
            ) : (
              <List
                loading={loading}
                dataSource={filteredTasks}
                renderItem={(task: TaskItem) => (
                  <List.Item
                    style={{ padding: '16px 24px', cursor: 'pointer' }}
                    onClick={() => navigate(`/customers/${task.id}`)}
                    actions={[
                      <Button
                        key="go"
                        size="small"
                        type="link"
                        onClick={(e) => {
                          e.stopPropagation()
                          navigate(`/customers/${task.id}`)
                        }}
                      >
                        {task.reason === 'HUMAN_REQUIRED' ? '查看' : '去跟进'}
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      avatar={
                        <Avatar
                          size={38}
                          style={{ background: `${REASON_COLORS[task.reason] === 'red' ? '#DC2626' : '#2563EB'}18`, color: REASON_COLORS[task.reason] === 'red' ? '#DC2626' : '#2563EB' }}
                          icon={<TeamOutlined />}
                        />
                      }
                      title={
                        <Space size={8} wrap>
                          <Text strong style={{ fontSize: 14 }}>
                            {task.name}
                          </Text>
                          <SourceTag value={task.source_type} />
                          <Tag color={REASON_COLORS[task.reason] ?? 'default'}>
                            {TASK_REASON_LABELS[task.reason] ?? task.reason}
                          </Tag>
                          <Tag>{LIFECYCLE_LABELS[task.lifecycle_status] ?? task.lifecycle_status}</Tag>
                        </Space>
                      }
                      description={
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 13 }}>
                            {task.company_name ? `${task.company_name} · ` : ''}
                            {task.reason_detail}
                          </Text>
                          <div style={{ marginTop: 4 }}>
                            <Tooltip title={`证据：${task.evidence_ref}`}>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {timeAgo(task.occurred_at)}
                              </Text>
                            </Tooltip>
                          </div>
                        </div>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>

          {/* ── 客户分布 + 客户增长趋势 ── */}
          <Row gutter={20} style={{ marginBottom: 20 }}>
            <Col xs={24} lg={10}>
              <Card
                variant="outlined"
                title="客户分布"
                style={{ height: '100%' }}
                styles={{ body: { padding: 16 } }}
              >
                {donutData.length === 0 ? (
                  <div style={{ padding: '32px 0' }}>
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={<Text type="secondary">暂无客户地区数据</Text>}
                    />
                  </div>
                ) : (
                  <Pie
                    data={donutData}
                    angleField="count"
                    colorField="region"
                    innerRadius={0.62}
                    height={220}
                    legend={{ color: { position: 'right', rowPadding: 6 } }}
                    scale={{ color: { range: DONUT_COLORS } }}
                    label={false}
                    tooltip={{ title: 'region' }}
                    annotations={[
                      {
                        type: 'text',
                        style: {
                          text: `客户总数\n${data?.region_distribution.total.value ?? 0}`,
                          x: '50%',
                          y: '50%',
                          textAlign: 'center',
                          fontSize: 13,
                          fill: '#1F2937',
                        },
                      },
                    ]}
                  />
                )}
              </Card>
            </Col>

            <Col xs={24} lg={14}>
              <Card
                variant="outlined"
                title="客户增长趋势"
                extra={
                  <Space size={6}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      近 {data?.growth_trend.days ?? 7} 天
                    </Text>
                    <ArrowRightOutlined style={{ fontSize: 11, color: '#9CA3AF' }} />
                  </Space>
                }
                style={{ height: '100%' }}
                styles={{ body: { padding: 16 } }}
              >
                {lineData.length === 0 ? (
                  <div style={{ padding: '32px 0' }}>
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={<Text type="secondary">暂无增长数据</Text>}
                    />
                  </div>
                ) : (
                  <Line
                    data={lineData}
                    xField="day"
                    yField="count"
                    height={220}
                    shapeField="smooth"
                    style={{ lineWidth: 2, stroke: '#2563EB' }}
                    axis={{ y: { title: false }, x: { title: false } }}
                    point={{ size: 3, style: { fill: '#2563EB' } }}
                  />
                )}
              </Card>
            </Col>
          </Row>

          {/* ── 客户来源 + 数据连接状态 ── */}
          <Row gutter={20}>
            <Col xs={24} lg={10}>
              <Card variant="outlined" title="客户来源" style={{ height: '100%' }}>
                {(data?.source_breakdown.slices.length ?? 0) === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无来源数据</Text>} />
                ) : (
                  <List
                    size="small"
                    dataSource={data?.source_breakdown.slices ?? []}
                    renderItem={(s, idx) => (
                      <List.Item>
                        <Space size={10}>
                          <span
                            style={{
                              width: 26,
                              height: 26,
                              borderRadius: '50%',
                              background: `${DONUT_COLORS[idx % DONUT_COLORS.length]}22`,
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: 13,
                            }}
                          >
                            <DatabaseOutlined style={{ color: DONUT_COLORS[idx % DONUT_COLORS.length] }} />
                          </span>
                          <Text style={{ fontSize: 13 }}>{s.label}</Text>
                        </Space>
                        <Text strong>{s.count}</Text>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>

            <Col xs={24} lg={14}>
              <Card variant="outlined" title="数据连接状态" style={{ height: '100%' }}>
                <List
                  size="small"
                  dataSource={data?.data_connections.items ?? []}
                  renderItem={(conn) => (
                    <List.Item>
                      <Space size={10}>
                        {conn.connected ? (
                          <CheckCircleFilled style={{ color: '#16A34A' }} />
                        ) : conn.note === '同步中' ? (
                          <LoadingOutlined style={{ color: '#F59E0B' }} />
                        ) : (
                          <CloseOutlined style={{ color: '#D1D5DB' }} />
                        )}
                        <Text style={{ fontSize: 13 }}>{conn.label}</Text>
                      </Space>
                      <Space size={12}>
                        <Tag color={conn.connected ? 'green' : 'default'}>
                          {conn.connected ? '已连接' : '未连接'}
                        </Tag>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {conn.last_sync_at
                            ? conn.last_sync_at.replace('T', ' ').slice(5, 16)
                            : '—'}
                        </Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
          </Row>
        </Col>

        {/* ─────────── 右侧辅助区（AI 助手 / 快捷入口 / 今日汇报） ─────────── */}
        <Col xs={24} xl={7}>
          {/* AI 助手：★ 与「AI助手」独立页**共用同一个组件**（TASK-036），
              不复制两份 —— 否则改一处忘一处，两处能问的问题会不一致 */}
          <AiAssistant assistant={data?.ai_assistant} />

          {/* 快捷入口 2×2（★ TASK-036：全部接成真功能，不再有"待接入"占位） */}
          <Card variant="outlined" title="快捷入口" style={{ marginBottom: 20 }}>
            <Row gutter={[12, 12]}>
              {[
                {
                  key: 'connect',
                  icon: <CloudServerOutlined />,
                  label: '连接数据源',
                  hint: '配置并测试连接',
                  onClick: () => navigate('/connections'),
                },
                {
                  key: 'import',
                  icon: <ImportOutlined />,
                  label: '导入文件',
                  hint: 'Excel / CSV',
                  onClick: () => setImportOpen(true),
                },
                {
                  key: 'report',
                  icon: <FileTextOutlined />,
                  label: generating ? '生成中…' : '生成报告',
                  hint: '生成今日汇报',
                  onClick: () => void generateReportNow(),
                  disabled: generating,
                },
                {
                  key: 'assistant',
                  icon: <RobotOutlined />,
                  label: '问 AI 助手',
                  hint: '打开完整问答页',
                  onClick: () => navigate('/ai-assistant'),
                },
              ].map((a) => (
                <Col span={12} key={a.key}>
                  <button
                    type="button"
                    disabled={a.disabled}
                    onClick={a.onClick}
                    style={{
                      width: '100%',
                      height: 78,
                      border: '1px solid #EEF0F4',
                      borderRadius: 10,
                      background: '#FFFFFF',
                      cursor: a.disabled ? 'wait' : 'pointer',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 4,
                      color: '#2563EB',
                    }}
                  >
                    <span style={{ fontSize: 18 }}>{a.icon}</span>
                    <span style={{ fontSize: 12.5 }}>{a.label}</span>
                    <span style={{ fontSize: 10, color: '#9CA3AF' }}>{a.hint}</span>
                  </button>
                </Col>
              ))}
            </Row>
          </Card>

          {/* 今日汇报 */}
          <Card
            variant="outlined"
            title="今日汇报"
            extra={
              <Button type="link" size="small" disabled={!data?.today_report.available}>
                生成汇报
              </Button>
            }
            style={{ marginBottom: 20 }}
          >
            {!data?.today_report.available ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">{data?.today_report.note ?? '暂无汇报'}</Text>}
              />
            ) : (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  汇报摘要
                </Text>
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(data?.today_report.metrics ?? []).map((m) => (
                    <div key={m.key} style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <Text style={{ fontSize: 13 }}>{m.label}</Text>
                      <Text strong>
                        <EvidenceNumber evidence={m.evidence} noDataLabel="" />
                      </Text>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 14 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    重点事项
                  </Text>
                  <div style={{ marginTop: 6 }}>
                    {(data?.today_report.highlights.length ?? 0) === 0 ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        暂无新增重点事项
                      </Text>
                    ) : (
                      data?.today_report.highlights.map((h) => (
                        <div key={h} style={{ fontSize: 12, color: '#4B5563' }}>
                          · {h}
                        </div>
                      ))
                    )}
                  </div>
                </div>
                <div style={{ marginTop: 10 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {data?.today_report.note}
                  </Text>
                </div>
              </>
            )}
            <div style={{ marginTop: 12, textAlign: 'right' }}>
              <Button type="link" size="small" disabled>
                查看详细汇报 <ArrowRightOutlined />
              </Button>
            </div>
          </Card>
        </Col>
      </Row>

      {/* ── 中央底部：最近客户动态（跨整宽） ── */}
      <Card
        variant="outlined"
        style={{ marginTop: 20 }}
        styles={{ body: { padding: 0 } }}
        title={
          // ★ TASK-043 移动端：同一类问题 —— 单行 flex + 不换行，
          //   窄屏时「全部/客户访问/沟通记录/商机动态/系统提醒 + 查看全部」
          //   宽 430px 顶出屏幕（右边界 557px）。改成可换行。
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 10,
            }}
          >
            <Text strong style={{ fontSize: 15 }}>
              最近客户动态
            </Text>
            <Space size={10} wrap>
              <Segmented
                size="small"
                options={ACTIVITY_FILTERS}
                value={activityFilter}
                onChange={(v) => setActivityFilter(String(v))}
              />
              <Button type="link" size="small" onClick={() => navigate('/customers')}>
                查看全部 <ArrowRightOutlined />
              </Button>
            </Space>
          </div>
        }
      >
        {(data?.recent_activities.items.length ?? 0) === 0 ? (
          <div style={{ padding: 40 }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Text>暂无客户动态</Text>
                  <Text type="secondary">
                    状态变更 / 沟通记录 / 人工接管 都会记录在这里（只显示真实事件）
                  </Text>
                </Space>
              }
            />
          </div>
        ) : (
          <List
            dataSource={data?.recent_activities.items ?? []}
            renderItem={(act) => (
              <List.Item style={{ padding: '12px 24px' }}>
                <List.Item.Meta
                  avatar={
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background:
                          act.actor === 'AI' ? '#8B5CF6' : act.actor === 'HUMAN' ? '#2563EB' : '#9CA3AF',
                        display: 'inline-block',
                        marginTop: 8,
                      }}
                    />
                  }
                  title={
                    <Space size={8} wrap>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {act.occurred_at.replace('T', ' ').slice(5, 16)}
                      </Text>
                      <Text strong style={{ fontSize: 13 }}>
                        {act.customer_name}
                      </Text>
                      <Tag>{act.title}</Tag>
                    </Space>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 12.5 }}>
                      {act.detail ?? '—'}（发起方：{act.actor}）
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <div style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          客户指标口径：{stats?.scope ?? '全部'} · 所有数字均由后端计算并携带证据锚点，前端不做业务计算
        </Text>
      </div>

      <ImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => setReloadKey((k) => k + 1)}
      />
    </>
  )
}
