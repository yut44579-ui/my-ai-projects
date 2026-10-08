import {
  AlertOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  FilterOutlined,
  LineChartOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Column, DualAxes, Funnel } from '@ant-design/charts'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { AnalyticsResponse, FunnelStage, QualityIssue, SourceQuality } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const SEVERITY_COLORS: Record<string, string> = {
  HIGH: 'red',
  MEDIUM: 'orange',
  LOW: 'default',
}

const SEVERITY_LABELS: Record<string, string> = {
  HIGH: '阻塞跟进',
  MEDIUM: '影响判断',
  LOW: '仅影响统计',
}

/** 比率显示：null → 「—」（★ 后端在分母为 0 / 样本不足时给 null，前端绝不自己算） */
function ratioText(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const d = await apiGet<AnalyticsResponse>('/api/analytics/overview')
      setData(d)
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

  const trendData = (data?.trend ?? []).map((p) => ({ ...p, day: p.day.slice(5) }))
  const funnelData = (data?.funnel ?? [])
    .filter((f) => f.reached > 0)
    .map((f) => ({ stage: f.label, count: f.reached }))

  const cards = [
    { title: '客户总数', icon: <TeamOutlined />, color: '#2563EB', evidence: data?.total },
    { title: '本周新增', icon: <RiseOutlined />, color: '#16A34A', evidence: data?.new_this_week },
    {
      title: '有事件的客户',
      icon: <LineChartOutlined />,
      color: '#7C3AED',
      evidence: data?.active_customers,
    },
    {
      title: '数据无问题',
      icon: <CheckCircleOutlined />,
      color: '#0EA5E9',
      evidence: data?.quality_ok_total,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            数据分析
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            趋势 / 漏斗 / 数据质量 —— 与「市场研究」互补：那里看客户构成，这里看客户怎么流动
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
            刷新
          </Button>
        </Col>
      </Row>

      {/* 汇总卡 */}
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
              <div style={{ fontSize: 26, fontWeight: 600 }}>
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 趋势 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <BarChartOutlined />
            <span>近 {data?.trend_days ?? 14} 天趋势</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            按 Asia/Shanghai 归日 · 含 0 值以便看断档
          </Text>
        }
        styles={{ body: { padding: 16 } }}
      >
        {trendData.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
        ) : (
          <>
            <Column
              data={trendData}
              xField="day"
              yField="new_customers"
              height={180}
              style={{ fill: '#2563EB', radiusTopLeft: 4, radiusTopRight: 4 }}
              axis={{ y: { title: false }, x: { title: false } }}
            />
            <div style={{ height: 12 }} />
            <DualAxes
              data={trendData}
              xField="day"
              height={160}
              legend={{ color: { position: 'top' } }}
              children={[
                {
                  type: 'line',
                  yField: 'events',
                  shapeField: 'smooth',
                  style: { stroke: '#F59E0B', lineWidth: 2 },
                  axis: { y: { title: '事件' } },
                },
                {
                  type: 'line',
                  yField: 'visits',
                  shapeField: 'smooth',
                  style: { stroke: '#16A34A', lineWidth: 2, lineDash: [4, 3] },
                  axis: { y: { title: '访问' } },
                },
              ]}
            />
          </>
        )}
      </Card>

      {/* 漏斗 + 来源质量 */}
      <Row gutter={16}>
        <Col xs={24} lg={14}>
          <Card
            variant="outlined"
            title={
              <Space size={8}>
                <FilterOutlined />
                <span>客户生命周期漏斗</span>
              </Space>
            }
            extra={
              <Tooltip title="用累计口径：达到或超过该阶段的客户数。若按当前状态直接计数，会出现「越往后越大」的错结论">
                <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                  累计口径
                </Text>
              </Tooltip>
            }
            style={{ height: '100%' }}
            styles={{ body: { padding: 16 } }}
          >
            {funnelData.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <Funnel
                data={funnelData}
                xField="stage"
                yField="count"
                height={200}
                style={{ fill: '#2563EB', stroke: '#fff' }}
                legend={false}
                label={{ text: (d: { count: number }) => `${d.count}` }}
              />
            )}
            <div style={{ marginTop: 12 }}>
              <Table<FunnelStage>
                rowKey="status"
                size="small"
                loading={loading}
                pagination={false}
                dataSource={data?.funnel ?? []}
                columns={[
                  { title: '阶段', dataIndex: 'label', width: 100 },
                  { title: '累计到达', dataIndex: 'reached', width: 90 },
                  {
                    title: '相对上一级',
                    width: 110,
                    render: (_: unknown, r: FunnelStage) => (
                      <Tooltip title={r.conversion_from_prev === null ? '分母为 0 或样本不足，无法计算（不是 0%）' : ''}>
                        <Text type={r.conversion_from_prev === null ? 'secondary' : undefined} style={{ cursor: 'help' }}>
                          {ratioText(r.conversion_from_prev)}
                        </Text>
                      </Tooltip>
                    ),
                  },
                  {
                    title: '占总数',
                    width: 90,
                    render: (_: unknown, r: FunnelStage) => (
                      <Text type="secondary">{ratioText(r.conversion_from_total)}</Text>
                    ),
                  },
                ]}
              />
            </div>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            variant="outlined"
            title="客户来源质量"
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                测试数据不计入业务汇报
              </Text>
            }
            style={{ height: '100%' }}
            styles={{ body: { padding: (data?.source_quality.length ?? 0) ? 16 : 24 } }}
          >
            {(data?.source_quality.length ?? 0) === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <Space orientation="vertical" size={14} style={{ width: '100%' }}>
                {(data?.source_quality ?? []).map((s: SourceQuality) => (
                  <div key={s.source_type}>
                    <Row justify="space-between">
                      <Text style={{ fontSize: 13 }}>{s.label}</Text>
                      <Space size={8}>
                        <Text strong>{s.count}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {ratioText(s.share)}
                        </Text>
                      </Space>
                    </Row>
                    <Progress
                      percent={s.share === null ? 0 : Math.round(s.share * 100)}
                      showInfo={false}
                      strokeColor={s.source_type === 'TEST' ? '#F59E0B' : '#2563EB'}
                      size="small"
                    />
                  </div>
                ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      {/* 数据质量 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <AlertOutlined />
            <span>数据质量</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {data?.quality.length ? `${data.quality.length} 类问题` : '没有发现问题'}
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.quality.length ?? 0) ? 0 : 24 } }}
      >
        {(data?.quality.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>当前没有数据质量问题</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  电话 / 邮箱 / 公司名 / 地区都不缺，也没有待裁决的重复记录
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<QualityIssue>
            rowKey="key"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={data?.quality ?? []}
            columns={[
              {
                title: '问题',
                dataIndex: 'label',
                width: 140,
                render: (v: string) => <Text strong>{v}</Text>,
              },
              {
                title: '影响客户数',
                dataIndex: 'count',
                width: 110,
                render: (v: number) => <Text>{v}</Text>,
              },
              {
                title: '严重度',
                dataIndex: 'severity',
                width: 120,
                render: (v: string) => (
                  <Tag color={SEVERITY_COLORS[v] ?? 'default'}>{SEVERITY_LABELS[v] ?? v}</Tag>
                ),
              },
              { title: '处理建议', dataIndex: 'hint' },
              {
                title: '证据',
                dataIndex: 'evidence_ref',
                width: 240,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v}
                  </Text>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Text type="secondary" style={{ fontSize: 12 }}>
        {data?.note ?? ''}
      </Text>
    </Space>
  )
}
