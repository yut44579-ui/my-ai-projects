/**
 * 清掉 localStorage 后重验登录页 + 登录流程 + AI。
 * ★ 上一版漏了这步：之前注入的令牌还在，访问 /login 会被自动跳转到 /，
 *   于是在"过渡中"截图，页面是空的 —— 误判成"登录页没渲染"。
 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = process.argv[2] ?? 'http://127.0.0.1:18082'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9361,
  windowSize: '375,812',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375, height: 812, deviceScaleFactor: 2, mobile: true,
  })

  // 先到同源页面，再清 localStorage
  await cdp.send('Page.navigate', { url: BASE + '/login' })
  await new Promise((r) => setTimeout(r, 3000))
  await cdp.eval(`localStorage.clear(); sessionStorage.clear(); true`)
  await cdp.send('Page.reload', { ignoreCache: true })
  await new Promise((r) => setTimeout(r, 4000))

  const login = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      len: t.length,
      hasDemo: t.includes('演示账号'),
      hasAccount: t.includes('Demo@c8986e'),
      hasRate: t.includes('限流'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      text: t.replace(/\\s+/g, ' ').slice(0, 300),
    }
  })()`)
  console.log('① 登录页（已清缓存）')
  console.log('   地址        :', login.path, ' 文本长度', login.len)
  console.log('   演示账号提示:', login.hasDemo ? '✅' : '❌')
  console.log('   账号密码    :', login.hasAccount ? '✅' : '❌')
  console.log('   限流说明    :', login.hasRate ? '✅' : '❌')
  console.log('   横向溢出    :', login.overflow, 'px')
  console.log('   文案        :', login.text)
  await cdp.shot('live_login_final', OUT)

  // 登录
  await cdp.eval(`(() => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    const ins = [...document.querySelectorAll('input')]
    setter.call(ins[0], 'admin'); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
    setter.call(ins[1], 'Demo@c8986e'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    // ★ 不要按文字找按钮：AntD 会在两个汉字之间插空格，
    //   按钮的 innerText 是「登 录」而不是「登录」—— 按文字找会找不到。
    //   直接找表单的提交按钮。
    const btn = document.querySelector('button[type="submit"]') ||
      [...document.querySelectorAll('button')].find(b => /登\\s*录/.test(b.innerText))
    if (btn) btn.click()
    return !!btn
  })()`)
  await new Promise((r) => setTimeout(r, 7000))

  const home = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      hasCustomer: t.includes('客户总数'),
      number: (t.match(/客户总数\\s*(\\d+)/) || [])[1] || null,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      text: t.replace(/\\s+/g, ' ').slice(0, 200),
    }
  })()`)
  console.log('\n② 登录后（手机视口）')
  console.log('   地址    :', home.path)
  console.log('   工作台  :', home.hasCustomer ? '✅ 客户总数=' + home.number : '❌')
  console.log('   溢出    :', home.overflow, 'px')
  console.log('   文案    :', home.text)
  await cdp.shot('live_home_final', OUT)

  // AI
  await cdp.send('Page.navigate', { url: BASE + '/ai-assistant' })
  await new Promise((r) => setTimeout(r, 3500))
  const before = await cdp.eval(`document.body.innerText.length`)
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
  await new Promise((r) => setTimeout(r, 16000))
  const ai = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    const m = t.match(/(今天有[^。]{0,60}。|有 \\d+ [^。]{0,60}。)/)
    return { grew: t.length > ${before}, answer: m ? m[0] : '(没抓到成句回答)', tail: t.slice(-400).replace(/\\s+/g,' ') }
  })()`)
  console.log('\n③ AI 问答（线上真调模型）')
  console.log('   内容有增长:', ai.grew ? '✅' : '❌')
  console.log('   回答      :', ai.answer)
  console.log('   尾部      :', ai.tail.slice(0, 260))
  await cdp.shot('live_ai_final', OUT)
} finally {
  await close()
}
