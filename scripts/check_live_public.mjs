/**
 * 验证线上页面（public 模式）不再渲染任何指向本机的链接。
 * ★ 这是这次修复的核心断言：访客点不到死链接。
 */
import { launchEdge } from './lib/cdp.mjs'

const URL_ = process.argv[2] ?? 'http://43.134.58.30:8081/'
const NAME = process.argv[3] ?? 'live_check'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9346,
  windowSize: '1440,1100',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Page.navigate', { url: URL_ })
  await new Promise((r) => setTimeout(r, 4000))
  await cdp.send('Page.reload', { ignoreCache: true })
  await new Promise((r) => setTimeout(r, 3500))

  const info = await cdp.eval(`(() => {
    const links = [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
    const t = document.body.innerText || ''
    const statuses = [...document.querySelectorAll('.status .st')].map(s => s.innerText)
    return {
      mode: window.SITE_MODE,
      localLinks: links.filter(h => /127\\.0\\.0\\.1|localhost/.test(h)),
      detailLinks: links.filter(h => h.includes('projects/') && h.endsWith('.html')),
      statuses,
      hasHonestNote: t.includes('在线版不开放实时操作') || t.includes('本机真实运行'),
      cards: (document.getElementById('pgrid') || { children: [] }).children.length,
    }
  })()`)

  console.log('  SITE_MODE          :', info.mode)
  console.log('  指向本机的链接      :', info.localLinks.length ? '❌ ' + info.localLinks.join(', ') : '✅ 无')
  console.log('  项目详情页链接数    :', info.detailLinks.length)
  console.log('  卡片数              :', info.cards)
  console.log('  卡片状态标签        :', info.statuses.join(' | '))
  console.log('  含说明性文案        :', info.hasHonestNote)

  await cdp.shot(NAME, 'D:\\biz-assistant-int\\docs\\screenshots')
} finally {
  await close()
}
