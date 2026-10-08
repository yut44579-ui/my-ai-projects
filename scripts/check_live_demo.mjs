/**
 * 线上产物最终验证（走 SSH 隧道，等价于公网访客）：
 *   ① 登录页有没有把演示账号写出来（我在作品集上承诺过）
 *   ② 手机视口下能不能真的登进去并看到数据
 *   ③ AI 问答在线上是不是真的能用
 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://127.0.0.1:18082'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9360,
  windowSize: '375,812',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375,
    height: 812,
    deviceScaleFactor: 2,
    mobile: true,
  })
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })

  // ① 登录页
  await cdp.send('Page.navigate', { url: BASE + '/login' })
  await new Promise((r) => setTimeout(r, 3500))
  const loginInfo = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      hasDemo: t.includes('演示账号'),
      hasAccount: t.includes('Demo@c8986e'),
      hasRate: t.includes('限流') || t.includes('12 次'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      snippet: t.replace(/\\s+/g,' ').slice(0, 200),
    }
  })()`)
  console.log('① 登录页')
  console.log('   写了「演示账号」:', loginInfo.hasDemo ? '✅' : '❌')
  console.log('   写出了账号密码  :', loginInfo.hasAccount ? '✅' : '❌')
  console.log('   说明了限流      :', loginInfo.hasRate ? '✅' : '❌')
  console.log('   横向溢出        :', loginInfo.overflow, 'px')
  console.log('   文案            :', loginInfo.snippet)
  await cdp.shot('live_mobile_login', OUT)

  // ② 真的登录进去
  await cdp.eval(`(() => {
    const set = (el, v) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(el, v)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }
    const inputs = [...document.querySelectorAll('input')]
    if (inputs[0]) set(inputs[0], 'admin')
    if (inputs[1]) set(inputs[1], 'Demo@c8986e')
    const btn = [...document.querySelectorAll('button')].find(b => b.innerText.includes('登录'))
    if (btn) btn.click()
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 5000))
  const home = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      loggedIn: !location.pathname.includes('/login'),
      hasCustomer: /客户总数/.test(t),
      number: (t.match(/客户总数\\s*(\\d+)/) || [])[1] || null,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      snippet: t.replace(/\\s+/g,' ').slice(0, 180),
    }
  })()`)
  console.log('\n② 登录后（手机视口）')
  console.log('   地址            :', home.path)
  console.log('   登录成功        :', home.loggedIn ? '✅' : '❌')
  console.log('   看到工作台数据  :', home.hasCustomer ? `✅ 客户总数=${home.number}` : '❌')
  console.log('   横向溢出        :', home.overflow, 'px')
  await cdp.shot('live_mobile_home', OUT)

  // ③ AI 问答
  await cdp.send('Page.navigate', { url: BASE + '/ai-assistant' })
  await new Promise((r) => setTimeout(r, 3500))
  const ai = await cdp.eval(`(async () => {
    const input = document.querySelector('input[placeholder*="问题"]') || document.querySelector('input')
    if (!input) return { err: '没有输入框' }
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, '今天有哪些客户需要我处理？')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise(r => setTimeout(r, 300))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }))
    await new Promise(r => setTimeout(r, 12000))
    const t = document.body.innerText || ''
    return {
      text: t.replace(/\\s+/g,' ').slice(0, 500),
      hasAnswer: /客户|需要|处理/.test(t),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })()`)
  console.log('\n③ AI 问答（线上真实调用模型）')
  console.log('   页面文本:', ai.text?.slice(0, 260) ?? ai.err)
  console.log('   横向溢出:', ai.overflow, 'px')
  await cdp.shot('live_mobile_ai', OUT)
} finally {
  await close()
}
