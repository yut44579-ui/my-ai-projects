import {
  CheckCircleOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
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
import { apiGet, apiPost } from '../api/client'
import { PRESENCE_OPTIONS } from '../api/types'
import type { UserItem, UserListResponse, UserRole } from '../api/types'
import { readCachedUser } from '../utils/currentUser'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '从未登录'

const ROLE_LABELS: Record<string, string> = { ADMIN: '管理员', MEMBER: '成员' }
const ROLE_COLORS: Record<string, string> = { ADMIN: 'red', MEMBER: 'blue' }

/** 状态圆点颜色（与个人中心、顶栏共用同一份来源，避免三处不一致） */
function presenceColor(p: string | undefined): string {
  return PRESENCE_OPTIONS.find((o) => o.value === p)?.color ?? '#9CA3AF'
}

export default function UsersPage() {
  const me = readCachedUser()
  const isAdmin = me?.role === 'ADMIN'

  const [list, setList] = useState<UserListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const [resetTarget, setResetTarget] = useState<UserItem | null>(null)
  const [resetForm] = Form.useForm()

  const load = useCallback(async () => {
    try {
      setList(await apiGet<UserListResponse>('/api/users'))
      setError(null)
    } catch (err) {
      const e = err as { message?: string; status?: number }
      setError(
        e.status === 403
          ? '需要管理员权限才能查看账号列表（当前账号是成员）'
          : (e.message ?? '加载失败'),
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      await apiPost<UserItem>('/api/users', {
        username: v.username,
        display_name: v.display_name,
        password: v.password,
        role: v.role,
      })
      message.success('账号已创建')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const submitReset = async () => {
    if (!resetTarget) return
    const v = await resetForm.validateFields()
    setSaving(true)
    try {
      const res = await apiPost<{ message: string }>(
        `/api/users/${resetTarget.id}/reset-password`,
        { new_password: v.new_password },
      )
      message.success(res.message)
      setResetTarget(null)
      resetForm.resetFields()
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '重置失败')
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (row: UserItem) => {
    try {
      const res = await apiPost<{ message: string }>(`/api/users/${row.id}/toggle-active`, {})
      message.success(res.message)
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '操作失败')
    }
  }

  const items = list?.items ?? []
  const activeCount = items.filter((u) => u.is_active).length
  const adminCount = items.filter((u) => u.role === 'ADMIN').length

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            用户与权限
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            账号由管理员创建（系统不开放自助注册，避免部署后被抢注管理员）
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Tooltip title={isAdmin ? '' : '需要管理员权限'}>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!isAdmin}
                onClick={() => setCreateOpen(true)}
              >
                新建账号
              </Button>
            </Tooltip>
          </Space>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        message="权限模型：只有两级角色"
        description={
          <Space orientation="vertical" size={2}>
            <Text style={{ fontSize: 12 }}>
              <Tag color="red">管理员</Tag>可管理账号（新建 / 重置口令 / 停用）
            </Text>
            <Text style={{ fontSize: 12 }}>
              <Tag color="blue">成员</Tag>只能用业务功能，访问账号管理接口会返回 403
            </Text>
            <Text type="secondary" style={{ fontSize: 11 }}>
              规格见 docs/AUTH_SPEC.md §1 —— V1 刻意「不做」权限矩阵（按资源/按动作的细粒度授权）
            </Text>
          </Space>
        }
      />

      <Row gutter={16}>
        <Col xs={24} sm={8}>
          <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }}>
            <Space size={10} style={{ marginBottom: 8 }}>
              <span
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: '50%',
                  background: '#2563EB14',
                  color: '#2563EB',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <TeamOutlined />
              </span>
              <Text type="secondary" style={{ fontSize: 13 }}>
                账号总数
              </Text>
            </Space>
            <div style={{ fontSize: 24, fontWeight: 600 }}>
              {list?.total ? <EvidenceNumber evidence={list.total} /> : <Text type="secondary">—</Text>}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }}>
            <Space size={10} style={{ marginBottom: 8 }}>
              <span
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: '50%',
                  background: '#16A34A14',
                  color: '#16A34A',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <CheckCircleOutlined />
              </span>
              <Text type="secondary" style={{ fontSize: 13 }}>
                启用中
              </Text>
            </Space>
            <div style={{ fontSize: 24, fontWeight: 600 }}>{items.length ? activeCount : '—'}</div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }}>
            <Space size={10} style={{ marginBottom: 8 }}>
              <span
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: '50%',
                  background: '#DC262614',
                  color: '#DC2626',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <KeyOutlined />
              </span>
              <Text type="secondary" style={{ fontSize: 13 }}>
                管理员
              </Text>
            </Space>
            <div style={{ fontSize: 24, fontWeight: 600 }}>{items.length ? adminCount : '—'}</div>
          </Card>
        </Col>
      </Row>

      <Card
        variant="outlined"
        title="账号清单"
        styles={{ body: { padding: items.length ? 0 : 24 } }}
      >
        {items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '没有可显示的账号'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  若当前账号是「成员」，这里会因权限不足而看不到列表
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<UserItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={items}
            expandable={{
              expandedRowRender: (r) => (
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label="账号 ID">{r.id}</Descriptions.Item>
                  <Descriptions.Item label="登录名">{r.username}</Descriptions.Item>
                  <Descriptions.Item label="显示名">{r.display_name}</Descriptions.Item>
                  <Descriptions.Item label="角色">{ROLE_LABELS[r.role] ?? r.role}</Descriptions.Item>
                  <Descriptions.Item label="最近登录" span={2}>
                    {stamp(r.last_login_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间" span={2}>
                    {stamp(r.created_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="口令" span={2}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      口令以 PBKDF2-HMAC-SHA256 加盐哈希存储，接口永不回显；
                      重置口令会让该账号已发出的登录令牌立即失效
                    </Text>
                  </Descriptions.Item>
                </Descriptions>
              ),
            }}
            columns={[
              {
                title: '账号',
                dataIndex: 'display_name',
                render: (v: string, r) => (
                  <Space size={8}>
                    {/* ★ TASK-040：头像 + 实时状态圆点。
                        状态由后端按心跳推算，列表刷新时即最新值。 */}
                    <span style={{ position: 'relative', display: 'inline-flex' }}>
                      <Avatar
                        size={30}
                        src={r.avatar_url ?? undefined}
                        style={{ background: '#2563EB' }}
                        icon={r.avatar_url ? undefined : <UserOutlined />}
                      />
                      <Tooltip title={`${r.presence_label ?? '离线'}：${r.presence_reason ?? ''}`}>
                        <span
                          style={{
                            position: 'absolute',
                            right: -1,
                            bottom: -1,
                            width: 10,
                            height: 10,
                            borderRadius: '50%',
                            background: presenceColor(r.presence),
                            border: '2px solid #FFFFFF',
                            cursor: 'help',
                          }}
                        />
                      </Tooltip>
                    </span>
                    <Space orientation="vertical" size={0}>
                      <Text>
                        {v}
                        {me?.id === r.id && (
                          <Tag color="green" style={{ marginLeft: 6 }}>
                            当前登录
                          </Tag>
                        )}
                      </Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {r.username}
                      </Text>
                    </Space>
                  </Space>
                ),
              },
              {
                title: '角色',
                dataIndex: 'role',
                width: 100,
                render: (v: UserRole) => <Tag color={ROLE_COLORS[v]}>{ROLE_LABELS[v] ?? v}</Tag>,
              },
              {
                title: '状态',
                dataIndex: 'is_active',
                width: 100,
                render: (v: boolean) =>
                  v ? <Tag color="green">启用</Tag> : <Tag color="default">已停用</Tag>,
              },
              {
                title: '最近登录',
                dataIndex: 'last_login_at',
                width: 170,
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
                    <Tooltip title={isAdmin ? '' : '需要管理员权限'}>
                      <Button
                        size="small"
                        type="link"
                        disabled={!isAdmin}
                        onClick={() => {
                          setResetTarget(r)
                          resetForm.resetFields()
                        }}
                      >
                        重置口令
                      </Button>
                    </Tooltip>
                    <Tooltip
                      title={
                        me?.id === r.id
                          ? '不能停用当前登录的账号（否则会把自己锁在门外）'
                          : isAdmin
                            ? ''
                            : '需要管理员权限'
                      }
                    >
                      <Button
                        size="small"
                        type="link"
                        danger={r.is_active}
                        disabled={!isAdmin || me?.id === r.id}
                        icon={r.is_active ? <StopOutlined /> : undefined}
                        onClick={() => void toggle(r)}
                      >
                        {r.is_active ? '停用' : '启用'}
                      </Button>
                    </Tooltip>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 新建账号 */}
      <Modal
        title="新建账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="username"
            label="登录名"
            rules={[{ required: true, message: '填登录名' }]}
            extra="统一按小写存储，避免 Admin 与 admin 变成两个账号"
          >
            <Input placeholder="如 lisi" autoComplete="off" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名" rules={[{ required: true, message: '填显示名' }]}>
            <Input placeholder="如 李四" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始口令"
            rules={[
              { required: true, message: '填初始口令' },
              { min: 8, message: '至少 8 位' },
            ]}
            extra="加密存储，接口永不回显"
          >
            <Input.Password placeholder="至少 8 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="MEMBER">
            <Select
              options={[
                { value: 'MEMBER', label: '成员（只能用业务功能）' },
                { value: 'ADMIN', label: '管理员（可管理账号）' },
              ]}
            />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="系统不提供注册入口"
            description="账号只能由管理员创建，避免部署后被人抢先注册管理员（AUTH_SPEC §1/§9）。"
          />
        </Form>
      </Modal>

      {/* 重置口令 */}
      <Modal
        title={resetTarget ? `重置口令：${resetTarget.display_name}` : '重置口令'}
        open={!!resetTarget}
        onCancel={() => setResetTarget(null)}
        onOk={() => void submitReset()}
        confirmLoading={saving}
        okText="重置"
      >
        <Form form={resetForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="new_password"
            label="新口令"
            rules={[
              { required: true, message: '填新口令' },
              { min: 8, message: '至少 8 位' },
            ]}
          >
            <Input.Password placeholder="至少 8 位" autoComplete="new-password" />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="重置后该账号的登录令牌立即失效"
            description="靠 users.token_version 自增实现 —— 不是等过期，而是马上踢下线（见 AUTH_SPEC §5）。"
          />
        </Form>
      </Modal>
    </Space>
  )
}
