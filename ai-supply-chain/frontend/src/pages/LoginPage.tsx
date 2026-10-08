import { UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Space, Typography, message } from 'antd'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiPost, setToken, USER_KEY } from '../api/client'
import BrandLogo from '../components/BrandLogo'
import type { LoginResponse } from '../api/types'

const { Text, Title } = Typography

/**
 * 登录页（TASK-019）。规格见 docs/AUTH_SPEC.md §8。
 *
 * ★ 不做注册入口：账号由管理员创建（避免部署后被抢注管理员）。
 * ★ 忘记密码 → 找管理员重置（页面上如实说明，不给假的重置入口）。
 */
/**
 * 演示账号提示（TASK-043）。
 * ★ 来自构建期环境变量 VITE_DEMO_ACCOUNT —— 只有部署演示环境时才设。
 *   本地开发不设，登录页就不会显示"演示账号"，避免提示了一个登不进去的账号。
 */
const DEMO_ACCOUNT = (import.meta.env.VITE_DEMO_ACCOUNT as string | undefined) || ''

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (values: { username: string; password: string }) => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiPost<LoginResponse>('/api/auth/login', {
        username: values.username,
        password: values.password,
      })
      setToken(res.access_token)
      try {
        localStorage.setItem(USER_KEY, JSON.stringify(res.user))
      } catch {
        /* 缓存失败不影响登录本身 */
      }
      message.success(`欢迎回来，${res.user.display_name}`)
      // 回到被拦截前的页面（没有就回工作台）
      const from = (location.state as { from?: string } | null)?.from
      navigate(from && from !== '/login' ? from : '/', { replace: true })
    } catch (err) {
      const e = err as { message?: string; status?: number }
      setError(
        e.status === 500
          ? (e.message ?? '服务端未配置认证密钥，请联系管理员检查 AUTH_SECRET_KEY')
          : (e.message ?? '登录失败'),
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#F5F7FA',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <Card
        variant="outlined"
        style={{ width: 400, boxShadow: '0 4px 20px rgba(15,37,68,0.08)' }}
        styles={{ body: { padding: '28px 28px 24px' } }}
      >
        <Space size={12} align="center" style={{ marginBottom: 6 }}>
          {/* ★ 与侧栏用同一个品牌图标组件（TASK-034）：改一处两边同步，
              不会出现"侧栏一个样、登录页另一个样" */}
          <BrandLogo size={40} />
          <div>
            <Title level={4} style={{ margin: 0 }}>
              AI供应链
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              小笋供应链
            </Text>
          </div>
        </Space>

        {error && (
          <Alert type="error" showIcon message={error} style={{ margin: '14px 0 0' }} />
        )}

        {/* ★ TASK-043 演示环境提示。
            ★ 为什么用构建期环境变量而不是写死：
              本地开发时根本没有这个演示账号（本地是 Admin@2026!），
              写死就会出现"提示的账号登不进去"—— 那比没有提示更糟。
              只有带 VITE_DEMO_ACCOUNT 构建出来的产物才显示。 */}
        {DEMO_ACCOUNT && (
          <Alert
            type="info"
            showIcon
            style={{ margin: '14px 0 0' }}
            message="这是在线演示环境"
            description={
              <span style={{ fontSize: 12.5 }}>
                演示账号：<b>{DEMO_ACCOUNT}</b>
                <br />
                数据是演示数据；AI 接口有限流（一个 IP 每分钟 12 次）。
              </span>
            }
          />
        )}

        <Form layout="vertical" onFinish={submit} style={{ marginTop: 18 }} requiredMark={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input
              size="large"
              prefix={<UserOutlined style={{ color: '#9CA3AF' }} />}
              placeholder="用户名"
              autoComplete="username"
              autoFocus
            />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              size="large"
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Button type="primary" size="large" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>

        <div style={{ marginTop: 16 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            没有账号或忘记密码？请联系管理员创建/重置（系统不开放自助注册，避免被抢注管理员）。
          </Text>
        </div>
      </Card>
    </div>
  )
}
