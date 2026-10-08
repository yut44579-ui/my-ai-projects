import {
  BulbOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { BusinessInsightResponse } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const SEVERITY: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
  info: { color: '#2563EB', label: '正常', icon: <InfoCircleOutlined /> },
  attention: { color: '#F59E0B', label: '需关注', icon: <ExclamationCircleOutlined /> },
  warning: { color: '#DC2626', label: '要处理', icon: <ExclamationCircleOutlined /> },
}

/**
 * 商业洞察（需求 §八 AI 能力）。
 *
 * ★ 本页的诚信设计：
 *   上半部分是**用真实业务数据算出来的**洞察（每条带判定规则与证据锚点，可核对）；
 *   下半部分是**做不了的**洞察及所需数据。
 *   ★ 不编市场判断 —— 缺外部数据就明说缺什么（§二 / §二十六）。
 */
export default function BusinessInsightPage() {
  const [data, setData] = useState<BusinessInsightResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      setData(await apiGet<BusinessInsightResponse>('/api/business-insight/overview'))
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

  const cov = data?.coverage
  const coverageCards = [
    { title: '客户', value: cov?.customers },
    { title: '进行中商机', value: cov?.opportunities_open },
    { title: '进行中项目', value: cov?.projects_active },
    { title: '待处理风险', value: cov?.risks_open },
    { title: '营销内容', value: cov?.contents },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            商业洞察
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            只用系统里真实存在的业务数据做分析；缺外部数据的部分如实列出，不编
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
            刷新
          </Button>
        </Col>
      </Row>

      {data && (
        <Alert
          type="info"
          showIcon
          message="这一页能说什么、不能说什么"
          description={data.note}
        />
      )}

      {/* 数据覆盖 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <DatabaseOutlined />
            <span>分析所用数据</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              共{' '}
              {data?.findings_total ? (
                <EvidenceNumber evidence={data.findings_total} />
              ) : (
                <Text type="secondary">—</Text>
              )}{' '}
              条洞察
            </Text>
          </Space>
        }
      >
        <Row gutter={16}>
          {coverageCards.map((c) => (
            <Col key={c.title} xs={12} sm={8} md={4}>
              <Space orientation="vertical" size={2}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {c.title}
                </Text>
                <Text strong style={{ fontSize: 20 }}>
                  {c.value === undefined ? '—' : c.value}
                </Text>
              </Space>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 洞察 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <BulbOutlined />
            <span>洞察</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              每条都写明判定规则，可核对
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.findings.length ?? 0) ? 12 : 24 } }}
      >
        {(data?.findings.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '没有可分析的业务数据'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  导入客户或创建商机后，这里会出现基于真实数据的洞察
                </Text>
              </Space>
            }
          />
        ) : (
          <Row gutter={16}>
            {data?.findings.map((f) => {
              const sev = SEVERITY[f.severity] ?? SEVERITY.info
              return (
                <Col key={f.key} xs={24} lg={12} style={{ marginBottom: 16 }}>
                  <Card
                    size="small"
                    variant="outlined"
                    styles={{ body: { padding: 14 } }}
                    style={{ borderLeft: `3px solid ${sev.color}`, height: '100%' }}
                  >
                    <Space size={8} style={{ marginBottom: 8 }}>
                      <span style={{ color: sev.color }}>{sev.icon}</span>
                      <Text strong>{f.title}</Text>
                      <Tag color={sev.color === '#2563EB' ? 'blue' : sev.color === '#F59E0B' ? 'gold' : 'red'}>
                        {sev.label}
                      </Tag>
                    </Space>
                    <div style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 8 }}>
                      {f.finding}
                    </div>
                    <Space size={8} wrap>
                      <Text strong style={{ fontSize: 16 }}>
                        {f.value === null ? '—' : f.value}
                      </Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {f.unit}
                      </Text>
                    </Space>
                    <div style={{ marginTop: 8 }}>
                      <Tooltip title={f.rule}>
                        <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                          判定规则：{f.rule}
                        </Text>
                      </Tooltip>
                    </div>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      证据锚点：
                      <Text code style={{ fontSize: 11 }}>
                        {f.evidence_ref}
                      </Text>
                    </Text>
                  </Card>
                </Col>
              )
            })}
          </Row>
        )}
      </Card>

      {/* ★ 做不了的 —— 本页存在的主要理由之一 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <StopOutlined style={{ color: '#9CA3AF' }} />
            <span>这些洞察做不了（缺外部数据）</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              如实列出，不用"行业经验值"凑数
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.missing.length ?? 0) ? 0 : 24 } }}
      >
        {(data?.missing.length ?? 0) === 0 ? (
          <Text type="secondary">—</Text>
        ) : (
          <Table
            rowKey="key"
            size="middle"
            pagination={false}
            dataSource={data?.missing ?? []}
            columns={[
              {
                title: '做不了的洞察',
                dataIndex: 'title',
                width: 180,
                render: (v: string) => <Text strong>{v}</Text>,
              },
              {
                title: '为什么做不了',
                dataIndex: 'why_missing',
                render: (v: string) => <Text style={{ fontSize: 12.5 }}>{v}</Text>,
              },
              {
                title: '需要接入什么',
                dataIndex: 'would_need',
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12.5 }}>
                    {v}
                  </Text>
                ),
              },
            ]}
          />
        )}
      </Card>
    </Space>
  )
}
