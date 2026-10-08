import { ArrowLeftOutlined, ExportOutlined } from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  List,
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
import type {
  CustomerDetail,
  CustomerTimelineResponse,
  CustomerWorkspaceResponse,
  EvidenceValue,
  RiskAlert,
} from '../api/types'
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
  const [workspace, setWorkspace] = useState<CustomerWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [d, t, w] = await Promise.all([
        apiGet<CustomerDetail>(`/api/customers/${id}`),
        apiGet<CustomerTimelineResponse>(`/api/customers/${id}/timeline`),
        apiGet<CustomerWorkspaceResponse>(`/api/customers/${id}/workspace`),
      ])
      setDetail(d)
      setTimeline(t)
      setWorkspace(w)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
      setDetail(null)
      setTimeline(null)
      setWorkspace(null)
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
      {/* 头部：姓名 + 公司 + 来源 + 去重 + 生命周期/接管状态 */}
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
            {/* ★ 生命周期与接管状态：后端字段，这一版才显示出来 */}
            {workspace && (
              <>
                <Tag color="blue">{workspace.lifecyle_label}</Tag>
                <Tag
                  color={
                    workspace.handover_state === 'AUTO'
                      ? 'default'
                      : workspace.handover_state === 'HUMAN_REQUIRED'
                        ? 'red'
                        : 'volcano'
                  }
                >
                  {workspace.handover_label}
                </Tag>
              </>
            )}
          </Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/customers')}>
            返回列表
          </Button>
        </Space>
      </Card>

      {/* 状态与下一步：判定依据全部是真实字段，不是模型推测 */}
      {workspace && (
        <Card variant="outlined" title="状态与下一步">
          <Row gutter={[16, 16]}>
            <Col xs={24} md={10}>
              <Space orientation="vertical" size={6}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  下一步建议
                </Text>
                <Space size={8}>
                  <Tag color="geekblue" style={{ fontSize: 13, padding: '2px 10px' }}>
                    {workspace.next_action.label}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {workspace.next_action.code}
                  </Text>
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  依据：{workspace.next_action.reason}
                </Text>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  <Badge status={workspace.ai_auto_reply_allowed ? 'success' : 'warning'} />
                  {workspace.ai_auto_reply_allowed
                    ? 'AI 可自动回复'
                    : 'AI 已停用自动回复，需人工处理'}
                </Text>
              </Space>
            </Col>
            <Col xs={24} md={14}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                接管流转历史（{workspace.handover_history.length} 条）
              </Text>
              {workspace.handover_history.length === 0 ? (
                <div style={{ marginTop: 8 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    还没有接管记录（一直是 AI 自动回复）
                  </Text>
                </div>
              ) : (
                <Timeline
                  style={{ marginTop: 8 }}
                  items={workspace.handover_history.map((h) => ({
                    color: h.to_state === 'HUMAN_REQUIRED' ? 'red' : h.to_state === 'HUMAN_ACTIVE' ? 'orange' : 'blue',
                    children: (
                      <Space orientation="vertical" size={0}>
                        <Text style={{ fontSize: 13 }}>
                          {h.from_state ? `${h.from_state} → ` : ''}
                          {h.to_state}
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {' '}（{h.actor_type}｜{h.event_type}）
                          </Text>
                        </Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {stamp(h.created_at)}
                          {h.reason ? ` ｜ ${h.reason}` : ''}
                        </Text>
                      </Space>
                    ),
                  }))}
                />
              )}
            </Col>
          </Row>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {workspace.note}
          </Text>
        </Card>
      )}

      {/* 风险提醒：只来自「命中敏感策略 / AI 回复失败」两条可复现依据 */}
      {workspace && (
        <Card
          variant="outlined"
          title={
            <Space size={8}>
              <span>风险提醒</span>
              <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
                {workspace.risk_total.state === 'VALID' ? `${workspace.risk_total.value} 条` : '暂无'}
              </Text>
            </Space>
          }
        >
          {workspace.risks.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={<Text type="secondary">{workspace.risk_total.reason ?? '暂无风险'}</Text>}
            />
          ) : (
            <List
              dataSource={workspace.risks}
              renderItem={(r: RiskAlert) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={<Badge color={r.level === 'HIGH' ? 'red' : 'orange'} />}
                    title={
                      <Space size={8}>
                        <Text strong style={{ fontSize: 13 }}>
                          {r.title}
                        </Text>
                        <Tag color={r.level === 'HIGH' ? 'red' : 'orange'}>
                          {r.level === 'HIGH' ? '高风险' : '需关注'}
                        </Tag>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {stamp(r.occurred_at)}
                        </Text>
                      </Space>
                    }
                    description={
                      <Space orientation="vertical" size={2}>
                        <Text style={{ fontSize: 12.5, color: '#4B5563' }}>{r.detail}</Text>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          证据：{r.evidence_ref}
                        </Text>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Card>
      )}

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

      {/* 相关批次明细 —— 显式空态，不含任何数字或假占位 */}
      <EmptyBlock title="相关批次明细" evidence={detail.batch_details} />
    </Space>
  )
}
