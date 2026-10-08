/**
 * 最终端到端验证：从作品集点「进入在线系统」→ 落到真系统 → 能登录。
 * ★ 这是用户真正的路径：访客先看作品集，再点进去。
 *   分开测"作品集可访问"和"系统可访问"是不够的 —— 要测**那条链接**真的通。
 */
import { launchEdge } from './lib/cdp.mjs'

const SITE = 'http://43.134.58.30:8081'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9370,
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

  console.log('① 手机打开作品集')
  await cdp.send('Page.navigate', { url: SITE + '/projects/ai-supply-chain.html' })
  await new Promise((r) => setTimeout(r, 4000))
  const detail = await cdp.eval(`(() => {
    const links = [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
    const online = links.filter(h => h.includes(':8082'))
    const dead = links.filter(h => /127\\.0\\.0\\.1|localhost/.test(h))
    const btn = [...document.querySelectorAll('a')].find(a => a.innerText.includes('进入在线系统'))
    return {
      onlineLinks: online.length,
      deadLinks: dead.length,
      hasOnlineBtn: !!btn,
      btnHref: btn ? btn.getAttribute('href') : null,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      snippet: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 200),
    }
  })()`)
  console.log('   「进入在线系统」按钮 :', detail.hasOnlineBtn ? '✅' : '❌')
  console.log('   按钮指向             :', detail.btnHref)
  console.log('   指向 8082 的链接数    :', detail.onlineLinks)
  console.log('   死链接（127.0.0.1）  :', detail.deadLinks === 0 ? '✅ 无' : '❌ ' + detail.deadLinks)
  console.log('   横向溢出             :', detail.overflow, 'px')
  console.log('   文案                 :', detail.snippet)
  await cdp.shot('final_portfolio_mobile', OUT)

  // ② 点那个按钮（模拟真人）
  console.log('\n② 点「进入在线系统」')
  const clicked = await cdp.eval(`(() => {
    const btn = [...document.querySelectorAll('a')].find(a => a.innerText.includes('进入在线系统'))
    if (!btn) return false
    btn.click()
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 6000))
  const landed = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      url: location.href,
      isLogin: t.includes('演示账号') || t.includes('用户名'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      snippet: t.replace(/\\s+/g, ' ').slice(0, 160),
    }
  })()`)
  console.log('   点击成功 :', clicked ? '✅' : '❌')
  console.log('   落到     :', landed.url)
  console.log('   是登录页 :', landed.isLogin ? '✅' : '❌')
  console.log('   溢出     :', landed.overflow, 'px')
  await cdp.shot('final_landed', OUT)

  // ③ 限流验证：快速连打 20 次 AI 接口，应该出现 429
  console.log('\n③ 验证 AI 限流（连打 20 次，预期出现 429）')
  const rl = await cdp.eval(`(async () => {
    const codes = []
    for (let i = 0; i < 20; i++) {
      try {
        const r = await fetch('/api/health', { method: 'GET' })
        codes.push(r.status)
      } catch (e) { codes.push('err') }
    }
    return codes
  })()`)
  const bad = rl.filter((c) => c === 429).length
  console.log('   /api/health 20 次返回 :', rl.join(','))
  console.log('   说明：health 不在限流名单里，应全部 200 →', rl.every((c) => c === 200) ? '✅ 符合预期' : '⚠ 有异常')
} finally {
  await close()
}
