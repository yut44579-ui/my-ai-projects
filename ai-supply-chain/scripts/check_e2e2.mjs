/**
 * 修正版端到端验证。
 * ★ 上一版两个错都在测试本身：
 *   ① <a target="_blank"> CDP 不会跟随，所以"点完还在原页"是正常的 ——
 *      要验证链接目标，应该直接导航到该 href。
 *   ② 限流测试用了相对路径 /api/health，打到了作品集 origin(:8081)，
 *      那里没有这个接口 → 20 个 404，根本没测到限流。
 */
import { launchEdge } from './lib/cdp.mjs'

const SITE = 'http://43.134.58.30:8081'
const APP = 'http://43.134.58.30:8082'
const OUT = 'D:\\biz-assistant-int\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9371,
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

  console.log('① 作品集详情页：按钮指向对不对')
  await cdp.send('Page.navigate', { url: SITE + '/projects/ai-supply-chain.html' })
  await new Promise((r) => setTimeout(r, 3500))
  const d = await cdp.eval(`(() => {
    const btn = [...document.querySelectorAll('a')].find(a => a.innerText.includes('进入在线系统'))
    const links = [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
    return {
      href: btn ? btn.getAttribute('href') : null,
      target: btn ? btn.getAttribute('target') : null,
      to8082: links.filter(h => h.includes(':8082')).length,
      dead: links.filter(h => /127\\.0\\.0\\.1|localhost/.test(h)).length,
    }
  })()`)
  console.log('   按钮 href  :', d.href)
  console.log('   打开方式    :', d.target, '（新标签页打开，符合外链习惯）')
  console.log('   指向 8082 的链接:', d.to8082, '个')
  console.log('   死链接      :', d.dead === 0 ? '✅ 无' : '❌ ' + d.dead)

  console.log('\n② 直接打开按钮的目标地址（这才是访客真实看到的）')
  await cdp.send('Page.navigate', { url: APP + '/' })
  await new Promise((r) => setTimeout(r, 5000))
  const app = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      url: location.href,
      hasBrand: t.includes('AI供应链'),
      hasDemo: t.includes('演示账号'),
      hasAccount: t.includes('Demo@c8986e'),
      isLoginForm: !!document.querySelector('input[type="password"]'),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })()`)
  console.log('   地址        :', app.url)
  console.log('   品牌        :', app.hasBrand ? '✅' : '❌')
  console.log('   登录表单    :', app.isLoginForm ? '✅' : '❌')
  console.log('   演示账号提示:', app.hasAccount ? '✅' : '❌')
  console.log('   横向溢出    :', app.overflow, 'px')
  await cdp.shot('final_app_mobile', OUT)

  console.log('\n③ 限流验证（对 8082 的 AI 接口连打 20 次）')
  await cdp.send('Page.navigate', { url: APP + '/login' })
  await new Promise((r) => setTimeout(r, 3000))
  const rl = await cdp.eval(`(async () => {
    const codes = []
    for (let i = 0; i < 20; i++) {
      try {
        const r = await fetch('${APP}/api/dashboard/ai-ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: '限流探测 ' + i }),
        })
        codes.push(r.status)
      } catch (e) { codes.push('err') }
    }
    return codes
  })()`)
  const n429 = rl.filter((c) => c === 429).length
  console.log('   返回码分布 :', JSON.stringify(rl.reduce((a, c) => (a[c] = (a[c] || 0) + 1, a), {})))
  console.log('   429 次数   :', n429, n429 > 0 ? '✅ 限流生效（脚本刷不动）' : '⚠ 没触发限流')
} finally {
  await close()
}
