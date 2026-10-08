import {
  CheckCircleOutlined,
  FileExcelOutlined,
  FileTextOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
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
import type {
  BatchCustomerItem,
  BatchDetailResponse,
  BatchListResponse,
  BatchSummary,
  SkippedRow,
} from '../api/types'
import { SKIP_REASON_LABELS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: 'green',
  PARTIAL: 'gold',
  FAILED: 'red',
}

const STATUS_LABELS: Record<string, string> = {
  SUCCESS: '成功',
  PARTIAL: '部分成功',
  FAILED: '失败',
}

/** 来源类型配色（TEST 必须一眼可辨，避免把演示数据当真实业绩） */
const SOURCE_COLORS: Record<string, string> = {
  REAL: 'green',
  IMPORT: 'blue',
  SYNC: 'cyan',
  WEB: 'geekblue',
  TEST: 'orange',
  DEMO: 'purple',
  MANUAL: 'default',
  SYSTEM: 'default',
}

/**
 * 来源类型中文名（与后端 SourceType 8 种对齐）。
 * ★ 必须在使用前声明 —— `const` 有暂时性死区，
 *   放在文件末尾会在渲染时报 "Cannot access before initialization"。
 */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  REAL: '真实数据',
  IMPORT: '文件导入',
  SYNC: '系统同步',
  WEB: '公开网络',
  TEST: '测试数据',
  DEMO: '演示数据',
  MANUAL: '人工录入',
  SYSTEM: '系统生成',
}

/**
 * 文件资料页（需求 §八「数据中心 → 文件资料」）。
 *
 * ★ 定位：导入批次的**追溯入口**。
 *   §十九 要求"所有数字必须可追溯"，所以这里不只列文件名，
 *   还要能看到：这批文件建了哪些客户、跳过了哪些行及原因、用了哪个来源类型。
 * ★ 本页**不上传文件** —— 上传走「客户管理 → 导入 Excel/CSV」，
 *   那里有字段映射预览与冲突处理（TASK-001）。本页只做查阅与追溯，
 *   避免同一件事有两个入口（§四 不重复造）。
 */
export default function FileAssetsPage() {
  const [list, setList] = useState<BatchListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [detail, setDetail] = useState<BatchDetailResponse | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      setList(await apiGet<BatchListResponse>('/api/imports'))
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

  const openDetail = async (batchId: number) => {
    setDetailOpen(true)
    setDetailLoading(true)
    setDetail(null)
    try {
      setDetail(await apiGet<BatchDetailResponse>(`/api/imports/${batchId}`))
    } catch (err) {
      setError((err as { message?: string }).message ?? '读取批次详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const items = list?.items ?? []
  // ★ 只统计真实存在的批次；没有批次时显示 —（不填 0 冒充"导入过但都是 0"）
  const totalRows = items.reduce((s, b) => s + (b.rows_total.value ?? 0), 0)
  const totalSkipped = items.reduce((s, b) => s + (b.rows_skipped.value ?? 0), 0)
  const testBatches = items.filter((b) => b.source_type === 'TEST').length

  const cards = [
    {
      title: '导入批次',
      icon: <FileTextOutlined />,
      color: '#2563EB',
      value: items.length ? items.length : null,
    },
    {
      title: '累计行数',
      icon: <FileExcelOutlined />,
      color: '#7C3AED',
      value: items.length ? totalRows : null,
    },
    {
      title: '累计跳过',
      icon: <WarningOutlined />,
      color: '#F59E0B',
      value: items.length ? totalSkipped : null,
    },
    {
      title: '测试来源批次',
      icon: <CheckCircleOutlined />,
      color: '#EA580C',
      value: items.length ? testBatches : null,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            文件资料
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            历次导入的批次与追溯明细。上传文件请到「客户管理 → 导入 Excel/CSV」
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
            刷新
          </Button>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        message="这里是导入结果的追溯入口，不是上传入口"
        description={
          <Space orientation="vertical" size={2}>
            <Text style={{ fontSize: 12 }}>
              每个批次都能点开看到：这批文件建了哪些客户、跳过了哪些行及原因、用了哪个来源类型。
            </Text>
            <Text style={{ fontSize: 12 }}>
              上传与字段映射在「客户管理 → 导入 Excel/CSV」（含预览与重复冲突处理），
              本页不重复提供上传，避免同一件事有两个入口。
            </Text>
            <Text type="secondary" style={{ fontSize: 11 }}>
              ★ 来源类型为「测试」的批次不计入真实业绩 —— 报表里的真实指标不会包含它们。
            </Text>
          </Space>
        }
      />

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
                {c.value === null ? <Text type="secondary">—</Text> : c.value}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>导入批次</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 批` : '暂无'}
            </Text>
          </Space>
        }
        styles={{ body: { padding: items.length ? 0 : 24 } }}
      >
        {items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '还没有导入过文件'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  到「客户管理 → 导入 Excel/CSV」上传一个文件后，这里会出现批次记录
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<BatchSummary>
            rowKey="batch_id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={items}
            expandable={{
              expandedRowRender: (r) => (
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label="文件指纹（SHA-256）" span={2}>
                    <Text code style={{ fontSize: 11 }}>
                      {r.file_sha256}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                      同一文件重复导入可据此识别
                    </Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="证据锚点" span={2}>
                    <Text code style={{ fontSize: 11 }}>
                      {r.evidence_ref}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                      客户记录会指向这个批次，可反查来源
                    </Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="总行数">
                    <EvidenceNumber evidence={r.rows_total} />
                  </Descriptions.Item>
                  <Descriptions.Item label="新建客户">
                    <EvidenceNumber evidence={r.rows_created} />
                  </Descriptions.Item>
                  <Descriptions.Item label="重复去重">
                    <EvidenceNumber evidence={r.rows_deduplicated} />
                  </Descriptions.Item>
                  <Descriptions.Item label="跳过">
                    <EvidenceNumber evidence={r.rows_skipped} />
                  </Descriptions.Item>
                </Descriptions>
              ),
            }}
            columns={[
              {
                title: '文件',
                dataIndex: 'filename',
                render: (v: string, r) => (
                  <Space size={8}>
                    <FileExcelOutlined style={{ color: '#16A34A' }} />
                    <Space orientation="vertical" size={0}>
                      <Text>{v}</Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        批次 #{r.batch_id}
                      </Text>
                    </Space>
                  </Space>
                ),
              },
              {
                title: '结果',
                dataIndex: 'status',
                width: 100,
                render: (v: string) => (
                  <Tag color={STATUS_COLORS[v] ?? 'default'}>{STATUS_LABELS[v] ?? v}</Tag>
                ),
              },
              {
                title: '来源类型',
                dataIndex: 'source_type',
                width: 110,
                render: (v: string) => (
                  <Tooltip title={v === 'TEST' ? '测试数据，不计入真实业绩' : undefined}>
                    <Tag color={SOURCE_COLORS[v] ?? 'default'} style={{ cursor: 'help' }}>
                      {SOURCE_TYPE_LABELS[v] ?? v}
                    </Tag>
                  </Tooltip>
                ),
              },
              {
                title: '总行数',
                width: 90,
                render: (_: unknown, r: BatchSummary) => <EvidenceNumber evidence={r.rows_total} />,
              },
              {
                title: '新建',
                width: 80,
                render: (_: unknown, r: BatchSummary) => <EvidenceNumber evidence={r.rows_created} />,
              },
              {
                title: '去重',
                width: 80,
                render: (_: unknown, r: BatchSummary) => (
                  <EvidenceNumber evidence={r.rows_deduplicated} />
                ),
              },
              {
                title: '跳过',
                width: 80,
                render: (_: unknown, r: BatchSummary) => <EvidenceNumber evidence={r.rows_skipped} />,
              },
              {
                title: '导入时间',
                dataIndex: 'imported_at',
                width: 160,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '操作',
                key: 'action',
                width: 90,
                render: (_: unknown, r: BatchSummary) => (
                  <Button size="small" type="link" onClick={() => void openDetail(r.batch_id)}>
                    追溯明细
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 批次追溯明细 */}
      <Drawer
        title={detail ? `批次 #${detail.batch_id} · ${detail.filename}` : '批次追溯明细'}
        width={860}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detailLoading && <Text type="secondary">加载中…</Text>}
        {detail && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="文件">{detail.filename}</Descriptions.Item>
              <Descriptions.Item label="结果">
                <Tag color={STATUS_COLORS[detail.status] ?? 'default'}>
                  {STATUS_LABELS[detail.status] ?? detail.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="来源类型">
                <Tag color={SOURCE_COLORS[detail.source_type] ?? 'default'}>
                  {SOURCE_TYPE_LABELS[detail.source_type] ?? detail.source_type}
                </Tag>
                {detail.source_type === 'TEST' && (
                  <Text type="warning" style={{ fontSize: 11, marginLeft: 6 }}>
                    测试数据，不计入真实业绩
                  </Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="导入时间">{stamp(detail.imported_at)}</Descriptions.Item>
              <Descriptions.Item label="总行数">
                <EvidenceNumber evidence={detail.rows_total} />
              </Descriptions.Item>
              <Descriptions.Item label="新建客户">
                <EvidenceNumber evidence={detail.rows_created} />
              </Descriptions.Item>
              <Descriptions.Item label="重复去重">
                <EvidenceNumber evidence={detail.rows_deduplicated} />
              </Descriptions.Item>
              <Descriptions.Item label="跳过">
                <EvidenceNumber evidence={detail.rows_skipped} />
              </Descriptions.Item>
              <Descriptions.Item label="证据锚点" span={2}>
                <Text code style={{ fontSize: 11 }}>
                  {detail.evidence_ref}
                </Text>
              </Descriptions.Item>
            </Descriptions>

            {/* ★ 跳过的行与原因 —— §十九 可追溯的关键：不只看"成功了几条" */}
            {detail.skipped_reasons.length > 0 && (
              <Card
                size="small"
                variant="outlined"
                title={
                  <Space size={6}>
                    <WarningOutlined style={{ color: '#F59E0B' }} />
                    <span>被跳过的行（{detail.skipped_reasons.length}）</span>
                  </Space>
                }
              >
                <Table<SkippedRow>
                  rowKey={(r) => `${r.row}-${r.reason}`}
                  size="small"
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  dataSource={detail.skipped_reasons}
                  columns={[
                    { title: '行号', dataIndex: 'row', width: 80 },
                    {
                      title: '原因',
                      dataIndex: 'reason',
                      width: 160,
                      render: (v: string) => (
                        <Tag color="gold">{SKIP_REASON_LABELS[v] ?? v}</Tag>
                      ),
                    },
                    {
                      title: '原始内容',
                      dataIndex: 'raw',
                      ellipsis: true,
                      render: (v: unknown) =>
                        v === null || v === undefined ? (
                          <Text type="secondary">—</Text>
                        ) : (
                          <Text style={{ fontSize: 12 }}>{String(v)}</Text>
                        ),
                    },
                  ]}
                />
              </Card>
            )}

            {detail.skipped_columns.length > 0 && (
              <Card size="small" variant="outlined" title="未被采用的列">
                <Space orientation="vertical" size={4}>
                  {detail.skipped_columns.map((c) => (
                    <Text key={c.column} style={{ fontSize: 12 }}>
                      <Text code>{c.column}</Text> —— {SKIP_REASON_LABELS[c.reason] ?? c.reason}
                    </Text>
                  ))}
                </Space>
              </Card>
            )}

            {/* 这批文件建出来的客户 —— 从批次反查到具体客户 */}
            <Card
              size="small"
              variant="outlined"
              title={
                <Space size={6}>
                  <span>这批文件建立的客户（{detail.customers.length}）</span>
                  {detail.pending_review_customer_ids.length > 0 && (
                    <Tag color="gold">{detail.pending_review_customer_ids.length} 条待人工复核</Tag>
                  )}
                </Space>
              }
              styles={{ body: { padding: detail.customers.length ? 0 : 20 } }}
            >
              {detail.customers.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  这批文件没有新建客户（可能全部被判为重复）。
                </Text>
              ) : (
                <Table<BatchCustomerItem>
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  dataSource={detail.customers}
                  columns={[
                    {
                      title: '客户',
                      dataIndex: 'name',
                      render: (v: string, r) => (
                        <Space size={6}>
                          <Text>{v}</Text>
                          {detail.pending_review_customer_ids.includes(r.id) && (
                            <Tooltip title="疑似重复，待人工确认">
                              <Tag color="gold">待复核</Tag>
                            </Tooltip>
                          )}
                        </Space>
                      ),
                    },
                    { title: '公司', dataIndex: 'company_name', render: (v) => v ?? '—' },
                    { title: '电话', dataIndex: 'phone', render: (v) => v ?? '—' },
                    { title: '邮箱', dataIndex: 'email', render: (v) => v ?? '—' },
                    {
                      title: '来源类型',
                      dataIndex: 'source_type',
                      width: 100,
                      render: (v: string) => (
                        <Tag color={SOURCE_COLORS[v] ?? 'default'}>{SOURCE_TYPE_LABELS[v] ?? v}</Tag>
                      ),
                    },
                    {
                      title: '证据',
                      dataIndex: 'evidence_ref',
                      width: 150,
                      render: (v: string) => (
                        <Text code style={{ fontSize: 11 }}>
                          {v}
                        </Text>
                      ),
                    },
                  ]}
                />
              )}
            </Card>

            <Collapse
              size="small"
              items={[
                {
                  key: 'ids',
                  label: `全部客户 ID（${detail.customer_ids.length}）`,
                  children: (
                    <Text style={{ fontSize: 12 }}>{detail.customer_ids.join('、') || '（无）'}</Text>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
