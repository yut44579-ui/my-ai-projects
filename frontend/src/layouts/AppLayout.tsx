import { Layout, Menu, Typography } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { PRIMARY_COLOR } from '../theme'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

/** 左侧导航：客户 / 汇报。汇报为占位项（页面自后续 TASK 接入）。 */
const MENU_ITEMS = [
  { key: '/customers', label: '客户' },
  { key: '/reports', label: '汇报', disabled: true },
]

/** 路由 -> 顶栏标题 */
const PAGE_TITLES: Record<string, string> = {
  '/': '首页',
  '/customers': '客户',
}

export default function AppLayout() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const selectedKeys = MENU_ITEMS.some((item) => item.key === pathname)
    ? [pathname]
    : []

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        theme="light"
        width={220}
        style={{ borderRight: '1px solid #EEF0F4' }}
      >
        <div style={{ padding: '20px 20px 16px' }}>
          <Title level={5} style={{ margin: 0, color: PRIMARY_COLOR, fontWeight: 700 }}>
            AI 商业项目助理
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            骨架版 · 暂无业务数据
          </Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          items={MENU_ITEMS}
          style={{ borderInlineEnd: 'none', padding: '0 8px' }}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            borderBottom: '1px solid #EEF0F4',
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <Text strong style={{ fontSize: 15 }}>
            {PAGE_TITLES[pathname] ?? 'AI 商业项目助理'}
          </Text>
        </Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
