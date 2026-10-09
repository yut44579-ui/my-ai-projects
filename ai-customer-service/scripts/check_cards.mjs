/** 看三张卡的状态和链接。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile_cards', port: 9940, windowSize: '1440,1300',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(5500)

  const st = await cdp.eval(`[...document.querySelectorAll('.pcard')].map(c => ({
    badge: (c.querySelector('.badge')||{}).textContent,
    title: (c.querySelector('h3')||{}).textContent,
    status: (c.querySelector('.status')||{}).innerText.replace(/\\s+/g,' '),
    links: [...c.querySelectorAll('a')].map(a => a.textContent.trim()).filter(x => x.includes('打开')),
  }))`)
  console.log('  三张卡片的状态:')
  for (const x of st) console.log('   ', x.badge, '|', x.title, '|', x.status, '|', JSON.stringify(x.links))

  const box = await cdp.eval(`(() => {
    const g = document.querySelector('#pgrid');
    const r = g.getBoundingClientRect();
    return { y: Math.round(r.top + window.scrollY), h: Math.round(r.height) };
  })()`)
  await wait(800)
  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: box.y - 20, width: 1440, height: Math.min(1250, box.h + 40), scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\portfolio_cards_all.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → portfolio_cards_all.png')

  // 手机
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(5000)
  const m = await cdp.eval(`(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    cards: document.querySelectorAll('.pcard').length,
    badges: [...document.querySelectorAll('.pcard .badge')].map(b => b.textContent),
  }))()`)
  console.log('  手机: 溢出', m.overflow, 'px |', m.cards, '张卡 |', JSON.stringify(m.badges))
} finally { await close() }
