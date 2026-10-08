import {
  ApartmentOutlined,
  BarChartOutlined,
  CompassOutlined,
  EnvironmentOutlined,
  InfoCircleOutlined,
  PieChartOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Column, Pie } from '@ant-design/charts'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  List,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { CountSlice, ResearchResponse } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const CHART_COLORS = ['#2563EB', '#60A5FA', '#93C5FD', '#A7F3D0', '#FCD34D', '#FCA5A5', '#C4B5FD', '#CBD5E1']

/** 占比显示：后端只在样本足够时给 share；为 null 时显示「—」而不是自己算 */
function shareText(s: CountSlice): string {
  return s.share === null || s.share === undefined ? '—' : `${(s.share * 100).toFixed(1)}%`
}

/** 一个分布块：标题 + 计数/占比表 + 空态 */
function DistributionCard({
  title,
  icon,
  items,
  emptyText,
  color,
}: {
  title: string
  icon: React.ReactNode
  items: CountSlice[]
  emptyText: string
  color: string
}) {
  return (
    <Card
      variant="outlined"
      title={
        <Space size={8}>
          <span style={{ color }}>{icon}</span>
          <span>{title}</span>
        </Space>
      }
      style={{ height: '100%' }}
      styles={{ body: { padding: items.length ? '8px 0' : 24 } }}
    >
      {items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">{emptyText}</Text>} />
      ) : (
        <List
          size="small"
          dataSource={items}
          renderItem={(s, idx) => (
            <List.Item style={{ padding: '10px 20px' }}>
              <Space size={10} style={{ minWidth: 0 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: CHART_COLORS[idx % CHART_COLORS.length],
                    display: 'inline-block',
                    flex: '0 0 auto',
                  }}
                />
                <Text style={{ fontSize: 13 }} ellipsis>
                  {s.label}
                </Text>
              </Space>
              <Space size={12}>
                <Text strong>{s.count}</Text>
                <Tooltip title={s.share === null ? '样本量不足，不给占比（避免把小样本当成市场结论）' : ''}>
                  <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                    {shareText(s)}
                  </Text>
                </Tooltip>
              </Space>
            </List.Item>
          )}
        />
      )}
    </Card>
  )
}

export default function ResearchPage() {
  const [data, setData] = useState<ResearchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const d = await apiGet<ResearchResponse>('/api/research/overview')
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

  const regionChart = (data?.region_distribution ?? []).slice(0, 8).map((s) => ({
    region: s.label,
    count: s.count,
  }))
  const lifecycleChart = (data?.lifecycle_distribution ?? []).map((s) => ({
    stage: s.label,
    count: s.count,
  }))

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      {/* ① 页面顶部：这个页面能做什么、不能做什么（如实声明边界） */}
      <Card variant="outlined" styles={{ body: { padding: '16px 20px' } }}>
        <Row justify="space-between" align="top" gutter={16}>
          <Col flex="auto">
            <Title level={5} style={{ margin: 0, marginBottom: 6 }}>
              市场研究
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              {data?.boundary.reason ?? '加载中…'}
            </Text>
            <div style={{ marginTop: 10 }}>
              <Space size={8} wrap>
                {(data?.boundary.unavailable ?? []).map((u) => (
                  <Tag key={u} color="default" style={{ marginInlineEnd: 0 }}>
                    答不了：{u}
                  </Tag>
                ))}
              </Space>
            </div>
          </Col>
          <Col flex="0 0 auto">
            <Space>
              <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
                刷新
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* ② 样本量：先告诉用户结论建立在多大样本上 */}
      <Card variant="outlined" styles={{ body: { padding: '16px 20px' } }}>
        <Row align="middle" gutter={24}>
          <Col>
            <Text type="secondary" style={{ fontSize: 13 }}>
              统计样本
            </Text>
            <div style={{ fontSize: 26, fontWeight: 600 }}>
              {data?.sample_size ? <EvidenceNumber evidence={data.sample_size} /> : <Text type="secondary">—</Text>}
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              个客户（占全部客户）
            </Text>
          </Col>
          <Col flex="auto">
            {data && !data.sample_sufficient ? (
              <Alert
                type="warning"
                showIcon
                message={`样本量少于 ${data.min_sample} 个，因此不给出占比结论`}
                description="只有计数是可信的；小样本算出的百分比容易被误当成市场结论，所以后端直接返回 null，界面显示「—」。"
              />
            ) : (
              <Alert
                type="info"
                showIcon
                message={data?.note ?? ''}
                description="所有数字都来自库内真实数据的分组计数；行业词频是字面统计，不是 AI 归类。"
              />
            )}
          </Col>
        </Row>
      </Card>

      {/* ③ 构成分布 */}
      <Row gutter={16}>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="地区分布"
            icon={<EnvironmentOutlined />}
            items={data?.region_distribution ?? []}
            emptyText="暂无地区数据"
            color="#2563EB"
          />
        </Col>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="获客渠道"
            icon={<CompassOutlined />}
            items={data?.channel_distribution ?? []}
            emptyText="暂无渠道数据"
            color="#16A34A"
          />
        </Col>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="客户阶段分布"
            icon={<BarChartOutlined />}
            items={data?.lifecycle_distribution ?? []}
            emptyText="暂无阶段数据"
            color="#7C3AED"
          />
        </Col>
      </Row>

      {/* ④ 图表：地区 + 阶段 */}
      <Row gutter={16}>
        <Col xs={24} lg={14}>
          <Card variant="outlined" title="客户地区分布" style={{ height: '100%' }} styles={{ body: { padding: 16 } }}>
            {regionChart.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <Column
                data={regionChart}
                xField="region"
                yField="count"
                height={220}
                style={{ fill: '#2563EB', radiusTopLeft: 4, radiusTopRight: 4 }}
                axis={{ y: { title: false }, x: { title: false } }}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card variant="outlined" title="客户阶段构成" style={{ height: '100%' }} styles={{ body: { padding: 16 } }}>
            {lifecycleChart.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <Pie
                data={lifecycleChart}
                angleField="count"
                colorField="stage"
                innerRadius={0.6}
                height={220}
                legend={{ color: { position: 'right', rowPadding: 6 } }}
                scale={{ color: { range: CHART_COLORS } }}
                label={false}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* ⑤ 活跃度 + 行业词 + 需求信号 */}
      <Row gutter={16}>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="各地区事件活跃度"
            icon={<ThunderboltOutlined />}
            items={data?.activity_by_region ?? []}
            emptyText="暂无事件"
            color="#F59E0B"
          />
        </Col>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="公司名字面词频（≥2 次）"
            icon={<ApartmentOutlined />}
            items={data?.industry_keywords ?? []}
            emptyText="没有出现 ≥2 次的词（样本太散时这是正常的）"
            color="#0EA5E9"
          />
        </Col>
        <Col xs={24} lg={8}>
          <DistributionCard
            title="需求信号（命中策略闸门的类别）"
            icon={<PieChartOutlined />}
            items={data?.demand_signals ?? []}
            emptyText="暂无敏感咨询记录"
            color="#DC2626"
          />
        </Col>
      </Row>

      {/* ⑥ 沟通记录类型分布（不含正文，避免把客户原话摊在研究页） */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <span>沟通记录类型分布</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              共 {data?.message_total.state === 'VALID' ? data.message_total.value : '—'} 条
            </Text>
          </Space>
        }
        extra={
          <Tooltip title="只统计类型与条数，不展示消息正文——客户原话属于沟通记录页的上下文">
            <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
              <InfoCircleOutlined /> 不含正文
            </Text>
          </Tooltip>
        }
        styles={{ body: { padding: (data?.message_mix.length ?? 0) ? 0 : 24 } }}
      >
        {(data?.message_mix.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text type="secondary">还没有沟通记录</Text>}
          />
        ) : (
          <Table<CountSlice>
            rowKey="key"
            size="small"
            loading={loading}
            pagination={false}
            dataSource={data?.message_mix ?? []}
            columns={[
              { title: '类型', dataIndex: 'label' },
              { title: '条数', dataIndex: 'count', width: 100 },
              {
                title: '占比',
                width: 100,
                render: (_: unknown, r: CountSlice) => (
                  <Text type="secondary">{shareText(r)}</Text>
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
