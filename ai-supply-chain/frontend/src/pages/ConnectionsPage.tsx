import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  FileExcelOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '../api/client'
import type {
  ConnectionSummaryResponse,
  ConnectionTestResponse,
  DataConnectionItem,
  DataConnectionListResponse,
  SyncRecordItem,
  SyncRecordListResponse,
} from '../api/types'
import { CONNECTION_STATUS_COLORS, CONNECTOR_KIND_OPTIONS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

const KIND_ICONS: Record<string, React.ReactNode> = {
  EXCEL_CSV: <FileExcelOutlined />,
  MYSQL: <DatabaseOutlined />,
  POSTGRESQL: <DatabaseOutlined />,
  CRM: <CloudServerOutlined />,
  ERP: <CloudServerOutlined />,
  API: <ApiOutlined />,
  WEBHOOK: <LinkOutlined />,
}

/** 需要填连接信息的类型 */
const NEEDS_DB_CONFIG: string[] = ['MYSQL', 'POSTGRESQL']

export default function ConnectionsPage() {
  const [list, setList] = useState<DataConnectionListResponse | null>(null)
  const [summary, setSummary] = useState<ConnectionSummaryResponse | null>(null)
  const [records, setRecords] = useState<SyncRecordListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()
  const [kind, setKind] = useState<string>('EXCEL_CSV')
  const [testingId, setTestingId] = useState<number | null>(null)
  /**
   * ★ 按连接缓存"该连接的同步记录"（需求 §二十 要求每个连接都能看到同步记录）。
   *   此前只有页面级的全局卡片，单条连接的同步历史看不到 —— 接口本来就支持
   *   `?connection_id=`，只是页面没用。这里在展开该行时按需拉取并缓存。
   */
  const [connRecords, setConnRecords] = useState<
    Record<number, SyncRecordItem[] | 'loading' | 'error'>
  >({})

  const load = useCallback(async () => {
    try {
      const [l, s, r] = await Promise.all([
        apiGet<DataConnectionListResponse>('/api/connections'),
        apiGet<ConnectionSummaryResponse>('/api/connections/summary'),
        apiGet<SyncRecordListResponse>('/api/connections/sync-records?limit=20'),
      ])
      setList(l)
      setSummary(s)
      setRecords(r)
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

  /** 展开某连接时拉取它自己的同步记录（★ 需求 §二十：每个连接要显示同步记录） */
  const loadConnRecords = useCallback(async (connectionId: number) => {
    setConnRecords((prev) => ({ ...prev, [connectionId]: 'loading' }))
    try {
      const res = await apiGet<SyncRecordListResponse>(
        `/api/connections/sync-records?connection_id=${connectionId}&limit=10`,
      )
      setConnRecords((prev) => ({ ...prev, [connectionId]: res.items }))
    } catch {
      setConnRecords((prev) => ({ ...prev, [connectionId]: 'error' }))
    }
  }, [])

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const config = NEEDS_DB_CONFIG.includes(v.kind)
        ? {
            host: v.host,
            port: v.port,
            database: v.database,
            user: v.user,
          }
        : undefined
      await apiPost<DataConnectionItem>('/api/connections', {
        kind: v.kind,
        name: v.name,
        config,
        secret: v.secret ?? null,
        scope_note: v.scope_note ?? null,
        permission_note: v.permission_note ?? null,
      })
      message.success('连接已创建；请点「测试连接」验证是否真的可用')
      setCreateOpen(false)
      form.resetFields()
      setKind('EXCEL_CSV')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const runTest = async (row: DataConnectionItem) => {
    setTestingId(row.id)
    try {
      const res = await apiPost<ConnectionTestResponse>(`/api/connections/${row.id}/test`, {})
      // ★ 结果如实显示：成功就是成功，失败把后端返回的真实原因原样展示
      if (res.ok) {
        message.success(`连接成功（${res.elapsed_ms}ms）：${res.message}`)
      } else {
        message.error({
          content: `连接失败（${res.elapsed_ms}ms）：${res.message}`,
          duration: 8,
        })
      }
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const toggle = async (row: DataConnectionItem, enabled: boolean) => {
    try {
      await apiPost<DataConnectionItem>(`/api/connections/${row.id}/toggle?enabled=${enabled}`, {})
      message.success(enabled ? '已启用' : '已停用')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '操作失败')
    }
  }

  const disconnect = async (row: DataConnectionItem) => {
    try {
      await apiDelete<DataConnectionItem>(`/api/connections/${row.id}`)
      message.success('已断开（同步记录仍然保留）')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '断开失败')
    }
  }

  const cards = [
    { title: '连接总数', icon: <LinkOutlined />, color: '#2563EB', evidence: summary?.total },
    { title: '连接正常', icon: <CheckCircleOutlined />, color: '#16A34A', evidence: summary?.ok_total },
    {
      title: '未配置',
      icon: <CloudServerOutlined />,
      color: '#94A3B8',
      evidence: summary?.unconfigured_total,
    },
    {
      title: '连接失败',
      icon: <ExclamationCircleOutlined />,
      color: '#DC2626',
      evidence: summary?.error_total,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            数据连接
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            连接状态只由真实的测试/同步结果驱动；没有凭据时如实显示「未配置」
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建连接
            </Button>
          </Space>
        </Col>
      </Row>

      {/* 如实说明能力边界：哪些真实现、哪些仅框架 */}
      {summary && (
        <Alert
          type="info"
          showIcon
          message="本系统不做假连接页"
          description={
            <Space orientation="vertical" size={4}>
              <Text style={{ fontSize: 12 }}>{summary.note}</Text>
              <Text style={{ fontSize: 12 }}>
                <Text strong>已真正实现：</Text>
                {summary.supported_kinds.join(' / ')}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <Text strong>仅建配置框架、V1 未接入：</Text>
                {summary.framework_only_kinds.join(' / ')} —— 这些需要外部系统授权，
                没有凭据时无法做真实测试，因此测试会如实返回失败与原因
              </Text>
            </Space>
          }
        />
      )}

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
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 连接清单 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>连接清单</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 个` : '暂无'}
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
                <Text>{loading ? '加载中…' : '还没有配置数据连接'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点「新建连接」开始；没有凭据的连接会如实显示「未配置」
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<DataConnectionItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            expandable={{
              onExpand: (expanded, r) => {
                // ★ 只在首次展开时拉取，已缓存的（含空数组）不重复请求
                if (expanded && connRecords[r.id] === undefined) void loadConnRecords(r.id)
              },
              expandedRowRender: (r) => {
                const own = connRecords[r.id]
                return (
                  <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                    <Descriptions column={2} size="small" bordered>
                      <Descriptions.Item label="能做什么" span={2}>
                        {r.note}
                      </Descriptions.Item>
                      <Descriptions.Item label="数据范围">{r.scope_note ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="权限">{r.permission_note ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="非敏感配置" span={2}>
                        {Object.keys(r.config).length
                          ? Object.entries(r.config)
                              .map(([k, v]) => `${k}=${v}`)
                              .join('  ')
                          : '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label="凭据">
                        {r.has_credential ? (
                          <Tag color="green">已配置（加密存储，不回显）</Tag>
                        ) : (
                          <Tag>未配置</Tag>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="V1 支持">
                        {r.supported ? (
                          <Tag color="green">已实现</Tag>
                        ) : (
                          <Tag color="default">仅配置框架</Tag>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="最近测试" span={2}>
                        {stamp(r.last_test_at)}
                      </Descriptions.Item>
                      <Descriptions.Item label="最近同步" span={2}>
                        {stamp(r.last_sync_at)}
                      </Descriptions.Item>
                      {r.last_error && (
                        <Descriptions.Item label="最近失败原因" span={2}>
                          <Text type="danger" style={{ fontSize: 12 }}>
                            {r.last_error}
                          </Text>
                        </Descriptions.Item>
                      )}
                    </Descriptions>

                    {/* ★ 该连接自己的同步记录（需求 §二十 要求每连接可见） */}
                    <div>
                      <Text strong style={{ fontSize: 13 }}>
                        该连接的同步记录
                      </Text>
                      <div style={{ marginTop: 6 }}>
                        {own === undefined || own === 'loading' ? (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            加载中…
                          </Text>
                        ) : own === 'error' ? (
                          <Text type="danger" style={{ fontSize: 12 }}>
                            读取失败
                          </Text>
                        ) : own.length === 0 ? (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            这个连接还没有同步过。通过「客户管理 → 导入 Excel/CSV」上传文件后会出现记录。
                          </Text>
                        ) : (
                          <Table<SyncRecordItem>
                            rowKey="id"
                            size="small"
                            pagination={false}
                            dataSource={own}
                            columns={[
                              {
                                title: '结果',
                                dataIndex: 'result_label',
                                width: 90,
                                render: (v: string, rec) => (
                                  <Tag
                                    color={
                                      rec.result === 'SUCCESS'
                                        ? 'green'
                                        : rec.result === 'PARTIAL'
                                          ? 'gold'
                                          : 'red'
                                    }
                                  >
                                    {v}
                                  </Tag>
                                ),
                              },
                              { title: '触发', dataIndex: 'trigger_label', width: 100 },
                              { title: '总行数', dataIndex: 'rows_total', width: 80 },
                              { title: '新建', dataIndex: 'rows_created', width: 70 },
                              { title: '跳过', dataIndex: 'rows_skipped', width: 70 },
                              {
                                title: '时间',
                                dataIndex: 'started_at',
                                width: 160,
                                render: (v: string) => (
                                  <Text type="secondary" style={{ fontSize: 12 }}>
                                    {stamp(v)}
                                  </Text>
                                ),
                              },
                              {
                                title: '说明',
                                dataIndex: 'message',
                                ellipsis: true,
                                render: (v: string | null) => v ?? '—',
                              },
                            ]}
                          />
                        )}
                      </div>
                    </div>
                  </Space>
                )
              },
            }}
            columns={[
              {
                title: '连接',
                dataIndex: 'name',
                render: (v: string, r) => (
                  <Space size={8}>
                    <span style={{ color: '#2563EB' }}>{KIND_ICONS[r.kind] ?? <LinkOutlined />}</span>
                    <Space orientation="vertical" size={0}>
                      <Text>{v}</Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {r.kind_label}
                      </Text>
                    </Space>
                  </Space>
                ),
              },
              {
                title: '状态',
                dataIndex: 'status',
                width: 130,
                render: (v: string, r) => (
                  <Tooltip title={r.last_error ?? r.status_label}>
                    <Tag color={CONNECTION_STATUS_COLORS[v] ?? 'default'} style={{ cursor: 'help' }}>
                      {r.status_label}
                    </Tag>
                  </Tooltip>
                ),
              },
              {
                title: '凭据',
                width: 90,
                render: (_: unknown, r) =>
                  r.has_credential ? (
                    <Tooltip title="凭据加密存储，接口永不回显">
                      <Tag color="green">已配置</Tag>
                    </Tooltip>
                  ) : (
                    <Tag>未配置</Tag>
                  ),
              },
              {
                title: '最近测试',
                dataIndex: 'last_test_at',
                width: 150,
                render: (v: string | null) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '最近同步',
                dataIndex: 'last_sync_at',
                width: 150,
                render: (v: string | null) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '操作',
                key: 'action',
                width: 190,
                render: (_: unknown, r) => (
                  <Space size={4}>
                    <Button
                      size="small"
                      type="link"
                      loading={testingId === r.id}
                      onClick={() => void runTest(r)}
                    >
                      测试连接
                    </Button>
                    {r.status === 'DISABLED' ? (
                      <Button size="small" type="link" onClick={() => void toggle(r, true)}>
                        启用
                      </Button>
                    ) : (
                      <Button size="small" type="link" onClick={() => void toggle(r, false)}>
                        停用
                      </Button>
                    )}
                    <Button size="small" type="link" danger onClick={() => void disconnect(r)}>
                      断开
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 同步记录 */}
      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <SyncOutlined />
            <span>同步记录</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {records?.total.state === 'VALID' ? `${records.total.value} 条` : '暂无'}
            </Text>
          </Space>
        }
        styles={{ body: { padding: (records?.items.length ?? 0) ? 0 : 24 } }}
      >
        {(records?.items.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>还没有同步记录</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  通过「客户管理 → 导入 Excel/CSV」上传文件后，这里会出现同步记录
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<SyncRecordItem>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={records?.items ?? []}
            columns={[
              { title: '连接', dataIndex: 'connection_name', width: 180 },
              { title: '触发', dataIndex: 'trigger_label', width: 100 },
              {
                title: '结果',
                dataIndex: 'result_label',
                width: 100,
                render: (v: string, r) => (
                  <Tag color={r.result === 'SUCCESS' ? 'green' : r.result === 'PARTIAL' ? 'gold' : 'red'}>
                    {v}
                  </Tag>
                ),
              },
              { title: '总行数', dataIndex: 'rows_total', width: 80 },
              { title: '新建', dataIndex: 'rows_created', width: 70 },
              { title: '跳过', dataIndex: 'rows_skipped', width: 70 },
              {
                title: '批次',
                dataIndex: 'import_batch_id',
                width: 80,
                render: (v: number | null) => (v ? `#${v}` : '—'),
              },
              {
                title: '时间',
                dataIndex: 'started_at',
                width: 150,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '说明',
                dataIndex: 'message',
                ellipsis: true,
                render: (v: string | null) => v ?? '—',
              },
            ]}
          />
        )}
      </Card>

      {/* 新建连接 */}
      <Modal
        title="新建数据连接"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        width={620}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="kind" label="连接类型" initialValue="EXCEL_CSV">
            <Select options={CONNECTOR_KIND_OPTIONS} onChange={(v) => setKind(String(v))} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '起个名字' }]}>
            <Input placeholder="如：客户名单导入通道 / 生产库只读" />
          </Form.Item>

          {NEEDS_DB_CONFIG.includes(kind) && (
            <>
              <Row gutter={12}>
                <Col span={16}>
                  <Form.Item name="host" label="主机" rules={[{ required: true, message: '填主机' }]}>
                    <Input placeholder="如 127.0.0.1" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="port" label="端口" rules={[{ required: true, message: '填端口' }]}>
                    <InputNumber style={{ width: '100%' }} placeholder={kind === 'MYSQL' ? '3306' : '5432'} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="database" label="数据库名" rules={[{ required: true, message: '填库名' }]}>
                    <Input />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="user" label="用户名" rules={[{ required: true, message: '填用户名' }]}>
                    <Input />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="secret" label="密码" extra="加密存储，接口永不回显">
                <Input.Password placeholder="连接密码" />
              </Form.Item>
            </>
          )}

          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="scope_note" label="数据范围">
                <Input placeholder="如：customers 表" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="permission_note" label="权限">
                <Input placeholder="如：只读" />
              </Form.Item>
            </Col>
          </Row>

          <Alert
            type="info"
            showIcon
            message="创建后请点「测试连接」验证"
            description="新建时状态不是「连接正常」——只有真实测试通过后才会变成「连接正常」。仅配置框架的连接器（CRM/ERP/企业微信/官网/API/Webhook）测试会如实返回失败。"
          />
        </Form>
      </Modal>
    </Space>
  )
}
