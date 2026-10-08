import {
  AppstoreOutlined,
  BarChartOutlined,
  BellOutlined,
  BulbOutlined,
  ContactsOutlined,
  DatabaseOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FundProjectionScreenOutlined,
  MenuOutlined,
  PieChartOutlined,
  ProjectOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { theme as antdTheme, Avatar, Button, Dropdown, Input, Layout, Menu, message, Space, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { clearAuth } from '../api/client'
import { PRESENCE_OPTIONS } from '../api/types'
import BrandLogo from '../components/BrandLogo'
import { usePresenceHeartbeat } from '../hooks/usePresenceHeartbeat'
import type { HeartbeatResponse } from '../hooks/usePresenceHeartbeat'
import { useTheme } from '../providers/ThemeProvider'
import { readCachedUser, roleLabel } from '../utils/currentUser'

const { Sider, Header, Content } = Layout
const { Text } = Typography

/** 状态圆点颜色（与个人中心共用同一份来源，避免两处不一致） */
function presenceColor(p: string | undefined): string {
  return PRESENCE_OPTIONS.find((o) => o.value === p)?.color ?? '#9CA3AF'
}

/** 侧栏宽度固定（参考图约 14%~15% 视口宽度；固定像素不随内容变化） */
export const SIDER_WIDTH = 248

/**
 * 左侧导航：严格按参考图的信息层级。
 *
 * ★ 未实现的模块一律 `disabled` —— 不做点进去是空白/假数据的假入口。
 * ★ 每个置灰项都写清**为什么没做**（把鼠标放上去能看到原因）：
 *   只灰不给理由，用户会以为是坏了。
 */
const NAV = [
  { key: '/', icon: <AppstoreOutlined />, label: '工作台' },
  { key: '/customers', icon: <TeamOutlined />, label: '客户管理' },
  { key: '/opportunities', icon: <FundProjectionScreenOutlined />, label: '商机管理' },
  { key: '/projects', icon: <ProjectOutlined />, label: '项目管理' },
  { key: '/marketing', icon: <ThunderboltOutlined />, label: '营销与内容' },
  { key: '/researches', icon: <FileSearchOutlined />, label: '获客研究' },
  { type: 'divider' as const },
  { key: '/research', icon: <PieChartOutlined />, label: '市场研究' },
  { key: '/analytics', icon: <BarChartOutlined />, label: '商业分析' },
  { key: '/risk', icon: <SafetyCertificateOutlined />, label: '风险中心' },

  { type: 'group' as const, label: '数据中心', children: [
    { key: '/visits', icon: <EyeOutlined />, label: '客户访问' },
    { key: '/connections', icon: <DatabaseOutlined />, label: '数据连接' },
    { key: '/files', icon: <ContactsOutlined />, label: '文件资料' },
    { key: '/knowledge', icon: <BulbOutlined />, label: '知识库' },
    // ★ 「数据同步」已移除（不是置灰）：它的内容就是「数据连接」页的同步记录区块。
    //   需求 §八 列了「同步记录」，但那指的是**数据连接里要能看到同步记录**（已实现），
    //   不需要一个永远灰着的独立菜单项 —— 那只会让人以为有功能没做。
    //   ★ 注意：需求 §八 的数据中心是 数据连接/文件资料/知识库/同步记录，
    //     若将来用户明确要一个独立的「同步记录」页，再从数据连接页抽出来。
  ]},
  // ★ 按需求 §八 修正（D46）：
  //   · 「AI助手」是 §八 列出的主导航项，此前被我删掉了 —— 现补回。
  //     它的实际功能在工作台右栏（TASK-013），所以本项**指向工作台**，
  //     不另建一个和右栏重复的页面（§四 不重复造）。
  //   · 「智能分析」已删除：它是我自己起的名，且与「商业分析」（/analytics）
  //     职责重复 —— 需求全文只有「商业分析」，没有「智能分析」「数据分析」。
  { type: 'group' as const, label: 'AI 能力', children: [
    { key: '/ai-assistant', icon: <RobotOutlined />, label: 'AI助手' },
    { key: '/ai/business', icon: <PieChartOutlined />, label: '商业洞察' },
  ]},
  { type: 'group' as const, label: '输出中心', children: [
    { key: '/reports', icon: <FileTextOutlined />, label: '报告' },
    { key: '/plans', icon: <FileTextOutlined />, label: '方案' },
    { key: '/contents', icon: <FileTextOutlined />, label: '内容' },
  ]},
  { type: 'group' as const, label: '系统管理', children: [
    // ★ 个人中心放最前：它改的是"我自己"，权限与影响范围都在自己身上，
    //   放在「用户与权限」（改别人）之前更符合从近到远的顺序。
    { key: '/profile', icon: <UserOutlined />, label: '个人中心' },
    { key: '/settings/users', icon: <TeamOutlined />, label: '用户与权限' },
    { key: '/settings/security', icon: <SafetyCertificateOutlined />, label: '安全设置' },
    { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
  ]},
]

/**
 * 置灰项的**真实原因**（hover 可见）。
 *
 * ★ 内容必须准确：说"接口未就绪"但其实接口能用，就是误导。
 *   例如「报告」此前写"待接入"，实际后端 4 个端点早就 HTTP 200 —— 那是低估。
 * ★ 更常见的毛病是**过时**：模块做完了、置灰项放开了，原因文案却还留在表里。
 *   所以本表**只保留当前真正置灰的项**；已启用的项一律删掉，
 *   避免"读代码的人以为没做"或将来重新置灰时显示错误原因。
 *
 * ★★ 现在这张表是**空的** —— 侧栏所有菜单项都已实现并放开。
 *   如果有意保留一个空表：将来新增置灰项时直接往里加，不用改结构；
 *   而且它是空的这件事本身就是"没有占位功能"的证据。
 * ★ 曾经从导航移除（不是置灰）的项：「数据同步」——
 *   它的内容就是「数据连接」页的同步记录区块，留个永远灰着的菜单项只会让人以为有功能没做。
 */
const NAV_REASONS: Record<string, string> = {}

const PAGE_TITLES: Record<string, string> = {
  '/': '工作台',
  '/customers': '客户管理',
  '/opportunities': '商机管理',
  '/projects': '项目管理',
  '/visits': '客户访问',
  '/research': '市场研究',
  '/analytics': '商业分析',
  '/ai-assistant': 'AI助手',
  '/reports': '报告',
  '/connections': '数据连接',
  '/knowledge': '知识库',
  '/profile': '个人中心',
  '/files': '文件资料',
  '/plans': '方案',
  '/ai/business': '商业洞察',
  '/settings/security': '安全设置',
  '/settings': '系统设置',
  '/settings/users': '用户与权限',
  '/researches': '获客研究',
  '/marketing': '营销与内容',
  '/contents': '营销与内容',
  '/risk': '风险中心',
}

/**
 * 给置灰项挂上"为什么没做"的 hover 提示（递归处理分组）。
 *
 * ★ 只在 disabled 且确实写了原因时包 Tooltip；
 *   已实现的项不加，避免无意义的悬停框。
 */
function withReasons(items: unknown[]): any[] {
  return items.map((raw) => {
    const item = raw as Record<string, any>
    if (item.children) {
      return { ...item, children: withReasons(item.children) }
    }
    const reason = NAV_REASONS[item.key as string]
    if (!item.disabled || !reason) return item
    return {
      ...item,
      label: (
        <Tooltip title={reason} placement="right" styles={{ root: { maxWidth: 320 } }}>
          <span>{item.label}</span>
        </Tooltip>
      ),
    }
  })
}

export default function AppLayout() {
  const navigate = useNavigate()
  // ★ 只读一次本地缓存用于顶栏显示；不拿它做任何鉴权判断
  const currentUser = readCachedUser()
  // ★ 顶栏的头像与状态来自 ThemeProvider 里统一拉取的资料 ——
  //   不再自己请求一遍，否则改完头像后顶栏要等下次刷新才更新
  const { profile } = useTheme()
  // ★ 用 AntD 的设计 token 取颜色，避免硬编码导致深色主题下露白
  const { token } = antdTheme.useToken()
  // ★ TASK-043 移动端：这两个状态**必须分开**，不能共用一个变量。
  //   isMobile       —— 是否处于窄屏（< AntD lg 断点）
  //   siderCollapsed —— 侧栏当前是否收起
  //   ★ 一开始我用了同一个变量，结果手机上点开菜单时 marginLeft 变成 248px，
  //     内容被挤到只剩 127px 宽，标题变成竖排。移动端菜单应该是**浮层**：
  //     侧栏盖在内容上，内容不动。
  const [isMobile, setIsMobile] = useState(false)
  const [siderCollapsed, setSiderCollapsed] = useState(false)

  // ★ TASK-040 实时状态：每 30 秒心跳一次，后端按「心跳 + 操作」推算状态。
  //   回调把最新结果同步进顶栏 —— 所以状态点是**活的**，不需要刷新页面。
  const [livePresence, setLivePresence] = useState<HeartbeatResponse | null>(null)
  usePresenceHeartbeat({ onStatus: setLivePresence })

  // ★ 优先用心跳返回的最新状态（变化反映更快），没有则用资料里的值。
  //   两者同源（都来自后端推算），不会打架。
  const liveStatus = livePresence?.status ?? profile?.presence ?? 'OFFLINE'
  const liveLabel = livePresence?.label ?? profile?.presence_label ?? '离线'
  const liveReason = livePresence?.reason ?? profile?.presence_reason ?? ''

  const { pathname } = useLocation()

  const flatKeys = NAV.flatMap((item) =>
    'children' in item && item.children ? item.children.map((c) => c.key) : 'key' in item ? [item.key] : [],
  ).filter(Boolean) as string[]

  const selectedKeys = flatKeys.filter(
    (key) => pathname === key || (key !== '/' && pathname.startsWith(`${key}/`)),
  )

  const title = pathname.startsWith('/customers/')
    ? '客户详情'
    : (PAGE_TITLES[pathname] ?? 'AI 商业项目助理')

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* ── 左侧深蓝导航 ──
          ★ TASK-043 移动端：原来侧栏是**固定 248px 且不可收起**，
            375px 的手机只剩 127px 给内容 —— 手机上根本没法用。
            现在改成：
              · 屏幕 < 992px（AntD lg 断点）时**自动收起**，收起宽度为 0
              · 顶栏出现汉堡按钮可以随时叫出来 / 收回去
              · 桌面端行为完全不变（默认展开 248px）
          ★ 用 Sider 自带的 breakpoint 而不是自己写 @media：
            自己写要同时处理 marginLeft、定位、层级，容易漏一处就错位。 */}
      <Sider
        width={SIDER_WIDTH}
        breakpoint="lg"
        collapsedWidth={0}
        collapsed={siderCollapsed}
        onBreakpoint={(broken) => {
          setIsMobile(broken)
          setSiderCollapsed(broken)
        }}
        trigger={null}
        style={{
          background: '#0F2544',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          overflow: 'auto',
          zIndex: 20,
        }}
      >
        {/* Logo 区 */}
        <div style={{ padding: '20px 20px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
          <BrandLogo size={36} />
          <div style={{ lineHeight: 1.3, minWidth: 0 }}>
            <div style={{ color: '#FFFFFF', fontWeight: 600, fontSize: 14 }}>AI供应链</div>
            <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: 11 }}>小笋供应链</div>
          </div>
        </div>

        <Menu
          mode="inline"
          theme="dark"
          selectedKeys={selectedKeys}
          items={withReasons(NAV)}
          style={{ background: 'transparent', borderInlineEnd: 'none', padding: '0 8px 24px' }}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>

      {/* ── 右侧主区（让出侧栏宽度；侧栏收起时为 0） ──
          ★ 背景色改成从 AntD token 取（token.colorBgLayout / colorBgContainer），
            不再硬编码 #F5F7FA / #FFFFFF ——
            硬编码会让深色主题下顶栏与内容区仍是白的（试过的实际效果）。 */}
      {/* ★ TASK-043 移动端：菜单是浮层，必须有个遮罩 + 点击关闭 ——
          否则用户点开菜单后没有明显的方式收回去（只能再点那个小汉堡）。
          遮罩层级放在侧栏（zIndex 20）之下、内容之上。 */}
      {isMobile && !siderCollapsed && (
        <div
          onClick={() => setSiderCollapsed(true)}
          aria-hidden="true"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.45)',
            zIndex: 19,
          }}
        />
      )}
      <Layout
        style={{
          marginLeft: isMobile || siderCollapsed ? 0 : SIDER_WIDTH,
          background: token.colorBgLayout,
          transition: 'margin-left 0.2s',
        }}
      >
        {/* 顶部全局 Header */}
        <Header
          style={{
            background: token.colorBgContainer,
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            padding: '0 24px',
            height: 60,
            lineHeight: '60px',
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          {/* ★ TASK-043 移动端：汉堡按钮。
              仅在侧栏收起时显示（手机 / 窄窗口），点一下把导航叫出来。
              放在搜索框前面，符合"左上角是导航入口"的通用习惯。 */}
          {isMobile && siderCollapsed && (
            <Button
              type="text"
              aria-label="打开导航菜单"
              icon={<MenuOutlined style={{ fontSize: 18 }} />}
              onClick={() => setSiderCollapsed(false)}
              style={{ marginLeft: -8, flex: '0 0 auto' }}
            />
          )}

          <Input
            allowClear
            prefix={<span style={{ color: '#9CA3AF' }}>🔍</span>}
            placeholder="搜索客户、公司、商机、项目、文档、知识库..."
            // ★ 手机上搜索框不再抢宽度（原来 maxWidth 520 会把右边图标挤出去）
            style={{ maxWidth: 520, minWidth: 0, flex: '1 1 auto', borderRadius: 8, background: '#F7F8FA' }}
          />

          <div
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              // ★ TASK-043 移动端：顶栏元素挨太近会挤到把用户名截断成两行。
              //   窄屏时收紧间距（18 → 10）。
              gap: isMobile ? 10 : 18,
              flex: '0 0 auto',
            }}
          >
            <span style={{ color: '#6B7280', fontSize: 16 }}>
              <BellOutlined />
            </span>
            <span style={{ color: '#6B7280', fontSize: 16 }}>
              <QuestionCircleOutlined />
            </span>
            {/* ★ TASK-043：日期很长（「2026年10月09日星期五」），
                手机上占掉大半条顶栏 —— 窄屏隐藏。日期在别处也能看到，
                而顶栏挤爆是马上就能感觉到的体验问题。 */}
            {!isMobile && (
              <Text type="secondary" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                {new Date().toLocaleDateString('zh-CN', {
                  year: 'numeric',
                  month: '2-digit',
                  day: '2-digit',
                  weekday: 'long',
                })}
              </Text>
            )}
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'whoami',
                    label: `${profile?.display_name ?? currentUser?.display_name ?? '未知用户'}（${
                      profile?.role_label ?? roleLabel(currentUser?.role)
                    }）`,
                    disabled: true,
                  },
                  { type: 'divider' },
                  { key: 'profile', label: '个人中心' },
                  /* ── 状态：★ 自动推算，不可点选（TASK-040）──
                     之前这里是「点一下切换状态」的手动菜单。
                     现在状态由系统按实际使用情况算，手动点选会与推算打架 ——
                     想覆盖请到个人中心（那里有带到期时间的手动覆盖）。 */
                  {
                    key: 'presence-live',
                    type: 'group',
                    label: '我的状态（系统自动）',
                    children: [
                      {
                        key: 'presence-live-info',
                        disabled: true,
                        label: (
                          <Space size={8} style={{ paddingRight: 8 }}>
                            <span
                              style={{
                                display: 'inline-block',
                                width: 8,
                                height: 8,
                                borderRadius: '50%',
                                background: presenceColor(liveStatus),
                                flex: '0 0 auto',
                              }}
                            />
                            <span style={{ whiteSpace: 'normal', maxWidth: 220 }}>
                              <strong>{liveLabel}</strong>
                              {liveReason && (
                                <span style={{ display: 'block', fontSize: 11, color: '#8C93A0' }}>
                                  {liveReason}
                                </span>
                              )}
                            </span>
                          </Space>
                        ),
                      },
                      { key: 'presence-goto-profile', label: '到个人中心查看规则 / 手动覆盖' },
                    ],
                  },
                  { type: 'divider' },
                  { key: 'logout', label: '退出登录' },
                ],
                onClick: ({ key }) => {
                  if (key === 'logout') {
                    clearAuth()
                    message.success('已退出登录')
                    navigate('/login', { replace: true })
                    return
                  }
                  if (key === 'profile' || key === 'presence-goto-profile') {
                    navigate('/profile')
                  }
                },
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                {/* ★ 头像右上角带状态圆点：一眼看出当前状态 */}
                <span style={{ position: 'relative', display: 'inline-flex' }}>
                  <Avatar
                    size={30}
                    src={profile?.avatar_url ?? undefined}
                    style={{ background: '#2563EB' }}
                    icon={profile?.avatar_url ? undefined : <UserOutlined />}
                  >
                    {profile?.avatar_url ? undefined : (profile?.display_name ?? '').slice(0, 1)}
                  </Avatar>
                  <span
                    title={liveLabel}
                    style={{
                      position: 'absolute',
                      right: -1,
                      bottom: -1,
                      width: 10,
                      height: 10,
                      borderRadius: '50%',
                      background: presenceColor(liveStatus),
                      // ★ 描边跟随主题底色：硬编码白色会让深色下的小圆点外圈发白
                      border: `2px solid ${token.colorBgContainer}`,
                    }}
                  />
                </span>
                {/* ★ TASK-043：窄屏隐藏姓名文字（头像已能认人，且下拉里有全名），
                    否则顶栏最后一个元素会被挤出屏幕。 */}
                {!isMobile && (
                  <Text style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                    {profile?.display_name ?? currentUser?.display_name ?? '未登录'}
                  </Text>
                )}
              </div>
            </Dropdown>
          </div>
        </Header>

        <Content style={{ padding: isMobile ? '0 12px 24px' : '0 24px 24px' }}>
          <div style={{ maxWidth: 1600, margin: '0 auto' }}>
            <div style={{ padding: '16px 0 12px' }}>
              <Text strong style={{ fontSize: 15 }}>
                {title}
              </Text>
            </div>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
