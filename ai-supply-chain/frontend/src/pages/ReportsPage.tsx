import {
  BulbOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  message,
  Modal,
  Radio,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { Column } from '@ant-design/charts'
import dayjs, { type Dayjs } from 'dayjs'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiPost } from '../api/client'
import type {
  ReportDetail,
  ReportDrilldownResponse,
  ReportDrilldownRow,
  ReportListResponse,
  ReportMetricCatalogResponse,
  ReportSummary,
  ReportType,
} from '../api/types'
import { REPORT_METRIC_LABELS, REPORT_TYPE_LABELS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

/** 要点语气 → 颜色/标签（TASK-039） */
const TONE_COLORS: Record<string, string> = {
  warning: 'red',
  attention: 'gold',
  good: 'green',
  info: 'blue',
}
const TONE_LABELS: Record<string, string> = {
  warning: '要处理',
  attention: '需关注',
  good: '向好',
  info: '进展',
}

/**
 * 把 trend 的宽表转成图表要的长表（一条序列一行）。
 * ★ 只做形状转换，不改任何数字 —— 图上与指标卡必须是同一批值。
 */
function trendChartData(trend: { day: string; customers: number; messages: number }[]) {
  return trend.flatMap((p) => [
    { day: p.day.slice(5), kind: '新增客户', count: p.customers },
    { day: p.day.slice(5), kind: '沟通消息', count: p.messages },
  ])
}

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) => (v ? String(v).replace('T', ' ').slice(0, 19) : '—')

/** 下钻行的实体类型展示名与配色（TASK-023 起下钻跨实体） */
const ROW_KIND_LABELS: Record<string, string> = {
  customer: '客户',
  message: '沟通',
  opportunity: '商机',
  project: '项目',
  risk: '风险',
  content: '内容',
}
const ROW_KIND_COLORS: Record<string, string> = {
  customer: 'blue',
  message: 'cyan',
  opportunity: 'orange',
  project: 'geekblue',
  risk: 'red',
  content: 'purple',
}

export default function ReportsPage() {
  const navigate = useNavigate()
  const [list, setList] = useState<ReportListResponse | null>(null)
  const [catalog, setCatalog] = useState<ReportMetricCatalogResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // 详情抽屉
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  // 下钻
  const [drill, setDrill] = useState<ReportDrilldownResponse | null>(null)
  const [drillOpen, setDrillOpen] = useState(false)
  const [drillLoading, setDrillLoading] = useState(false)
  const [drillMetric, setDrillMetric] = useState<string>('')

  // 生成
  const [genOpen, setGenOpen] = useState(false)
  const [genLoading, setGenLoading] = useState(false)
  const [genType, setGenType] = useState<ReportType>('DAILY')
  const [genRange, setGenRange] = useState<[Dayjs, Dayjs]>(() => {
    const today = dayjs()
    return [today, today]
  })

  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([
        apiGet<ReportListResponse>('/api/reports?page=1&page_size=50'),
        // ★ 指标目录来自后端：后端加指标前端自动出现，不用改前端
        apiGet<ReportMetricCatalogResponse>('/api/reports/metric-catalog'),
      ])
      setList(d)
      setCatalog(c)
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

  const openDetail = async (id: number) => {
    setDetailOpen(true)
    setDetailLoading(true)
    setDetail(null)
    try {
      setDetail(await apiGet<ReportDetail>(`/api/reports/${id}`))
    } catch (err) {
      message.error((err as { message?: string }).message ?? '读取报告失败')
      setDetailOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }

  const openDrill = async (metric: string, r: ReportSummary | ReportDetail) => {
    setDrillOpen(true)
    setDrillLoading(true)
    setDrill(null)
    setDrillMetric(metric)
    try {
      const qs = new URLSearchParams({
        metric,
        period_start: r.period_start,
        period_end: r.period_end,
        page: '1',
        page_size: '50',
      })
      setDrill(await apiGet<ReportDrilldownResponse>(`/api/reports/drilldown?${qs.toString()}`))
    } catch (err) {
      message.error((err as { message?: string }).message ?? '下钻失败')
      setDrillOpen(false)
    } finally {
      setDrillLoading(false)
    }
  }

  const generate = async () => {
    setGenLoading(true)
    try {
      const created = await apiPost<ReportDetail>('/api/reports/generate', {
        report_type: genType,
        period_start: genRange[0].format('YYYY-MM-DD'),
        period_end: genRange[1].format('YYYY-MM-DD'),
      })
      const vals = Object.values(created.metrics ?? {})
      const allNoData = vals.length > 0 && vals.every((v) => v.state !== 'VALID')
      message.success(
        allNoData
          ? `已生成报告 #${created.id}；本期没有 REAL 来源数据，指标为「暂无数据」（排除了 ${created.excluded_test_count} 条测试数据）`
          : `已生成报告 #${created.id}`,
      )
      setGenOpen(false)
      setReloadKey((k) => k + 1)
      void openDetail(created.id)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setGenLoading(false)
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            报告
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            报告是生成时的数据快照，不随客户数据变化而重算；每个指标都能点开下钻核对构成明细
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setGenOpen(true)}>
              生成报告
            </Button>
          </Space>
        </Col>
      </Row>

      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <FileTextOutlined />
            <span>历史报告</span>
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
                <Text>{loading ? '加载中…' : '还没有生成过报告'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  报告只统计 REAL 来源数据；测试数据会被排除并记入 excluded_test_count
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<ReportSummary>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            columns={[
              {
                title: '报告',
                dataIndex: 'id',
                width: 92,
                render: (v: number) => <Text strong>#{v}</Text>,
              },
              {
                title: '类型',
                dataIndex: 'report_type',
                width: 84,
                render: (v: string) => <Tag color="blue">{REPORT_TYPE_LABELS[v] ?? v}</Tag>,
              },
              {
                title: '统计期间',
                width: 200,
                render: (_: unknown, r) => (
                  <Text style={{ fontSize: 12.5 }}>
                    {r.period_start} ~ {r.period_end}
                  </Text>
                ),
              },
              {
                title: '生成时间',
                dataIndex: 'generated_at',
                width: 160,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '排除测试数据',
                dataIndex: 'excluded_test_count',
                width: 120,
                render: (v: number) =>
                  v > 0 ? (
                    <Tooltip title="这些 TEST 来源的客户不计入报告指标，避免测试数据被当成业务成绩">
                      <Tag color="gold">{v} 条</Tag>
                    </Tooltip>
                  ) : (
                    <Text type="secondary">0</Text>
                  ),
              },
              {
                title: '操作',
                key: 'action',
                width: 100,
                render: (_: unknown, r) => (
                  <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => void openDetail(r.id)}>
                    查看
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 生成报告 */}
      <Modal
        title="生成报告"
        open={genOpen}
        onCancel={() => setGenOpen(false)}
        onOk={() => void generate()}
        confirmLoading={genLoading}
        okText="生成快照"
      >
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item label="报告类型">
            <Radio.Group value={genType} onChange={(e) => setGenType(e.target.value as ReportType)}>
              <Radio.Button value="DAILY">日报</Radio.Button>
              <Radio.Button value="WEEKLY">周报</Radio.Button>
              <Radio.Button value="MONTHLY">月报</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="统计期间（含首尾）">
            <DatePicker.RangePicker
              style={{ width: '100%' }}
              value={genRange}
              onChange={(v) => {
                if (v && v[0] && v[1]) setGenRange([v[0], v[1]])
              }}
            />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="报告只统计 REAL 来源数据"
            description="TEST（测试导入）会被排除并记入 excluded_test_count；本期没有 REAL 数据时，指标是「暂无数据」而不是 0。"
          />
        </Form>
      </Modal>

      {/* 报告详情（含逐指标下钻） */}
      <Drawer
        title={detail ? `报告 #${detail.id}（${REPORT_TYPE_LABELS[detail.report_type] ?? detail.report_type}）` : '报告详情'}
        width={760}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        loading={detailLoading}
      >
        {detail && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="统计期间" span={2}>
                {detail.period_start} ~ {detail.period_end}
              </Descriptions.Item>
              <Descriptions.Item label="时区">{detail.timezone}</Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source_type}</Descriptions.Item>
              <Descriptions.Item label="生成时间" span={2}>
                {stamp(detail.generated_at)}
              </Descriptions.Item>
              <Descriptions.Item label="生成方">{detail.generated_by}</Descriptions.Item>
              <Descriptions.Item label="排除测试数据">{detail.excluded_test_count} 条</Descriptions.Item>
            </Descriptions>

            {/* ── 文字描述（TASK-039）──
                ★ 这段由后端按**真实数字**拼装，与下面的指标卡同一份来源，
                  所以不可能出现"文字说 3 个、卡片显示 2 个"的不一致。 */}
            {(detail.narrative?.length ?? 0) > 0 && (
              <Card
                size="small"
                variant="outlined"
                title={<Space size={6}><BulbOutlined /><span>本期要点</span></Space>}
              >
                <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                  {detail.narrative.map((n, i) => (
                    <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <Tag color={TONE_COLORS[n.tone] ?? 'default'} style={{ marginInlineEnd: 0 }}>
                        {TONE_LABELS[n.tone] ?? n.tone}
                      </Tag>
                      <Text style={{ fontSize: 13 }}>{n.text}</Text>
                    </div>
                  ))}
                </Space>
              </Card>
            )}

            {/* ── 走势图 ──
                ★ 数据来自报告快照的 trend（生成时冻结），不是前端另算 —— 
                  否则图与卡片会因统计时点不同而对不上（D6）。 */}
            {(detail.trend?.length ?? 0) > 0 && (
              <Card
                size="small"
                variant="outlined"
                title={
                  <Space size={6}>
                    <span>走势</span>
                    <Text type="secondary" style={{ fontWeight: 400, fontSize: 11 }}>
                      {detail.trend[0]?.aggregated_days
                        ? `按周聚合（每点 ${detail.trend[0].aggregated_days} 天）`
                        : '按天'}
                      ｜与指标卡同口径（只含真实来源数据）
                    </Text>
                  </Space>
                }
              >
                <Column
                  data={trendChartData(detail.trend)}
                  xField="day"
                  yField="count"
                  colorField="kind"
                  group
                  height={200}
                  legend={{ color: { position: 'bottom' } }}
                  axis={{ x: { labelAutoRotate: true } }}
                />
              </Card>
            )}

            {/* ── 无数据说明 ──
                ★ 这一块和"要点"同等重要：只列有数字的指标，
                  读者会把"取不到"当成 0（§二 禁止）。 */}
            {(detail.no_data?.length ?? 0) > 0 && (
              <Card
                size="small"
                variant="outlined"
                title={
                  <Space size={6}>
                    <ExclamationCircleOutlined style={{ color: '#F59E0B' }} />
                    <span>本期无数据的指标（{detail.no_data.length}）</span>
                    <Text type="secondary" style={{ fontWeight: 400, fontSize: 11 }}>
                      这些不是 0，是取不到
                    </Text>
                  </Space>
                }
              >
                <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                  {detail.no_data.map((t, i) => (
                    <Text key={i} type="secondary" style={{ fontSize: 12 }}>
                      · {t}
                    </Text>
                  ))}
                </Space>
              </Card>
            )}

            <div>
              <Space size={8} style={{ marginBottom: 10 }}>
                <Text strong>指标快照</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点指标可下钻看构成明细（下钻条数与这里的数字同口径）
                </Text>
              </Space>
              {/* ★ TASK-023：指标按 §十八 的汇报内容分类分组展示。
                  分组与顺序来自后端 metric-catalog，前端不硬编码。 */}
              {(catalog?.groups ?? []).map((group) => {
                const keys = Object.keys(detail.metrics).filter((k) => {
                  const def = catalog?.metrics.find((m) => m.key === k)
                  // 旧快照里的指标若不在目录中，归到"其它"，不隐藏
                  return def ? def.group === group : false
                })
                if (keys.length === 0) return null
                return (
                  <div key={group} style={{ marginBottom: 14 }}>
                    <Tag color="blue" style={{ marginBottom: 8 }}>
                      {group}
                    </Tag>
                    <Row gutter={[16, 16]}>
                      {keys.map((k) => (
                        <Col key={k} xs={24} md={12}>
                          <Card
                            variant="outlined"
                            size="small"
                            hoverable
                            onClick={() => void openDrill(k, detail)}
                            styles={{ body: { padding: '14px 16px' } }}
                          >
                            <Space size={6}>
                              <Text type="secondary" style={{ fontSize: 12.5 }}>
                                {catalog?.metrics.find((m) => m.key === k)?.label ??
                                  REPORT_METRIC_LABELS[k] ??
                                  k}
                              </Text>
                              <DownloadOutlined style={{ color: '#2563EB', fontSize: 11 }} />
                            </Space>
                            <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                              <EvidenceNumber evidence={detail.metrics[k]} />
                            </div>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              {detail.metrics[k].evidence_ref ?? ''}
                            </Text>
                          </Card>
                        </Col>
                      ))}
                    </Row>
                  </div>
                )
              })}
              {/* 目录里没有的指标（旧快照）不隐藏，单独兜底列出 */}
              {(() => {
                const known = new Set((catalog?.metrics ?? []).map((m) => m.key))
                const orphans = Object.keys(detail.metrics).filter((k) => !known.has(k))
                if (orphans.length === 0) return null
                return (
                  <div>
                    <Tag style={{ marginBottom: 8 }}>其它</Tag>
                    <Row gutter={[16, 16]}>
                      {orphans.map((k) => (
                        <Col key={k} xs={24} md={12}>
                          <Card
                            variant="outlined"
                            size="small"
                            hoverable
                            onClick={() => void openDrill(k, detail)}
                            styles={{ body: { padding: '14px 16px' } }}
                          >
                            <Text type="secondary" style={{ fontSize: 12.5 }}>
                              {REPORT_METRIC_LABELS[k] ?? k}
                            </Text>
                            <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                              <EvidenceNumber evidence={detail.metrics[k]} />
                            </div>
                          </Card>
                        </Col>
                      ))}
                    </Row>
                  </div>
                )
              })()}
            </div>

            {detail.excluded_test_count > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`本期排除了 ${detail.excluded_test_count} 条测试数据`}
                description="这些客户的 source_type 是 TEST，不计入报告指标；否则测试数据会被当成业务成绩。"
              />
            )}
          </Space>
        )}
      </Drawer>

      {/* 下钻明细 */}
      <Drawer
        title={`下钻：${REPORT_METRIC_LABELS[drillMetric] ?? drillMetric}`}
        width={820}
        open={drillOpen}
        onClose={() => setDrillOpen(false)}
        loading={drillLoading}
      >
        {drill && (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type={drill.total.state === 'VALID' ? 'success' : 'info'}
              showIcon
              message={
                <Space size={8}>
                  <span>下钻总数：</span>
                  <EvidenceNumber evidence={drill.total} />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    （与报告里的数字同口径，必须相等）
                  </Text>
                </Space>
              }
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  证据：{drill.evidence_ref}｜期间 {drill.period_start} ~ {drill.period_end}
                </Text>
              }
            />
            {(drill.items.length ?? 0) === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">该期间内没有构成这个指标的客户</Text>}
              />
            ) : (
              <Table<ReportDrilldownRow>
                rowKey={(r) => `${r.kind}-${r.id}`}
                size="small"
                pagination={false}
                dataSource={drill.items}
                columns={[
                  {
                    title: '类型',
                    dataIndex: 'kind',
                    width: 96,
                    render: (v: string) => (
                      <Tag color={ROW_KIND_COLORS[v] ?? 'default'}>{ROW_KIND_LABELS[v] ?? v}</Tag>
                    ),
                  },
                  {
                    title: '明细',
                    dataIndex: 'title',
                    render: (v: string, r: ReportDrilldownRow) => {
                      // 能跳到详情页的就给链接，跳不了的只显示文字
                      const cid = r.extra?.customer_id as number | undefined
                      const href =
                        r.kind === 'customer'
                          ? `/customers/${r.id}`
                          : cid
                            ? `/customers/${cid}`
                            : null
                      return href ? (
                        <Button
                          type="link"
                          size="small"
                          style={{ padding: 0 }}
                          onClick={() => navigate(href)}
                        >
                          {v}
                        </Button>
                      ) : (
                        <Text>{v}</Text>
                      )
                    },
                  },
                  {
                    title: '说明',
                    dataIndex: 'subtitle',
                    ellipsis: true,
                    render: (v: string | null) => (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {v ?? '—'}
                      </Text>
                    ),
                  },
                  {
                    title: '时间',
                    dataIndex: 'occurred_at',
                    width: 150,
                    render: (v: string | null) => (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {stamp(v)}
                      </Text>
                    ),
                  },
                  {
                    title: '证据',
                    dataIndex: 'evidence_ref',
                    width: 190,
                    render: (v: string | null) => (
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {v ?? '—'}
                      </Text>
                    ),
                  },
                ]}
              />
            )}
          </Space>
        )}
      </Drawer>

      <Text type="secondary" style={{ fontSize: 12 }}>
        <ThunderboltOutlined /> 报告为生成时快照（D6），不随数据变化重算；因此历史报告可以复现、可下钻核对。
      </Text>
    </Space>
  )
}
