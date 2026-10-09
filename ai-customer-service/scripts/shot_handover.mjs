/** 给「人工」页截个图，并清掉队列里的测试垃圾。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9600, windowSize: '1280,900',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // 先造几条"像样"的转人工：用演示官网的挂件问几句答不了的
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3000)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(800)
  const qs = ['能不能开专票', '你们厂在哪儿']
  for (const q of qs) {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q2')
      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      s.call(i, ${JSON.stringify(q)}); i.dispatchEvent(new Event('input',{bubbles:true}))
      document.querySelector('#go2').click(); return true })()`)
    await wait(14000)
  }

  // 打开控制台的人工页
  await cdp.send('Page.navigate', { url: BASE + '/#ho' })
  await wait(4000)
  const r = await cdp.eval(`(() => {
    const b = document.querySelector('#navBadge')
    const bt = document.querySelector('#nav button[data-k=ho]')
    return {
      tabText: bt ? bt.innerText.replace(/\\s+/g, '') : '无',
      badge: b ? b.textContent : '无',
      badgeHidden: b ? b.classList.contains('zero') : null,
      open: document.querySelector('#hoCount').textContent,
      firstItems: [...document.querySelectorAll('.hoitem')].slice(0,5).map(e=>e.innerText.replace(/\\s+/g,' ')),
      onHoPage: document.querySelector('#p-ho').classList.contains('on'),
      detail: document.querySelector('#hoDetail').innerText.replace(/\\s+/g,' ').slice(0,150),
    }
  })()`)
  console.log('  「人工」标签文字 :', r.tabText, '（角标', r.badge, r.badgeHidden ? '隐藏' : '显示', '）')
  console.log('  待处理           :', r.open)
  console.log('  已经切到人工页   :', r.onHoPage ? '是' : '否')
  console.log('  队列前 5 条:')
  for (const x of r.firstItems) console.log('    ', x)
  console.log('  详情区:', r.detail)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1280, height: 900, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_handover.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → console_handover.png')

  // 手机上也看一眼
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/#ho' })
  await wait(3500)
  const m = await cdp.eval(`(() => {
    const b = document.querySelector('#navBadge')
    return { badge: b ? b.textContent : '无', vis: b ? !b.classList.contains('zero') : false,
             overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth }
  })()`)
  console.log('  手机上角标:', m.badge, m.vis ? '可见' : '隐藏', '| 溢出', m.overflow, 'px')
  const s2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\console_handover_mobile.png`, Buffer.from(s2.data, 'base64'))
  console.log('  截图 → console_handover_mobile.png')
} finally { await close() }
