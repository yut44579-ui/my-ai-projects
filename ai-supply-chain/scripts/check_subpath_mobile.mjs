/**
 * 子路径方案手机端验证（TASK-044）。
 * ★ 验的是用户真正关心的那条路：
 *   手机打开作品集 → 点「进入在线系统」→ 真的进得了系统
 * 全程只用 8081 这一个端口。
 */
import { launchEdge } from './lib/cdp.mjs'

const SITE = 'http://43.134.58.30:8081'
const APP = SITE + '/demo/supply'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9390,
  windowSize: '390,844',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  })
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })

  console.log('① 作品集详情页：按钮指向')
  await cdp.send('Page.navigate', { url: SITE + '/projects/ai-supply-chain.html' })
  await new Promise((r) => setTimeout(r, 4000))
  const d = await cdp.eval(`(() => {
    const btn = [...document.querySelectorAll('a')].find(a => a.innerText.includes('进入在线系统'))
    const links = [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
    return {
      href: btn ? btn.getAttribute('href') : null,
      to8082: links.filter(h => h.includes(':8082')).length,
      toSub: links.filter(h => h.includes('/demo/supply')).length,
      dead: links.filter(h => /127\\.0\\.0\\.1|localhost/.test(h)).length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })()`)
  console.log('   按钮 href        :', d.href)
  console.log('   指向子路径的链接 :', d.toSub, '个')
  console.log('   还指向旧 8082 的 :', d.to8082 === 0 ? '✅ 0 个' : '❌ ' + d.to8082)
  console.log('   死链接           :', d.dead === 0 ? '✅ 无' : '❌ ' + d.dead)
  console.log('   横向溢出         :', d.overflow, 'px')
  await cdp.shot('sub_portfolio_mobile', OUT)

  console.log('\n② 直接打开子路径（= 访客点进去看到的）')
  await cdp.send('Page.navigate', { url: APP + '/' })
  await new Promise((r) => setTimeout(r, 6000))
  const app = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      url: location.href,
      path: location.pathname,
      rootHasChild: (document.getElementById('root')?.children.length ?? 0) > 0,
      hasBrand: t.includes('AI供应链'),
      hasDemoAccount: t.includes('Demo@c8986e'),
      hasLoginForm: !!document.querySelector('input[type="password"]'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      text: t.replace(/\\s+/g, ' ').slice(0, 150),
    }
  })()`)
  console.log('   地址        :', app.url)
  console.log('   React 已挂载:', app.rootHasChild ? '✅' : '❌')
  console.log('   品牌        :', app.hasBrand ? '✅' : '❌')
  console.log('   登录表单    :', app.hasLoginForm ? '✅' : '❌')
  console.log('   演示账号提示:', app.hasDemoAccount ? '✅' : '❌')
  console.log('   横向溢出    :', app.overflow, 'px')
  console.log('   文案        :', app.text)
  await cdp.shot('sub_app_mobile', OUT)

  console.log('\n③ 走子路径登录 + 问 AI')
  const login = await cdp.eval(`(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    const ins = [...document.querySelectorAll('input')]
    if (ins.length < 2) return { err: '输入框不足' }
    setter.call(ins[0], 'admin'); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
    setter.call(ins[1], 'Demo@c8986e'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    const btn = document.querySelector('button[type="submit"]')
    if (btn) btn.click()
    await new Promise(r => setTimeout(r, 7000))
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      loggedIn: !location.pathname.endsWith('/login'),
      hasCustomer: t.includes('客户总数'),
      num: (t.match(/客户总数\\s*(\\d+)/) || [])[1] || null,
    }
  })()`)
  console.log('   登录后地址  :', login.path)
  console.log('   登录成功    :', login.loggedIn ? '✅' : '❌ ' + (login.err || ''))
  console.log('   工作台数据  :', login.hasCustomer ? '✅ 客户总数=' + login.num : '❌')
  await cdp.shot('sub_workbench_mobile', OUT)

  await cdp.send('Page.navigate', { url: APP + '/ai-assistant' })
  await new Promise((r) => setTimeout(r, 4000))
  const before = await cdp.eval('document.body.innerText.length')
  await cdp.eval(`(() => {
    const input = document.querySelector('input[placeholder*="问题"]') || document.querySelector('input')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, '今天有哪些客户需要我处理？')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 800))
  await cdp.eval(`(() => {
    const input = document.querySelector('input[placeholder*="问题"]') || document.querySelector('input')
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 18000))
  const ai = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    const m = t.match(/(今天有[^。]{0,50}。)/)
    return { grew: t.length > ${before}, answer: m ? m[0] : '(未截到成句回答)', len: t.length }
  })()`)
  console.log('   AI 有回答   :', ai.grew ? '✅' : '❌', '|', ai.answer)
  await cdp.shot('sub_ai_mobile', OUT)
} finally {
  await close()
}
