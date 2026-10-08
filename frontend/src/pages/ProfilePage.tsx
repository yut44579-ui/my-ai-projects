import {
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  KeyOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  message,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPatch, apiPost, apiPostForm } from '../api/client'
import type {
  AiProviderItem,
  AiProviderListResponse,
  AiProviderTestResponse,
  AvatarLimits,
  PresenceRulesResponse,
  PresenceStatus,
  ProfileMe,
  ThemePreference,
} from '../api/types'
import { PRESENCE_OPTIONS, PROTOCOL_LABELS } from '../api/types'
import { useTheme } from '../providers/ThemeProvider'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

const presenceColor = (p: string) =>
  PRESENCE_OPTIONS.find((o) => o.value === p)?.color ?? '#9CA3AF'

/**
 * 个人中心（TASK-037）。
 *
 * 四块：基本资料 / 账号安全 / 偏好设置 / AI 设置。
 *
 * ★ 设计取舍：**不做"保存全部"按钮**，每块各自保存。
 *   资料、口令、主题、AI 配置的失败原因各不相同，
 *   合成一个"保存"会让用户不知道是哪一项没成功。
 */
export default function ProfilePage() {
  const { preference, setPreference, profile, applyProfile, reloadProfile } = useTheme()

  const [limits, setLimits] = useState<AvatarLimits | null>(null)
  // ★ TASK-040：判定规则（界面原文展示，让用户能核对"为什么我是忙碌"）
  //   与手动覆盖时长
  const [rules, setRules] = useState<PresenceRulesResponse | null>(null)
  const [overrideMinutes, setOverrideMinutes] = useState(60)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [form] = Form.useForm()
  const [pwdForm] = Form.useForm()

  const [providers, setProviders] = useState<AiProviderListResponse | null>(null)
  const [testingKind, setTestingKind] = useState<string | null>(null)
  const [editKind, setEditKind] = useState<string | null>(null)
  const [providerForm] = Form.useForm()

  const load = useCallback(async () => {
    await reloadProfile()
    try {
      const [l, pr, p] = await Promise.all([
        apiGet<AvatarLimits>('/api/profile/avatar-limits'),
        apiGet<PresenceRulesResponse>('/api/profile/presence-rules'),
        apiGet<AiProviderListResponse>('/api/profile/ai-providers'),
      ])
      setLimits(l)
      setRules(pr)
      setProviders(p)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '加载失败')
    }
  }, [reloadProfile])

  useEffect(() => {
    void load()
  }, [load])

  // 资料回填（避免每次进页面都空表单）
  useEffect(() => {
    if (profile) {
      form.setFieldsValue({
        display_name: profile.display_name,
        email: profile.email ?? '',
        phone: profile.phone ?? '',
        bio: profile.bio ?? '',
      })
    }
  }, [profile, form])

  // ── 资料 ──
  const saveProfile = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      applyProfile(await apiPatch<ProfileMe>('/api/profile/me', v))
      message.success('资料已保存')
    } catch (err) {
      message.error((err as { message?: string }).message ?? '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // ── 头像 ──
  const beforeUpload = (file: File) => {
    // ★ 前端只做"减少无谓请求"的粗筛；**真正的判定在后端按文件魔数做**
    //   （扩展名与 MIME 都能伪造，不能作为安全依据）
    if (limits && file.size > limits.max_bytes) {
      message.error(`图片过大（${(file.size / 1024 / 1024).toFixed(1)} MB），上限 ${limits.max_mb} MB`)
      return Upload.LIST_IGNORE
    }
    void doUpload(file)
    return false
  }

  const doUpload = async (file: File) => {
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      // ★ 用 apiPostForm：它会**不设 Content-Type**，交给浏览器自动带 multipart boundary。
      //   自己设成 multipart/form-data 会缺 boundary，后端直接解析失败。
      const res = await apiPostForm<{ profile: ProfileMe; message: string }>(
        '/api/profile/avatar',
        fd,
      )
      applyProfile(res.profile)
      message.success(res.message)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const removeAvatar = async () => {
    try {
      applyProfile(await apiDelete<ProfileMe>('/api/profile/avatar'))
      message.success('头像已移除')
    } catch (err) {
      message.error((err as { message?: string }).message ?? '移除失败')
    }
  }

  // ── 口令 ──
  const changePassword = async () => {
    const v = await pwdForm.validateFields()
    setSaving(true)
    try {
      const res = await apiPost<{ message: string }>('/api/auth/change-password', {
        old_password: v.old_password,
        new_password: v.new_password,
      })
      message.success(res.message, 6)
      pwdForm.resetFields()
    } catch (err) {
      message.error((err as { message?: string }).message ?? '修改失败')
    } finally {
      setSaving(false)
    }
  }

  // ── 状态（TASK-040：自动推算 + 可选手动覆盖）──
  const overridePresence = async (p: PresenceStatus) => {
    try {
      applyProfile(
        await apiPost<ProfileMe>('/api/profile/presence', {
          presence: p,
          minutes: overrideMinutes,
        }),
      )
      message.success(`已覆盖为「${p === 'ONLINE' ? '在线' : p === 'BUSY' ? '忙碌' : '离线'}」，${overrideMinutes} 分钟后自动恢复`)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '设置失败')
    }
  }

  const cancelOverride = async () => {
    try {
      applyProfile(await apiDelete<ProfileMe>('/api/profile/presence'))
      message.success('已取消覆盖，恢复为系统推算')
    } catch (err) {
      message.error((err as { message?: string }).message ?? '取消失败')
    }
  }

  // ── AI 供应商 ──
  const saveProvider = async (kind: string) => {
    const v = await providerForm.validateFields()
    setSaving(true)
    try {
      await apiPatch<AiProviderItem>(`/api/profile/ai-providers/${kind}`, {
        base_url: v.base_url,
        model: v.model,
        // ★ 留空 = 不改动（传 undefined 而不是空串，否则会把已有 key 清掉）
        api_key: v.api_key ? v.api_key : undefined,
      })
      message.success('已保存')
      setEditKind(null)
      providerForm.resetFields()
      await load()
    } catch (err) {
      message.error((err as { message?: string }).message ?? '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const activateProvider = async (kind: string) => {
    try {
      await apiPost(`/api/profile/ai-providers/${kind}/activate`, {})
      message.success('已切换生效供应商')
      await load()
    } catch (err) {
      message.error((err as { message?: string }).message ?? '切换失败')
    }
  }

  const testProvider = async (kind: string) => {
    setTestingKind(kind)
    try {
      const res = await apiPost<AiProviderTestResponse>(
        `/api/profile/ai-providers/${kind}/test`,
        {},
      )
      if (res.ok) message.success(`${res.message}（${res.elapsed_ms}ms）`, 6)
      else message.error(`${res.message}（${res.elapsed_ms}ms）`, 8)
      await load()
    } catch (err) {
      message.error((err as { message?: string }).message ?? '测试失败')
    } finally {
      setTestingKind(null)
    }
  }

  const clearKey = async (kind: string) => {
    try {
      // ★ 传空串 = 明确清空（与"留空=不改"区分开）
      await apiPatch<AiProviderItem>(`/api/profile/ai-providers/${kind}`, { api_key: '' })
      message.success('已清除该供应商的 API Key')
      await load()
    } catch (err) {
      message.error((err as { message?: string }).message ?? '清除失败')
    }
  }

  if (!profile) {
    return (
      <Card variant="outlined" loading>
        <div style={{ height: 200 }} />
      </Card>
    )
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            个人中心
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            头像、资料、口令、界面偏好与 AI 模型都在这里设置
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Col>
      </Row>

      {/* ───────── 基本资料 ───────── */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <UserOutlined />
            <span>基本资料</span>
          </Space>
        }
      >
        <Row gutter={28}>
          <Col xs={24} md={7}>
            <Space orientation="vertical" size={12} style={{ width: '100%' }} align="center">
              <Avatar
                size={104}
                src={profile.avatar_url ?? undefined}
                style={{ background: '#2563EB', fontSize: 36 }}
              >
                {profile.display_name.slice(0, 1)}
              </Avatar>

              <Space size={8}>
                <Upload
                  accept="image/png,image/jpeg,image/webp"
                  showUploadList={false}
                  beforeUpload={beforeUpload}
                >
                  <Button icon={<UploadOutlined />} loading={uploading}>
                    上传头像
                  </Button>
                </Upload>
                {profile.avatar_url && (
                  <Button icon={<DeleteOutlined />} onClick={() => void removeAvatar()}>
                    移除
                  </Button>
                )}
              </Space>

              {limits && (
                <Text type="secondary" style={{ fontSize: 11, textAlign: 'center' }}>
                  支持 {limits.allowed_types.join(' / ')}，不超过 {limits.max_mb} MB。
                  <br />
                  {limits.note}
                </Text>
              )}
            </Space>
          </Col>

          <Col xs={24} md={17}>
            <Form form={form} layout="vertical">
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="登录名" extra="登录凭据的一部分，需由管理员修改">
                    <Input value={profile.username} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="角色">
                    <Input value={profile.role_label} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item
                name="display_name"
                label="显示名"
                rules={[{ required: true, message: '显示名不能为空' }]}
              >
                <Input placeholder="同事看到的名字" />
              </Form.Item>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="email" label="邮箱">
                    <Input placeholder="选填" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="phone" label="电话">
                    <Input placeholder="选填" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="bio" label="简介">
                <Input.TextArea rows={2} placeholder="选填，如负责的区域或业务" maxLength={300} />
              </Form.Item>
              <Button type="primary" loading={saving} onClick={() => void saveProfile()}>
                保存资料
              </Button>
            </Form>
          </Col>
        </Row>
      </Card>

      {/* ───────── 我的状态（TASK-040：系统自动推算）───────── */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <span>我的状态</span>
            <Tag color={presenceColor(profile.presence)}>{profile.presence_label}</Tag>
            {profile.presence_is_override && <Tag color="orange">手动覆盖中</Tag>}
          </Space>
        }
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {/* ★ 当前状态的**依据**：只给一个颜色点，用户看到自己"忙碌"会以为是 bug */}
          <Alert
            type={profile.presence_is_override ? 'warning' : 'info'}
            showIcon
            message={
              <Space size={8}>
                <span>当前：{profile.presence_label}</span>
                <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
                  {profile.presence_reason}
                </Text>
              </Space>
            }
            description={
              profile.presence_is_override
                ? '这是你手动覆盖的状态，到期会自动恢复为系统推算。也可以立即取消。'
                : '状态由系统按你的实际使用情况自动推算，无需手动设置，会自己变化。'
            }
          />

          {/* 判定规则：原文展示，可核对 */}
          <div
            style={{
              padding: '12px 14px',
              background: 'rgba(0,0,0,0.02)',
              border: '1px solid rgba(0,0,0,0.06)',
              borderRadius: 8,
            }}
          >
            <Text strong style={{ fontSize: 13 }}>
              判定规则
            </Text>
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(rules?.rules ?? []).map((r) => (
                <div key={r.status} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: presenceColor(r.status),
                      flex: '0 0 auto',
                    }}
                  />
                  <Text style={{ fontSize: 12.5, minWidth: 34 }}>{r.label}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {r.rule}
                  </Text>
                </div>
              ))}
            </div>
            {rules && (
              <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 8 }}>
                心跳每 {rules.heartbeat_interval_seconds} 秒一次；
                {rules.offline_after_seconds} 秒无心跳判离线；
                {Math.round(rules.idle_after_seconds / 60)} 分钟无操作判忙碌。{rules.note}
              </Text>
            )}
          </div>

          {/* 手动覆盖（带到期时间）*/}
          <div>
            <Text strong style={{ fontSize: 13 }}>
              临时覆盖（可选）
            </Text>
            <div style={{ marginTop: 6, marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                系统无法知道你是不是在开会/外出，只能观察到「页面开着但没动静」。
                这种情况可以手动覆盖一段时间，到期自动恢复。
              </Text>
            </div>
            <Space size={8} wrap>
              {PRESENCE_OPTIONS.map((o) => (
                <Button
                  key={o.value}
                  type={
                    profile.presence_is_override && profile.presence === o.value
                      ? 'primary'
                      : 'default'
                  }
                  onClick={() => void overridePresence(o.value)}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background:
                        profile.presence_is_override && profile.presence === o.value
                          ? '#fff'
                          : o.color,
                      marginRight: 6,
                    }}
                  />
                  设为{o.label}
                </Button>
              ))}
              <Select
                value={overrideMinutes}
                onChange={setOverrideMinutes}
                style={{ width: 108 }}
                options={(rules?.override_choices_minutes ?? [30, 60, 180]).map((m) => ({
                  value: m,
                  label: `${m} 分钟`,
                }))}
              />
              {profile.presence_is_override && (
                <Button danger onClick={() => void cancelOverride()}>
                  取消覆盖
                </Button>
              )}
            </Space>
          </div>
        </Space>
      </Card>

      {/* ───────── 账号安全 ───────── */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <SafetyCertificateOutlined />
            <span>账号安全</span>
          </Space>
        }
      >
        <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
          <Descriptions.Item label="最近登录">{stamp(profile.last_login_at)}</Descriptions.Item>
          <Descriptions.Item label="账号创建">{stamp(profile.created_at)}</Descriptions.Item>
        </Descriptions>
        <Divider style={{ margin: '0 0 16px' }} />
        <Form form={pwdForm} layout="vertical" style={{ maxWidth: 420 }}>
          <Form.Item
            name="old_password"
            label="当前口令"
            rules={[{ required: true, message: '请输入当前口令' }]}
            extra="需要验证当前口令，防止令牌被盗后直接改掉口令把你锁在门外"
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新口令"
            rules={[
              { required: true, message: '请输入新口令' },
              { min: 8, message: '至少 8 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
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
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="改完需要重新登录"
            description="修改口令会让该账号已发出的所有登录令牌立即失效（含当前会话）—— 这是安全设计，不是故障。"
          />
          <Button type="primary" loading={saving} onClick={() => void changePassword()}>
            修改口令
          </Button>
        </Form>
      </Card>

      {/* ───────── 偏好设置 ───────── */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <BulbOutlined />
            <span>偏好设置</span>
          </Space>
        }
      >
        <Space orientation="vertical" size={8} style={{ width: '100%' }}>
          <Text strong style={{ fontSize: 13 }}>
            界面主题
          </Text>
          <Segmented
            value={preference}
            onChange={(v) => void setPreference(v as ThemePreference)}
            options={[
              { label: '跟随系统', value: 'SYSTEM' },
              { label: '浅色', value: 'LIGHT' },
              { label: '深色', value: 'DARK' },
            ]}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            偏好存在你的账号上，换设备登录也一致。「跟随系统」会随操作系统的深浅色自动切换。
          </Text>
        </Space>
      </Card>

      {/* ───────── AI 设置 ───────── */}
      <Card
        variant="outlined"
        title={
          // ★ TASK-043 移动端：卡片标题在窄屏要能换行，否则那个
          //   「当前生效：DEEPSEEK（来自 .env 兜底）」标签 231px 会顶出屏幕。
          <Space size={8} wrap>
            <RobotOutlined />
            <span>AI 模型设置</span>
            {providers?.active_kind && (
              <Tag color={providers.active_source === 'DATABASE' ? 'green' : 'gold'}>
                当前生效：{providers.active_kind}
                {providers.active_source === 'ENV' ? '（来自 .env 兜底）' : ''}
              </Tag>
            )}
          </Space>
        }
        styles={{ body: { padding: 0 } }}
      >
        <div style={{ padding: '14px 18px' }}>
          <Alert type="info" showIcon message="说明" description={providers?.note} />
        </div>
        <Table<AiProviderItem>
          rowKey="kind"
          size="middle"
          pagination={false}
          loading={!providers}
          dataSource={providers?.items ?? []}
          // ★ TASK-043 移动端：这张表 6 列、列宽合计 578px，375px 屏上
          //   会把整页撑到 591px（横向溢出）。scroll.x 让表格**在容器内
          //   横向滚动**，而不是撑宽整页 —— 这是 AntD 表格在窄屏的标准做法。
          scroll={{ x: 'max-content' }}
          expandable={{
            expandedRowRender: (r) => (
              <Form
                form={editKind === r.kind ? providerForm : undefined}
                layout="vertical"
                style={{ maxWidth: 560 }}
                initialValues={{ base_url: r.base_url, model: r.model }}
              >
                <Form.Item name="base_url" label="接口地址 (Base URL)">
                  <Input placeholder="如 https://api.deepseek.com" />
                </Form.Item>
                <Form.Item
                  name="model"
                  label="模型名"
                  extra="各家模型名不同且会更新，请以官方文档为准"
                >
                  <Input placeholder="如 deepseek-chat" />
                </Form.Item>
                <Form.Item
                  name="api_key"
                  label="API Key"
                  extra="加密存储、接口永不回显。留空表示不改动；要清空请用右侧「清除 Key」。"
                >
                  <Input.Password placeholder={r.has_key ? '已配置（留空则不改动）' : '未配置'} />
                </Form.Item>
                <Space>
                  <Button type="primary" loading={saving} onClick={() => void saveProvider(r.kind)}>
                    保存
                  </Button>
                  <Button onClick={() => setEditKind(null)}>收起</Button>
                  {r.docs && (
                    <Button type="link" href={r.docs} target="_blank" rel="noreferrer">
                      官方文档
                    </Button>
                  )}
                </Space>
              </Form>
            ),
            expandedRowKeys: editKind ? [editKind] : [],
            onExpand: (expanded, r) => {
              setEditKind(expanded ? r.kind : null)
              providerForm.setFieldsValue({ base_url: r.base_url, model: r.model, api_key: '' })
            },
            expandIcon: ({ expanded, onExpand, record }) => (
              <Tooltip title={expanded ? '收起' : '配置'}>
                <Button
                  type="link"
                  size="small"
                  onClick={(e) => onExpand(record, e)}
                >
                  {expanded ? '收起' : '配置'}
                </Button>
              </Tooltip>
            ),
          }}
          columns={[
            {
              title: '供应商',
              dataIndex: 'label',
              render: (v: string, r) => (
                <Space size={6}>
                  <Text strong>{v}</Text>
                  {r.is_active && <Tag color="green">生效中</Tag>}
                </Space>
              ),
            },
            {
              title: '协议',
              dataIndex: 'protocol',
              width: 130,
              render: (v: string) => (
                <Tooltip title="请求格式不同，系统按协议适配">
                  <Tag color="blue">{PROTOCOL_LABELS[v] ?? v}</Tag>
                </Tooltip>
              ),
            },
            { title: '模型', dataIndex: 'model', width: 190, render: (v: string) => v || '—' },
            {
              title: 'API Key',
              dataIndex: 'has_key',
              width: 110,
              render: (v: boolean, r) =>
                v ? (
                  <Space size={4}>
                    <CheckCircleOutlined style={{ color: '#16A34A' }} />
                    <Text style={{ fontSize: 12 }}>已配置</Text>
                    <Tooltip title="清除该 Key">
                      <Button
                        type="link"
                        size="small"
                        danger
                        onClick={() => void clearKey(r.kind)}
                      >
                        清除
                      </Button>
                    </Tooltip>
                  </Space>
                ) : (
                  <Space size={4}>
                    <CloseCircleOutlined style={{ color: '#9CA3AF' }} />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      未配置
                    </Text>
                  </Space>
                ),
            },
            {
              title: '最近测试',
              dataIndex: 'last_test_at',
              width: 150,
              render: (v: string | null, r) =>
                v ? (
                  <Tooltip title={r.last_error ?? '测试通过'}>
                    <Tag color={r.last_test_ok ? 'green' : 'red'}>
                      {r.last_test_ok ? '通过' : '失败'}
                    </Tag>
                    <div>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {stamp(v)}
                      </Text>
                    </div>
                  </Tooltip>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    未测试
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
                    type="link"
                    size="small"
                    loading={testingKind === r.kind}
                    onClick={() => void testProvider(r.kind)}
                  >
                    测试
                  </Button>
                  <Tooltip title={r.is_active ? '已是当前生效' : '切换为当前生效'}>
                    <Button
                      type="link"
                      size="small"
                      disabled={r.is_active || !r.has_key}
                      onClick={() => void activateProvider(r.kind)}
                    >
                      设为生效
                    </Button>
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
        <div style={{ padding: '12px 18px' }}>
          <Alert
            type="warning"
            showIcon
            icon={<KeyOutlined />}
            message="Key 的安全边界"
            description="API Key 在服务端加密存储，接口永不回显。但它保护的是「数据库被拖走」这种情况 —— 能读到服务器密钥文件的人仍然能解出 Key，请妥善保管服务器。"
          />
        </div>
      </Card>
    </Space>
  )
}
