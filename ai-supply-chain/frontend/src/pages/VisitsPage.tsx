import {
  ArrowRightOutlined,
  EyeOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Line as LineChart } from '@ant-design/charts'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  List,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { VisitItem, VisitListResponse, VisitSummaryResponse } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text } = Typography

const CHANNEL_COLORS = ['#2563EB', '#60A5FA', '#A7F3D0', '#FCD34D', '#FCA5A5', '#C4B5FD', '#CBD5E1']

export default function VisitsPage() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<VisitSummaryResponse | null>(null)
  const [list, setList] = useState<VisitListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [channel, setChannel] = useState<string>('ALL')
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (channel !== 'ALL') params.set('channel', channel)
      const [s, l] = await Promise.all([
        apiGet<VisitSummaryResponse>('/api/visits/summary'),
        apiGet<VisitListResponse>(`/api/visits?${params.toString()}`),
      ])
      setSummary(s)
      setList(l)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [channel])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const dailyData = (summary?.daily ?? []).map((d) => ({ day: d.day.slice(5), count: d.count }))
  const channelOptions = [
    { label: '全部', value: 'ALL' },
    ...((summary?.by_channel ?? []).map((c) => ({ label: `${c.label} ${c.count}`, value: c.channel }))),
  ]

  const cards = [
    { title: '访问总次数', icon: <EyeOutlined />, color: '#2563EB', evidence: summary?.total },
    { title: '今日访问', icon: <RiseOutlined />, color: '#16A34A', evidence: summary?.today },
    {
      title: '有访问的客户',
      icon: <TeamOutlined />,
      color: '#7C3AED',
      evidence: summary?.visited_customers,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      {/* 汇总 */}
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
              <div style={{ fontSize: 26, fontWeight: 600 }}>
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 趋势 + 热门页面 + 渠道 */}
      <Row gutter={16}>
        <Col xs={24} lg={14}>
          <Card
            variant="outlined"
            title="访问趋势"
            extra={
              <Space size={6}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  近 7 天
                </Text>
              </Space>
            }
            style={{ height: '100%' }}
            styles={{ body: { padding: 16 } }}
          >
            {dailyData.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无访问数据</Text>} />
            ) : (
              <LineChart
                data={dailyData}
                xField="day"
                yField="count"
                height={200}
                shapeField="smooth"
                style={{ lineWidth: 2, stroke: '#2563EB' }}
                axis={{ y: { title: false }, x: { title: false } }}
                point={{ size: 3, style: { fill: '#2563EB' } }}
              />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            variant="outlined"
            title="客户最常看的页面"
            style={{ height: '100%' }}
            styles={{ body: { padding: 16 } }}
          >
            {(summary?.top_pages.length ?? 0) === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无数据</Text>} />
            ) : (
              <List
                size="small"
                dataSource={summary?.top_pages ?? []}
                renderItem={(p, idx) => (
                  <List.Item>
                    <Space size={10}>
                      <span
                        style={{
                          width: 26,
                          height: 26,
                          borderRadius: '50%',
                          background: `${CHANNEL_COLORS[idx % CHANNEL_COLORS.length]}22`,
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 13,
                        }}
                      >
                        <FileTextOutlined style={{ color: CHANNEL_COLORS[idx % CHANNEL_COLORS.length] }} />
                      </span>
                      <Text style={{ fontSize: 13 }}>{p.page}</Text>
                    </Space>
                    <Text strong>{p.count}</Text>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* 访问明细 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>访问明细</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 条` : '暂无'}
            </Text>
          </Space>
        }
        extra={
          <Space size={10}>
            <Segmented
              size="small"
              options={channelOptions.length > 1 ? channelOptions : [{ label: '全部', value: 'ALL' }]}
              value={channel}
              onChange={(v) => setChannel(String(v))}
            />
            <Button icon={<ReloadOutlined />} size="small" onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
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
                  <Text>{loading ? '加载中…' : '暂无访问记录'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    访问通过 POST /api/customers/&#123;id&#125;/visits 记录；只记能识别到客户的访问
                  </Text>
                </Space>
              }
            />
          </div>
        ) : (
          <Table<VisitItem>
            rowKey="event_id"
            size="middle"
            loading={loading}
            dataSource={list?.items ?? []}
            pagination={false}
            columns={[
              {
                title: '时间',
                dataIndex: 'occurred_at',
                width: 150,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v.replace('T', ' ').slice(0, 16)}
                  </Text>
                ),
              },
              {
                title: '客户',
                dataIndex: 'customer_name',
                width: 160,
                render: (_: string, r) => (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0 }}
                    onClick={() => navigate(`/customers/${r.customer_id}`)}
                  >
                    {r.customer_name}
                  </Button>
                ),
              },
              {
                title: '访问页面',
                dataIndex: 'page',
                render: (v: string) => <Text code>{v}</Text>,
              },
              {
                title: '渠道',
                dataIndex: 'channel_label',
                width: 110,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: '来源页',
                dataIndex: 'referrer',
                ellipsis: true,
                render: (v: string | null) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v ?? '—'}
                  </Text>
                ),
              },
              {
                title: '证据',
                dataIndex: 'evidence_ref',
                width: 170,
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
        {summary?.note ?? ''}
        <ArrowRightOutlined style={{ marginLeft: 6, fontSize: 11 }} />
      </Text>
    </Space>
  )
}
