/** 截实时监控页（★ 另开一页观察用）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9650, windowSize: '1400,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // ① 先在官网问几句（模拟真实访客）
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(800)
  for (const q of ['有哪些资质认证', '最小起订量多少', '你们能便宜点吗']) {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q2')
      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      s.call(i, ${JSON.stringify(q)}); i.dispatchEvent(new Event('input',{bubbles:true}))
      document.querySelector('#go2').click(); return true })()`)
    await wait(15000)
  }
  console.log('  官网那边问完了 3 句')

  // ② 切到实时页（真实使用时是另一个标签页）
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(6000)
  const r = await cdp.eval(`(() => ({
    stats: document.querySelector('#livestats').innerText.replace(/\\s+/g,' '),
    meta: document.querySelector('#livemeta')?.textContent,
    sessions: [...document.querySelectorAll('.sess')].slice(0,4).map(e => ({
      head: e.querySelector('.sh').innerText.replace(/\\s+/g,' ').slice(0,76),
      turns: e.querySelectorAll('.tur').length,
      last: [...e.querySelectorAll('.tur')].slice(-1)[0]?.innerText.replace(/\\s+/g,' ').slice(0,92),
    })),
  }))()`)
  console.log('\n  实时页:')
  console.log('   ', r.meta, '|', r.stats)
  for (const s of r.sessions) {
    console.log('    ●', s.head)
    console.log('      ', s.turns, '轮 |', s.last)
  }
  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1400, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_live.png`, Buffer.from(shot.data, 'base64'))
  console.log('\n  截图 → console_live.png')

  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(5000)
  const m = await cdp.eval(`(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    sessions: document.querySelectorAll('.sess').length,
  }))()`)
  console.log('  手机: 溢出', m.overflow, 'px | 会话', m.sessions, '个')
  const s2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\console_live_mobile.png`, Buffer.from(s2.data, 'base64'))
  console.log('  截图 → console_live_mobile.png')
} finally { await close() }
