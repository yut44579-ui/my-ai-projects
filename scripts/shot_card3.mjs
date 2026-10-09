/** 只看第三个项目卡片。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile_card', port: 9910, windowSize: '1440,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(5000)
  const info = await cdp.eval(`(() => {
    const cards = [...document.querySelectorAll('.pcard')]
    const c = cards[2]
    if (!c) return { err: '没有第三张卡' }
    const b = c.querySelector('.badge')
    const r = c.getBoundingClientRect()
    return {
      badgeText: b ? b.textContent : '?',
      badgeBox: b ? { w: Math.round(b.getBoundingClientRect().width), h: Math.round(b.getBoundingClientRect().height) } : null,
      badgeWrapped: b ? b.getBoundingClientRect().height > 46 : null,
      title: (c.querySelector('h3') || {}).textContent,
      y: Math.round(r.top + window.scrollY),
      h: Math.round(r.height),
      allBadges: cards.map(x => (x.querySelector('.badge') || {}).textContent),
    }
  })()`)
  console.log('  三张卡的 badge:', info.allBadges)
  console.log('  第三张:', info.title)
  console.log('  badge 文字:', info.badgeText, '尺寸:', JSON.stringify(info.badgeBox),
              info.badgeWrapped ? '❌ 撑破了（换行）' : '✅ 没撑破')

  await cdp.eval(`window.scrollTo(0, ${Math.max(0, info.y - 90)})`)
  await wait(1500)
  const r = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: Math.max(0, info.y - 90), width: 1440, height: Math.min(1000, info.h + 160), scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\portfolio_card3.png`, Buffer.from(r.data, 'base64'))
  console.log('  截图 → portfolio_card3.png')
} finally { await close() }
