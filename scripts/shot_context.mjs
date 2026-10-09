/** 截人工工作台的完整会话记录。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9610, windowSize: '1320,950',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // 先造一个多轮会话（这样时间线才好看）
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3000)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(800)
  for (const q of ['最小起订量多少', '你们厂在哪儿', '能不能开专票']) {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q2')
      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      s.call(i, ${JSON.stringify(q)}); i.dispatchEvent(new Event('input',{bubbles:true}))
      document.querySelector('#go2').click(); return true })()`)
    await wait(15000)
  }

  // 打开控制台的人工页
  await cdp.send('Page.navigate', { url: BASE + '/#ho' })
  await wait(4500)
  // 选第一条
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return true })()`)
  await wait(3500)

  const r = await cdp.eval(`(() => ({
    badge: (document.querySelector('#navBadge')||{}).textContent,
    open: document.querySelector('#hoCount').textContent,
    tlRows: [...document.querySelectorAll('.tl-row')].map(e => e.innerText.replace(/\\s+/g,' ').slice(0,72)),
    notes: [...document.querySelectorAll('.tl-note')].map(e => e.innerText.replace(/\\s+/g,' ').slice(0,50)),
    hdr: (document.querySelector('.hohdr')||{innerText:''}).innerText.replace(/\\s+/g,' '),
  }))()`)
  console.log('  角标:', r.badge, '| 待处理:', r.open)
  console.log('  头部:', r.hdr)
  console.log('  会话记录:')
  for (const x of r.tlRows) console.log('    ', x)
  for (const x of r.notes) console.log('     ——', x)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1320, height: 950, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\handover_context.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → handover_context.png')

  // 手机
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/#ho' })
  await wait(4500)
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return true })()`)
  await wait(3000)
  const m = await cdp.eval(`(() => ({
    badge: (document.querySelector('#navBadge')||{}).textContent,
    rows: document.querySelectorAll('.tl-row').length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('  手机: 角标', m.badge, '| 会话行数', m.rows, '| 溢出', m.overflow, 'px')
  const s2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\handover_mobile.png`, Buffer.from(s2.data, 'base64'))
  console.log('  截图 → handover_mobile.png')
} finally { await close() }
