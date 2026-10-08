import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  KeyOutlined,
  LockOutlined,
  ReloadOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  message,
  Modal,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { SecuritySettingsResponse } from '../api/types'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

/**
 * 安全设置（需求 §八 系统管理 → 安全设置）。
 *
 * ★ 这一页**展示真实生效的策略**，不用编造的"安全评分"凑数。
 *   每条策略都标明来源（哪个文件的哪个常量），可以核对。
 * ★ V1 不提供在线修改这些参数（走 .env），
 *   但「修改我的口令」是**可以自助完成的** —— 这是本页唯一的写操作，也是真需求。
 */
export default function SecuritySettingsPage() {
  const [data, setData] = useState<SecuritySettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const [pwdOpen, setPwdOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    try {
      setData(await apiGet<SecuritySettingsResponse>('/api/settings/security'))
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

  const submitPassword = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const res = await apiPost<{ message: string }>('/api/auth/change-password', {
        old_password: v.old_password,
        new_password: v.new_password,
      })
      message.success(res.message, 6)
      setPwdOpen(false)
      form.resetFields()
      // ★ 改完所有令牌失效（含当前会话），提示用户重新登录
      setTimeout(() => {
        message.info('请重新登录', 5)
      }, 1200)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '修改失败')
    } finally {
      setSaving(false)
    }
  }

  const failedChecks = (data?.checks ?? []).filter((c) => !c.passed)

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            安全设置
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            展示当前真实生效的安全策略；可自助修改自己的口令
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<KeyOutlined />} onClick={() => setPwdOpen(true)}>
              修改我的口令
            </Button>
          </Space>
        </Col>
      </Row>

      {data && <Alert type="info" showIcon message="这一页说明" description={data.note} />}

      {/* 自检 —— 有问题时显著提示 */}
      {failedChecks.length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<ExclamationCircleOutlined />}
          message={`发现 ${failedChecks.length} 项需要处理`}
          description={
            <Space orientation="vertical" size={4}>
              {failedChecks.map((c) => (
                <Text key={c.key} style={{ fontSize: 12.5 }}>
                  · {c.label}：{c.detail}
                </Text>
              ))}
            </Space>
          }
        />
      )}

      {/* 当前会话 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <UserOutlined />
            <span>当前登录</span>
          </Space>
        }
      >
        {data ? (
          <Descriptions column={2} size="small">
            <Descriptions.Item label="账号">
              {data.session.display_name}（{data.session.username}）
            </Descriptions.Item>
            <Descriptions.Item label="角色">
              <Tag color={data.session.role === 'ADMIN' ? 'red' : 'blue'}>
                {data.session.role === 'ADMIN' ? '管理员' : '成员'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="最近登录">{stamp(data.session.last_login_at)}</Descriptions.Item>
            <Descriptions.Item label="令牌有效期">
              到期需重新登录（V1 不做自动续期）
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Text type="secondary">{loading ? '加载中…' : '—'}</Text>
        )}
      </Card>

      {/* 自检清单 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <CheckCircleOutlined />
            <span>安全自检</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              只做能真实判断的检查，不给"安全评分"
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.checks.length ?? 0) ? 0 : 24 } }}
      >
        {(data?.checks.length ?? 0) === 0 ? (
          <Text type="secondary">{loading ? '加载中…' : '—'}</Text>
        ) : (
          <Table
            rowKey="key"
            size="middle"
            pagination={false}
            dataSource={data?.checks ?? []}
            columns={[
              {
                title: '检查项',
                dataIndex: 'label',
                width: 280,
                render: (v: string) => <Text>{v}</Text>,
              },
              {
                title: '结果',
                dataIndex: 'passed',
                width: 100,
                render: (v: boolean) =>
                  v ? (
                    <Space size={4}>
                      <CheckCircleOutlined style={{ color: '#16A34A' }} />
                      <Text style={{ color: '#16A34A' }}>通过</Text>
                    </Space>
                  ) : (
                    <Space size={4}>
                      <CloseCircleOutlined style={{ color: '#DC2626' }} />
                      <Text style={{ color: '#DC2626' }}>未通过</Text>
                    </Space>
                  ),
              },
              {
                title: '说明',
                dataIndex: 'detail',
                render: (v: string) => <Text style={{ fontSize: 12.5 }}>{v}</Text>,
              },
            ]}
          />
        )}
      </Card>

      {/* 策略清单 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <LockOutlined />
            <span>当前生效的安全策略</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              每条都标明来源，可在源码中核对
            </Text>
          </Space>
        }
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {(data?.policies ?? []).map((p) => (
            <Card
              key={p.key}
              size="small"
              variant="outlined"
              styles={{ body: { padding: 14 } }}
              style={{
                borderLeft: `3px solid ${p.level === 'attention' ? '#F59E0B' : '#2563EB'}`,
              }}
            >
              <Space size={8} style={{ marginBottom: 6 }}>
                <Text strong>{p.label}</Text>
                {p.level === 'attention' && <Tag color="gold">有取舍</Tag>}
              </Space>
              <div style={{ fontSize: 13, lineHeight: 1.7 }}>{p.value}</div>
              {p.note && (
                <div style={{ marginTop: 6 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {p.note}
                  </Text>
                </div>
              )}
              <div style={{ marginTop: 6 }}>
                <Tooltip title="这个值来自哪里">
                  <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                    来源：{p.source}
                  </Text>
                </Tooltip>
              </div>
            </Card>
          ))}
        </Space>
      </Card>

      {/* 修改口令 */}
      <Modal
        title="修改我的口令"
        open={pwdOpen}
        onCancel={() => setPwdOpen(false)}
        onOk={() => void submitPassword()}
        confirmLoading={saving}
        okText="修改"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="old_password"
            label="当前口令"
            rules={[{ required: true, message: '请输入当前口令' }]}
            extra="需要验证当前口令，防止令牌被盗后直接改掉口令把你锁在门外"
          >
            <Input.Password placeholder="当前口令" autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新口令"
            rules={[
              { required: true, message: '请输入新口令' },
              { min: 8, message: '至少 8 位' },
            ]}
          >
            <Input.Password placeholder="至少 8 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新口令"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再输一次' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) return Promise.resolve()
                  return Promise.reject(new Error('两次输入不一致'))
                },
              }),
            ]}
          >
            <Input.Password placeholder="再输一次" autoComplete="new-password" />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="改完需要重新登录"
            description="修改口令会让该账号已发出的所有登录令牌立即失效（含当前会话）—— 这是安全设计，不是故障。"
          />
        </Form>
      </Modal>
    </Space>
  )
}
