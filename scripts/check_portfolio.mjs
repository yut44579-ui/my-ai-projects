/** 验证作品集上第三个项目。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile_fresh', port: 9900, windowSize: '1440,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (x) => errs.push('EXC ' + (x.exceptionDetails?.exception?.description || '').slice(0, 180)))

  // ── 首页 ──
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(5000)
  const idx = await cdp.eval(`(() => {
    const cards = [...document.querySelectorAll('.pcard')]
    return {
      title: document.querySelector('h2.title') ? document.querySelector('h2.title').textContent : '?',
      count: cards.length,
      cards: cards.map(c => {
        const t = c.querySelector('h3') || c.querySelector('.t') || c.querySelector('b')
        return (t ? t.textContent.trim() : '?')
      }),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })()`)
  console.log('  首页标题:', idx.title)
  console.log('  项目卡片数:', idx.count)
  for (const c of idx.cards) console.log('    ·', c)
  console.log('  横向溢出:', idx.overflow, 'px')
  console.log('  JS 错误:', errs.length ? errs.slice(0, 2) : '（无）')

  // 滚到项目区截图
  await cdp.eval(`(() => { const s=document.querySelector('#projects'); if(s) s.scrollIntoView(); return 1 })()`)
  await wait(2500)
  const shot1 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: await cdp.eval(`Math.round(document.querySelector('#projects').getBoundingClientRect().top + window.scrollY)`), width: 1440, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\portfolio_index.png`, Buffer.from(shot1.data, 'base64'))
  console.log('  截图 → portfolio_index.png')

  // ── 详情页 ──
  await cdp.send('Page.navigate', { url: BASE + '/projects/ai-customer-service.html' })
  await wait(5000)
  const det = await cdp.eval(`(() => ({
    title: (document.querySelector('#dTitle')||{}).textContent,
    sub: ((document.querySelector('#dSub')||{}).textContent||'').slice(0,60),
    tags: document.querySelectorAll('#dTags span').length,
    shots: document.querySelectorAll('#dShots .shot').length,
    metrics: document.querySelectorAll('#dMetrics .metric, #dMetrics > div').length,
    tradeoffs: document.querySelectorAll('#dTradeoffs li').length,
    highlights: document.querySelectorAll('#dHighlights li').length,
    limits: document.querySelectorAll('#dLimits li').length,
    replay: ((document.querySelector('#dReplay')||{}).innerText||'').replace(/\\s+/g,' ').slice(0,80),
    demoLinks: document.querySelectorAll('#dDemo a').length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('\n  详情页:')
  console.log('    标题    :', det.title)
  console.log('    副标题  :', det.sub)
  console.log('    标签', det.tags, '| 截图', det.shots, '| 指标', det.metrics,
              '| 取舍', det.tradeoffs, '| 硬的地方', det.highlights, '| 局限', det.limits)
  console.log('    演示入口:', det.demoLinks, '个')
  console.log('    回放    :', det.replay)
  console.log('    溢出    :', det.overflow, 'px')

  await cdp.eval(`window.scrollTo(0, 0)`)
  await wait(1200)
  const shot2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1440, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\portfolio_detail.png`, Buffer.from(shot2.data, 'base64'))
  console.log('  截图 → portfolio_detail.png')

  // ── 手机 ──
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(5000)
  const m = await cdp.eval(`(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    cards: document.querySelectorAll('.pcard').length,
  }))()`)
  console.log('\n  手机: 溢出', m.overflow, 'px | 卡片', m.cards, '个')
} finally { await close() }
