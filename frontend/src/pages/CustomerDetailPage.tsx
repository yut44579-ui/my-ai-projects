import { ArrowLeftOutlined, ExportOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Row,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { CustomerDetail, CustomerTimelineResponse, EvidenceValue } from '../api/types'
import { DEDUPE_STATE_LABELS } from '../api/types'
import MessageTimeline from '../components/MessageTimeline'
import SourceTag from '../components/SourceTag'

const { Title, Text, Link } = Typography

/** 把后端时间戳显示成「YYYY-MM-DD HH:mm:ss」 */
function stamp(value: string | null | undefined): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

/**
 * 显式空态区块（沟通记录 / 风险提醒 / 相关批次明细）。
 *
 * ★ 只显示后端给的 NO_DATA 文案，**不显示任何数字、不放假图表、不放占位列表**。
 */
function EmptyBlock({ title, evidence }: { title: string; evidence: EvidenceValue }) {
  return (
    <Card variant="outlined" title={title} styles={{ body: { padding: 0 } }}>
      <div style={{ padding: 32 }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary">{evidence.reason ?? '暂无数据'}</Text>
          }
        />
      </div>
    </Card>
  )
}

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<CustomerDetail | null>(null)
  const [timeline, setTimeline] = useState<CustomerTimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [d, t] = await Promise.all([
        apiGet<CustomerDetail>(`/api/customers/${id}`),
        apiGet<CustomerTimelineResponse>(`/api/customers/${id}/timeline`),
      ])
      setDetail(d)
      setTimeline(t)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
      setDetail(null)
      setTimeline(null)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) {
    return (
      <Card variant="outlined" styles={{ body: { padding: 48, textAlign: 'center' } }}>
        <Spin />
      </Card>
    )
  }

  if (error || !detail) {
    return (
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/customers')}>
          返回列表
        </Button>
        <Alert type="error" showIcon message="加载失败" description={error ?? '客户不存在'} />
      </Space>
    )
  }

  const batch = detail.source.batch
  // evidence_ref 指向的是批次详情接口（TASK-001 的「追溯入口」），点击即打开真实批次详情
  const batchHref = batch ? `/api/imports/${batch.batch_id}` : (detail.source.evidence_ref ?? undefined)

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {/* 头部：姓名 + 公司 + 来源 Tag + 去重状态 Tag + 返回列表 */}
      <Card variant="outlined" styles={{ body: { padding: 16 } }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap align="center" size={12}>
            <Title level={4} style={{ margin: 0 }}>
              {detail.name}
            </Title>
            <Text type="secondary">{detail.company_name ?? '未填写公司'}</Text>
            <SourceTag value={detail.source_type} />
            <Tag color={detail.dedupe_state === 'CLEAN' ? 'default' : 'gold'}>
              {DEDUPE_STATE_LABELS[detail.dedupe_state] ?? detail.dedupe_state}
            </Tag>
          </Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/customers')}>
            返回列表
          </Button>
        </Space>
      </Card>

      {/* 状态：V1 没有生命周期状态，这里是显式占位（不是数据库字段，见 customers 表注释） */}
      <Alert
        type="info"
        showIcon
        message={`状态：${detail.status.label}（V1 暂无流程）`}
        description={detail.status.note}
      />

      <Row gutter={16}>
        {/* 左侧：基本信息 */}
        <Col xs={24} lg={12}>
          <Card variant="outlined" title="基本信息">
            <Descriptions column={1} size="small" colon={false}>
              <Descriptions.Item label="电话">{detail.phone ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="邮箱">{detail.email ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="地区">{detail.region ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="备注">{detail.note ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="首次接触">{stamp(detail.first_seen_at)}</Descriptions.Item>
              <Descriptions.Item label="最近命中">{stamp(detail.last_seen_at)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        {/* 右侧：来源与可追溯 */}
        <Col xs={24} lg={12}>
          <Card variant="outlined" title="来源与可追溯">
            <Descriptions column={1} size="small" colon={false}>
              <Descriptions.Item label="来源">
                <SourceTag value={detail.source.source_type} />
              </Descriptions.Item>
              <Descriptions.Item label="文件名">{batch?.filename ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="批次号">
                {batch ? `#${batch.batch_id}` : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="导入时间">
                {stamp(batch?.imported_at)}
              </Descriptions.Item>
              <Descriptions.Item label="evidence_ref">
                {detail.source.evidence_ref ? (
                  batchHref ? (
                    <Link href={batchHref} target="_blank" rel="noreferrer">
                      {detail.source.evidence_ref} <ExportOutlined />
                    </Link>
                  ) : (
                    <Text>{detail.source.evidence_ref}</Text>
                  )
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      {/* 事件流：只列真实事件（来源导入 / 去重状态），没有的绝不编 */}
      <Card variant="outlined" title="事件流">
        {timeline && timeline.events.length > 0 ? (
          <Timeline
            items={timeline.events.map((e) => ({
              children: (
                <Space orientation="vertical" size={0}>
                  <Text strong>{e.title}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(e.occurred_at)}
                    {e.detail ? ` ｜ ${e.detail}` : ''}
                  </Text>
                </Space>
              ),
            }))}
          />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text type="secondary">暂无事件</Text>}
          />
        )}
        {/* 还没接入的类型：显式说明，不用空列表冒充"已接入但为空" */}
        <Space orientation="vertical" size={4} style={{ marginTop: 8 }}>
          {timeline?.unavailable.map((u) => (
            <Text key={u.kind} type="secondary" style={{ fontSize: 12 }}>
              {u.label}：{u.reason}
            </Text>
          ))}
        </Space>
      </Card>

      {/* 沟通记录：TASK-003 起接真数据 —— 真时间线 + 底部人工回复输入框 */}
      <MessageTimeline customerId={detail.id} onChanged={() => void load()} />

      {/* 尚未接入的区块 —— 显式空态，不含任何数字或假占位 */}
      <EmptyBlock title="风险提醒" evidence={detail.risks} />
      <EmptyBlock title="相关批次明细" evidence={detail.batch_details} />
    </Space>
  )
}
