/**
 * 最终验证：两个系统都在 8081 子路径下，手机能进。
 */
import { launchEdge } from './lib/cdp.mjs'

const SITE = 'http://43.134.58.30:8081'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9400,
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

  // ── 两个详情页的入口 ──
  console.log('① 作品集两个项目的入口')
  for (const slug of ['ai-supply-chain', 'sales-report-agent']) {
    await cdp.send('Page.navigate', { url: `${SITE}/projects/${slug}.html` })
    await new Promise((r) => setTimeout(r, 3500))
    const d = await cdp.eval(`(() => {
      const btn = [...document.querySelectorAll('a')].find(a => a.innerText.includes('进入在线系统'))
      const links = [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
      const t = document.body.innerText || ''
      return {
        href: btn ? btn.getAttribute('href') : null,
        hasBtn: !!btn,
        dead: links.filter(h => /127\\.0\\.0\\.1|localhost/.test(h)).length,
        accountShown: (t.match(/演示账号：([^\\n]{0,60})/) || [])[1] || null,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }
    })()`)
    console.log(`   ${slug}`)
    console.log('     按钮     :', d.hasBtn ? '✅ ' + d.href : '❌ 没有')
    console.log('     死链接   :', d.dead === 0 ? '✅ 无' : '❌ ' + d.dead)
    console.log('     账号说明 :', d.accountShown || '(登录页会显示)')
    console.log('     横向溢出 :', d.overflow, 'px')
  }
  await cdp.shot('final_sales_detail', OUT)

  // ── 销售 Agent：能打开登录页 ──
  console.log('\n② 销售 Agent（子路径）')
  await cdp.send('Page.navigate', { url: SITE + '/demo/sales/' })
  await new Promise((r) => setTimeout(r, 6000))
  const sales = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      url: location.href,
      hasCaptcha: !!document.querySelector('img[src^="data:image"], svg'),
      hasLogin: /登录|账号|密码/.test(t),
      hasDemoAcct: t.includes('Demo@2026'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      text: t.replace(/\\s+/g, ' ').slice(0, 200),
    }
  })()`)
  console.log('   地址      :', sales.url)
  console.log('   登录界面  :', sales.hasLogin ? '✅' : '❌')
  console.log('   验证码    :', sales.hasCaptcha ? '✅ 有' : '❌ 无')
  console.log('   横向溢出  :', sales.overflow, 'px')
  console.log('   文案      :', sales.text)
  await cdp.shot('final_sales_mobile', OUT)

  // ── AI供应链：完整走一遍 ──
  console.log('\n③ AI供应链（子路径）完整登录 + AI')
  await cdp.send('Page.navigate', { url: SITE + '/demo/supply/' })
  await new Promise((r) => setTimeout(r, 5000))
  await cdp.eval(`localStorage.clear(); true`)
  await cdp.send('Page.reload', { ignoreCache: true })
  await new Promise((r) => setTimeout(r, 5000))
  const login = await cdp.eval(`(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    const ins = [...document.querySelectorAll('input')]
    if (ins.length < 2) return { err: '没有登录表单' }
    setter.call(ins[0], 'admin'); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
    setter.call(ins[1], 'Demo@c8986e'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    const b = document.querySelector('button[type="submit"]')
    if (b) b.click()
    await new Promise(r => setTimeout(r, 8000))
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      ok: !location.pathname.endsWith('/login'),
      customer: (t.match(/客户总数\\s*(\\d+)/) || [])[1] || null,
    }
  })()`)
  console.log('   地址      :', login.path)
  console.log('   登录成功  :', login.ok ? '✅' : '❌ ' + (login.err || ''))
  console.log('   工作台    :', login.customer ? '✅ 客户总数=' + login.customer : '❌')
  await cdp.shot('final_supply_mobile', OUT)
} finally {
  await close()
}
