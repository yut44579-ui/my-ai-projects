/**
 * 手机视口验证（TASK-043）。
 *
 * ★ 为什么必须单独测：桌面断言全过也说明不了手机可用 ——
 *   侧栏原来固定 248px，375px 屏上只剩 127px，桌面测永远发现不了。
 *   这里用真实的移动端视口 + 触摸模拟。
 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'
const PAGES = [
  ['/', '工作台'],
  ['/customers', '客户管理'],
  ['/reports', '报告'],
  ['/ai-assistant', 'AI助手'],
  ['/profile', '个人中心'],
]

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9350,
  windowSize: '375,812',
})

const results = []
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })

  // ★ 必须先注入登录令牌，否则路由守卫把所有页面送到登录页 ——
  //   那样量到的 .ant-layout-sider 全是 null，会误判成"问题"。
  const testToken = process.env.DSH_TEST_TOKEN
  if (testToken) {
    await cdp.send('Page.navigate', { url: BASE + '/login' })
    await new Promise((r) => setTimeout(r, 2500))
    const api = process.env.DSH_API_BASE ?? 'http://127.0.0.1:8000'
    const meRes = await fetch(`${api}/api/auth/me`, {
      headers: { Authorization: `Bearer ${testToken}` },
    })
    const me = meRes.ok ? await meRes.json() : {}
    await cdp.eval(
      `localStorage.setItem('auth_token', ${JSON.stringify(testToken)});` +
        `localStorage.setItem('auth_user', ${JSON.stringify(JSON.stringify(me))}); true`,
    )
    console.log(`   （已注入登录令牌：${me.display_name ?? '?'}）`)
  }

  // 关键：用 Emulation 强制移动端视口 + 触摸，而不是只把窗口调小
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375,
    height: 812,
    deviceScaleFactor: 2,
    mobile: true,
  })
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })

  for (const [path, label] of PAGES) {
    await cdp.send('Page.navigate', { url: BASE + path })
    await new Promise((r) => setTimeout(r, 3000))

    const info = await cdp.eval(`(() => {
      const doc = document.documentElement
      const sider = document.querySelector('.ant-layout-sider')
      const content = document.querySelector('.ant-layout-content')
      const burger = [...document.querySelectorAll('button')]
        .find(b => (b.getAttribute('aria-label') || '').includes('导航'))
      const cRect = content ? content.getBoundingClientRect() : null
      return {
        vw: window.innerWidth,
        hOverflow: doc.scrollWidth - doc.clientWidth,
        siderWidth: sider ? Math.round(sider.getBoundingClientRect().width) : null,
        contentLeft: cRect ? Math.round(cRect.left) : null,
        contentWidth: cRect ? Math.round(cRect.width) : null,
        hasBurger: !!burger,
        bodyText: (document.body.innerText || '').slice(0, 60).replace(/\\s+/g, ' '),
      }
    })()`)

    const ok = info.hOverflow <= 1 && info.contentWidth > 300 && info.contentLeft === 0
    results.push({ label, path, ...info, ok })
    console.log(
      `  ${ok ? '✅' : '❌'} ${label.padEnd(6)} 视口${info.vw} 侧栏${info.siderWidth}px ` +
        `内容${info.contentLeft}+${info.contentWidth}px 横向溢出${info.hOverflow}px 汉堡${info.hasBurger ? '有' : '无'}`,
    )
    if (path === '/') await cdp.shot('mobile_home', OUT)
    if (path === '/ai-assistant') await cdp.shot('mobile_ai', OUT)
  }

  // 测汉堡按钮能不能叫出导航
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await new Promise((r) => setTimeout(r, 3000))
  const burgerTest = await cdp.eval(`(async () => {
    const before = Math.round(document.querySelector('.ant-layout-sider').getBoundingClientRect().width)
    const burger = [...document.querySelectorAll('button')]
      .find(b => (b.getAttribute('aria-label') || '').includes('导航'))
    if (!burger) return { before, after: -1, err: '没找到汉堡按钮' }
    burger.click()
    await new Promise(r => setTimeout(r, 700))
    const after = Math.round(document.querySelector('.ant-layout-sider').getBoundingClientRect().width)
    return { before, after }
  })()`)
  console.log(`\n  汉堡按钮：收起时 ${burgerTest.before}px → 点击后 ${burgerTest.after}px ` +
    `${burgerTest.after > 100 ? '✅ 能叫出导航' : '❌ 没反应'}`)
  await cdp.shot('mobile_menu_open', OUT)

  console.log('\n' + '='.repeat(58))
  const bad = results.filter((r) => !r.ok)
  console.log(bad.length ? `❌ ${bad.length}/${results.length} 个页面手机端有问题` : `✅ ${results.length} 个页面手机端全部正常`)
} finally {
  await close()
}
